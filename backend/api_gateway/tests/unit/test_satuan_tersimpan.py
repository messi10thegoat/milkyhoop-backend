"""BUG-014 (5 Nov 2026): satuan baru tersimpan + SATU sumber satuan (halaman Satuan /api/items/units = form barang /api/units/dropdown).
Dulu POST /api/items/units membalas 200 tanpa menulis apa pun dan GET-nya tak membaca product_units -> "Jerigen" hilang sunyi."""
import asyncio
import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import bank_accounts as RB
from app.routers import items as RI
from app.routers import units as RU
from app.schemas.bank_accounts import BankAccountListResponse
from app.schemas.items import DEFAULT_UNITS, CreateUnitRequest
from app.services import satuan_master as SM


class _Db:
    """product_units tiruan dengan indeks unik (tenant_id, lower(abbreviation)) + nama turunan produk/konversi."""

    def __init__(self, units=(), terpakai=()):
        self.units = [dict(u) for u in units]
        self.terpakai = list(terpakai)
        self.sql = []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        t = a[0]
        pu = [{"id": u["id"], "name": u["name"], "abbreviation": u["abbreviation"], "is_system": u.get("is_system", False)}
              for u in self.units if u["tenant_id"] == t and u.get("is_active", True)]
        return pu + [{"id": None, "name": x, "abbreviation": x, "is_system": False} for tt, x in self.terpakai if tt == t]

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        assert "INSERT INTO product_units" in sql and "ON CONFLICT (tenant_id, lower(abbreviation)) DO NOTHING" in sql
        t, nama, singkatan = a
        if any(u["tenant_id"] == t and u["abbreviation"].lower() == singkatan.lower() for u in self.units):
            return None
        uid = f"u{len(self.units) + 1}"
        self.units.append({"id": uid, "tenant_id": t, "name": nama, "abbreviation": singkatan})
        return uid

    async def close(self):
        pass


class _Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class _A:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *e):
                return False
        return _A()


def _req(tenant="t1", body=None):
    async def json():
        return body or {}
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tenant, "user_id": "00000000-0000-0000-0000-000000000001"}), json=json)


@pytest.fixture
def db(monkeypatch):
    d = _Db(units=[{"id": "x1", "tenant_id": "t2", "name": "Jerigen", "abbreviation": "Jerigen"}])  # tenant LAIN

    async def conn():
        return d

    async def pool():
        return _Pool(d)
    monkeypatch.setattr(RI, "get_db_connection", conn)
    monkeypatch.setattr(RU, "get_pool", pool)
    return d


def _halaman(t="t1"):
    r = asyncio.run(RI.list_units(_req(t)))
    return r.default_units, r.custom_units


def _dropdown(t="t1", search=None):
    return asyncio.run(RU.units_dropdown(_req(t), search))["items"]


def test_tambah_satuan_halaman_satuan_tersimpan_dan_muncul_sesudah_muat_ulang(db):
    """Langkah tester BUG-014: Singkatan "Jerigen", nama "Jerigen 18 liter" -> POST -> GET -> ada (dulu tidak)."""
    assert "Jerigen" not in _halaman()[1]
    r = asyncio.run(RI.create_unit(_req(), CreateUnitRequest(name="Jerigen 18 liter", abbreviation="Jerigen")))
    assert r.success and r.unit == "Jerigen"
    assert [(u["tenant_id"], u["name"], u["abbreviation"]) for u in db.units if u["tenant_id"] == "t1"] == [("t1", "Jerigen 18 liter", "Jerigen")]
    assert _halaman()[1] == ["Jerigen"]
    assert [u["abbreviation"] for u in _dropdown() if u["abbreviation"] == "Jerigen"] == ["Jerigen"]  # SATU sumber: form barang ikut


def test_tanpa_singkatan_pakai_nama_dan_teks_tidak_dipaksa_huruf_kecil(db):
    asyncio.run(RI.create_unit(_req(), CreateUnitRequest(name="Bal")))
    asyncio.run(RU.create_unit(_req(body={"name": "Sak Besar", "abbreviation": "Sak"})))
    assert {(u["name"], u["abbreviation"]) for u in db.units if u["tenant_id"] == "t1"} == {("Bal", "Bal"), ("Sak Besar", "Sak")}


@pytest.mark.parametrize("nama, singkatan", [("jerigen", None), ("JERIGEN", "jerigen"), ("Pieces", "pcs"), ("Kilo", "KG")])
def test_bentrok_tanpa_beda_huruf_400_berkalimat_bukan_sukses_sunyi(db, nama, singkatan):
    asyncio.run(RI.create_unit(_req(), CreateUnitRequest(name="Jerigen")))
    n = len(db.units)
    with pytest.raises(HTTPException) as e:
        asyncio.run(RI.create_unit(_req(), CreateUnitRequest(name=nama, abbreviation=singkatan)))
    assert e.value.status_code == 400 and e.value.code == "SATUAN_SUDAH_ADA" and "sudah ada" in e.value.detail
    assert len(db.units) == n


def test_form_barang_post_api_units_juga_menolak_bentrok_dan_kosong(db):
    asyncio.run(RU.create_unit(_req(body={"name": "Galon", "abbreviation": "galon"})))
    for body in ({"name": "galon", "abbreviation": "Galon"}, {"name": "", "abbreviation": "x"}, {"name": "A" * 51}):
        with pytest.raises(HTTPException) as e:
            asyncio.run(RU.create_unit(_req(body=body)))
        assert e.value.status_code == 400


def test_satuan_bawaan_ketemu_di_dropdown_tanpa_dibuat_ulang(db):
    """Tester: "Pcs" bawaan tak ditemukan di form barang (muncul "+ Buat satuan") karena dropdown hanya product_units."""
    d = _dropdown()
    assert [u["abbreviation"] for u in d[: len(DEFAULT_UNITS)]] == DEFAULT_UNITS
    assert {u["id"] for u in d[: len(DEFAULT_UNITS)]} == {SM.ID_BAWAAN + u for u in DEFAULT_UNITS}
    assert [u["abbreviation"] for u in _dropdown(search="pc")] == ["Pcs"]
    assert db.units == [u for u in db.units if u["tenant_id"] == "t2"]  # membaca tidak menulis


def test_dedupe_tanpa_beda_huruf_baris_tenant_menang(db):
    """Data lama ("pcs"/"galon" huruf kecil) tidak diubah; tampil sekali. Nama turunan produk ("Bal") ikut, tanpa dobel."""
    db.units += [{"id": "p1", "tenant_id": "t1", "name": "Pcs", "abbreviation": "pcs"},
                 {"id": "p2", "tenant_id": "t1", "name": "galon", "abbreviation": "galon"}]
    db.terpakai += [("t1", "PCS"), ("t1", "Bal"), ("t1", "bal"), ("t1", "Galon")]
    d = _dropdown()
    assert [u for u in d if SM.kunci(u["abbreviation"]) == "pcs"] == [{"id": "p1", "name": "Pcs", "abbreviation": "pcs"}]
    assert [u["id"] for u in d if SM.kunci(u["abbreviation"]) == "galon"] == ["p2"]
    assert [u["id"] for u in d if SM.kunci(u["abbreviation"]) == "bal"] == [SM.ID_TERPAKAI + "Bal"]
    bawaan, kustom = _halaman()
    assert bawaan == DEFAULT_UNITS and sorted(kustom, key=str.lower) == ["Bal", "galon"]


def test_satu_sumber_halaman_satuan_sama_dengan_dropdown(db):
    db.units += [{"id": "p9", "tenant_id": "t1", "name": "Liter", "abbreviation": "ltr", "is_system": True}]  # hasil /seed lama
    db.terpakai += [("t1", "Ikat")]
    bawaan, kustom = _halaman()
    assert {SM.kunci(x) for x in bawaan + kustom} == {SM.kunci(u["abbreviation"]) for u in _dropdown()}
    assert "ltr" in kustom


def test_sql_daftar_berpagar_tenant_dan_aktif_tiap_cabang():
    sql = inspect.getsource(SM.daftar)
    assert sql.count("WHERE tenant_id = $1") == 4 and "COALESCE(is_active, true)" in sql


def test_kedua_rute_post_memakai_satu_penulis():
    for fn in (RI.create_unit, RU.create_unit):
        src = inspect.getsource(fn)
        assert "SM.buat(" in src and "INSERT" not in src and "siap digunakan" not in src
    for fn in (RI.list_units, RU.units_dropdown):
        assert "SM.daftar(" in inspect.getsource(fn)


# ---------------------------------------------------------------- BUG-015
def test_daftar_kas_bank_mengembalikan_mata_uang(monkeypatch):
    """BUG-015: GET /api/bank-accounts tak men-SELECT ba.currency -> kolom Mata uang kosong (detail & dropdown sudah benar)."""
    baris = {"id": "b1", "account_name": "BCA", "account_number": "1", "bank_name": "BCA", "account_holder_name": None,
             "account_type": "bank", "currency": "USD", "coa_id": "c1", "coa_code": "1-10100", "coa_name": "Bank",
             "ledger_balance": 0, "is_active": True, "is_default": False, "created_at": datetime(2026, 11, 5)}

    class _C:
        async def fetchval(self, sql, *a):
            return 1

        async def fetch(self, sql, *a):
            assert "ba.currency" in sql
            return [baris]

    async def pool():
        return _Pool(_C())
    monkeypatch.setattr(RB, "get_pool", pool)
    out = asyncio.run(RB.list_bank_accounts(_req(), None, None, None, 0, 20, "account_name", "asc"))
    assert out["items"][0]["currency"] == "USD"
    assert BankAccountListResponse(**out).items[0].currency == "USD"  # response_model tak membuang kuncinya


# ---------------------------------------------------------------- lewat RUTE (temuan WORKSPACE: tes fungsi tak menjangkau response_model/urutan rute)
def _klien(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    async def pool():
        return _Pool(db)
    monkeypatch.setattr(RB, "get_pool", pool)
    app = FastAPI()

    @app.middleware("http")
    async def _masuk(request, call_next):
        request.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    app.include_router(RI.router, prefix="/api")
    app.include_router(RU.router, prefix="/api/units")
    app.include_router(RB.router, prefix="/api/bank-accounts")
    return TestClient(app)


def test_rute_halaman_satuan_post_lalu_get_dan_dropdown_form_barang(db, monkeypatch):
    k = _klien(db, monkeypatch)
    g0 = k.get("/api/items/units")
    assert g0.status_code == 200 and g0.json()["default_units"] == DEFAULT_UNITS and g0.json()["custom_units"] == []
    p = k.post("/api/items/units", json={"name": "Jerigen 18 liter", "abbreviation": "Jerigen"})
    assert p.status_code == 200 and p.json()["unit"] == "Jerigen"
    g = k.get("/api/items/units")
    assert g.status_code == 200 and g.json()["custom_units"] == ["Jerigen"]
    d = k.get("/api/units/dropdown").json()["items"]
    assert {"Pcs", "Jerigen"} <= {u["abbreviation"] for u in d}
    dup = k.post("/api/units", json={"name": "jerigen", "abbreviation": "jerigen"})
    assert dup.status_code == 400 and "sudah ada" in dup.text
    assert k.post("/api/items/units", json={"name": "PCS"}).status_code == 400


def test_rute_daftar_kas_bank_ada_mata_uang(monkeypatch):
    baris = {"id": "b1", "account_name": "BCA", "account_number": "1", "bank_name": "BCA", "account_holder_name": None,
             "account_type": "bank", "currency": "USD", "coa_id": "c1", "coa_code": "1-10100", "coa_name": "Bank",
             "ledger_balance": 0, "is_active": True, "is_default": False, "created_at": datetime(2026, 11, 5)}

    class _C:
        async def fetchval(self, sql, *a):
            return 1

        async def fetch(self, sql, *a):
            return [baris]
    r = _klien(_C(), monkeypatch).get("/api/bank-accounts")
    assert r.status_code == 200 and r.json()["items"][0]["currency"] == "USD"
