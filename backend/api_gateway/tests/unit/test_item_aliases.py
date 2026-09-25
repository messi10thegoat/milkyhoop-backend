"""Alias item per tenant — GET/PUT /api/items/aliases (W3/U4, Q-009, 25 Sep 2026).

Kontrak (disetujui MASTER): PUT 1-50 pasangan semua-atau-tidak; 400 {code,message,index};
normalisasi lower+trim+spasi tunggal ('|' dipertahankan); item asing/terhapus-lunak = 400
dengan pesan sama; GET menyembunyikan alias yang itemnya terhapus-lunak.
Gerbang rute: router alias di-include SEBELUM items.router (kalau tidak, "aliases" = item_id).
Gerbang izin: GET item R, PUT sales_order C — bukan pola /api/items/{id}.
"""
import ast
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.routing import Match

from app.routers import item_aliases as IA
from app.routers import items as ITEMS

TENANT = "kaos-biru-konveksi"
USER = str(uuid.uuid4())
ITEM_A = uuid.uuid4()
ITEM_B = uuid.uuid4()
ITEM_ASING = uuid.uuid4()        # milik tenant lain / terhapus-lunak: DB tak mengembalikannya
APP = Path(IA.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations" / "V305__item_aliases.sql"


class DB:
    def __init__(self, milik=(ITEM_A, ITEM_B)):
        self.milik = set(milik)
        self.sql = []
        self.args = []
        self.insert = None

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        self.args.append(a)
        if "FROM products" in sql:
            assert a[0] == TENANT
            return [{"id": i} for i in a[1] if i in self.milik]
        if "INSERT INTO item_aliases" in sql:
            self.insert = a
            return [{"teks": t, "item_id": i} for t, i in zip(a[1], a[2])]
        if "FROM item_aliases" in sql:
            return [{"teks": "combed 50", "item_id": ITEM_A}]
        raise AssertionError(sql)

    def transaction(self):
        class T:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *e):
                return False
        return T()


class Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class A:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *e):
                return False
        return A()


@pytest.fixture
def pasang(monkeypatch):
    def _p(db):
        async def _pool():
            return Pool(db)
        monkeypatch.setattr(IA, "get_pool", _pool)
        return db
    return _p


def _req(badan=None):
    async def _json():
        if isinstance(badan, Exception):
            raise badan
        return badan
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": USER}),
                           headers={}, json=_json)


def _kode(exc):
    return exc.value.status_code, exc.value.detail["code"], exc.value.detail.get("index")


# ---------- normalisasi & validasi (tanpa DB) ----------

@pytest.mark.parametrize("masuk,harap", [
    ("  Combed   50 ", "combed 50"),
    ("KAOS Polos Hitam|XL", "kaos polos hitam|xl"),
    ("kaos\tpolos\n hitam", "kaos polos hitam"),
    ("a", "a"),
])
def test_normalisasi(masuk, harap):
    assert IA.normalisasi_teks(masuk) == harap


@pytest.mark.parametrize("badan,kode,index", [
    (None, "ALIAS_BADAN_TIDAK_SAH", None),
    ({"aliases": "x"}, "ALIAS_BADAN_TIDAK_SAH", None),
    ({"aliases": []}, "ALIAS_JUMLAH", None),
    ({"aliases": [{"teks": f"a{i}", "item_id": str(ITEM_A)} for i in range(51)]}, "ALIAS_JUMLAH", None),
    ({"aliases": [{"teks": "ok", "item_id": str(ITEM_A)}, {"teks": "   ", "item_id": str(ITEM_A)}]}, "ALIAS_TEKS_KOSONG", 1),
    ({"aliases": [{"teks": "x" * 121, "item_id": str(ITEM_A)}]}, "ALIAS_TEKS_PANJANG", 0),
    ({"aliases": [{"item_id": str(ITEM_A)}]}, "ALIAS_TEKS_TIDAK_SAH", 0),
    ({"aliases": [{"teks": "ok", "item_id": "bukan-uuid"}]}, "ALIAS_ITEM_TIDAK_SAH", 0),
    ({"aliases": [{"teks": "Combed 50", "item_id": str(ITEM_A)},
                  {"teks": " combed  50", "item_id": str(ITEM_B)}]}, "ALIAS_TEKS_GANDA", 1),
])
def test_validasi_menolak_400(badan, kode, index):
    with pytest.raises(HTTPException) as e:
        IA.validasi_pasangan(badan)
    assert _kode(e) == (400, kode, index)


def test_validasi_batas_atas_diterima():
    # 50 pasangan dan teks 120 karakter (sesudah normalisasi) = sah; spasi berlebih tak dihitung
    ps = IA.validasi_pasangan({"aliases": [{"teks": f"a{i}", "item_id": str(ITEM_A)} for i in range(50)]})
    assert len(ps) == 50
    ps = IA.validasi_pasangan({"aliases": [{"teks": "  " + "x" * 120 + "  ", "item_id": str(ITEM_A)}]})
    assert ps == [("x" * 120, ITEM_A)]


# ---------- PUT: pagar tenant, semua-atau-tidak ----------

@pytest.mark.asyncio
async def test_put_item_asing_400_tanpa_insert(pasang):
    db = pasang(DB())
    badan = {"aliases": [{"teks": "combed 50", "item_id": str(ITEM_A)},
                         {"teks": "punya orang", "item_id": str(ITEM_ASING)}]}
    with pytest.raises(HTTPException) as e:
        await IA.simpan_alias(_req(badan))
    assert _kode(e) == (400, "ALIAS_ITEM_TIDAK_SAH", 1)
    assert db.insert is None                       # semua-atau-tidak: nol tersimpan
    # pesan item asing == pesan item terhapus-lunak == uuid rusak (tak membocorkan)
    assert e.value.detail["message"] == "Barang untuk alias ini tidak ditemukan."


@pytest.mark.asyncio
async def test_put_kueri_pagar_tenant_dan_terhapus(pasang):
    db = pasang(DB())
    await IA.simpan_alias(_req({"aliases": [{"teks": "x", "item_id": str(ITEM_A)}]}))
    cek = db.sql[0]
    assert "tenant_id = $1" in cek and "deleted_at IS NULL" in cek and "FROM products" in cek


@pytest.mark.asyncio
async def test_put_sah_upsert_ternormalisasi_urut_masukan(pasang):
    db = pasang(DB())
    badan = {"aliases": [{"teks": "  Kaos Polos Hitam|XL ", "item_id": str(ITEM_B)},
                         {"teks": "COMBED 50", "item_id": str(ITEM_A)}]}
    out = await IA.simpan_alias(_req(badan))
    assert out == {"aliases": [{"teks": "kaos polos hitam|xl", "item_id": str(ITEM_B)},
                               {"teks": "combed 50", "item_id": str(ITEM_A)}]}
    tenant, teks, items_, oleh = db.insert
    assert tenant == TENANT and teks == ["kaos polos hitam|xl", "combed 50"]
    assert items_ == [ITEM_B, ITEM_A] and oleh == uuid.UUID(USER)
    sql = db.sql[-1]
    assert "ON CONFLICT (tenant_id, teks)" in sql and "DO UPDATE SET item_id = EXCLUDED.item_id" in sql


@pytest.mark.asyncio
async def test_put_badan_bukan_json_400(pasang):
    pasang(DB())
    with pytest.raises(HTTPException) as e:
        await IA.simpan_alias(_req(ValueError("json rusak")))
    assert _kode(e)[:2] == (400, "ALIAS_BADAN_TIDAK_SAH")


# ---------- GET ----------

@pytest.mark.asyncio
async def test_get_tenant_dan_sembunyikan_terhapus(pasang):
    db = pasang(DB())
    out = await IA.daftar_alias(_req())
    assert out == {"aliases": [{"teks": "combed 50", "item_id": str(ITEM_A)}]}
    sql = db.sql[0]
    assert "a.tenant_id = $1" in sql and "p.deleted_at IS NULL" in sql
    assert "p.tenant_id = a.tenant_id" in sql and db.args[0] == (TENANT,)


# ---------- urutan rute (main.py) ----------

def _urutan_include_main():
    pohon = ast.parse((APP / "main.py").read_text())
    urut = []
    for n in ast.walk(pohon):
        if (isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "include_router"
                and n.args and isinstance(n.args[0], ast.Attribute)
                and isinstance(n.args[0].value, ast.Name)
                and n.args[0].value.id in ("item_aliases", "items") and n.args[0].attr == "router"):
            prefix = [k.value.value for k in n.keywords if k.arg == "prefix"]
            urut.append((n.lineno, n.args[0].value.id, prefix[0] if prefix else ""))
    return [(nama, pre) for _, nama, pre in sorted(urut)]


def _handler_pertama(app, metode, path):
    scope = {"type": "http", "method": metode, "path": path, "root_path": ""}
    for r in app.router.routes:
        m, _ = r.matches(scope)
        if m == Match.FULL:
            return r.endpoint
    return None


@pytest.mark.parametrize("metode,harap", [("GET", "daftar_alias"), ("PUT", "simpan_alias")])
def test_rute_alias_menang_atas_item_id(metode, harap):
    urut = _urutan_include_main()
    assert [n for n, _ in urut] == ["item_aliases", "items"], urut   # sekali masing-masing, alias dulu
    app = FastAPI()
    modul = {"item_aliases": IA, "items": ITEMS}
    for nama, pre in urut:
        app.include_router(modul[nama].router, prefix=pre)
    ep = _handler_pertama(app, metode, "/api/items/aliases")
    assert ep is not None and ep.__module__ == IA.__name__ and ep.__name__ == harap
    # kontrol: rute detail item tetap milik items.router
    ep_item = _handler_pertama(app, "GET", f"/api/items/{ITEM_A}")
    assert ep_item.__module__ == ITEMS.__name__


# ---------- peta izin ----------

def test_izin_alias_bukan_pola_item_id():
    from app.middleware.permission_middleware import PermissionMiddleware
    pm = PermissionMiddleware(app=None)
    assert pm._find_permission("/api/items/aliases", "GET") == ("item", "R")
    assert pm._find_permission("/api/items/aliases", "PUT") == ("sales_order", "C")
    # kontrol: pola umum tetap berlaku untuk item sungguhan
    assert pm._find_permission(f"/api/items/{ITEM_A}", "PUT") == ("item", "U")


# ---------- DDL V305 ----------

def test_ddl_v305():
    sql = MIG.read_text()
    assert re.search(r"CREATE TABLE IF NOT EXISTS item_aliases", sql)
    assert "UNIQUE (tenant_id, teks)" in sql
    assert re.search(r"item_id\s+uuid\s+NOT NULL REFERENCES products\(id\) ON DELETE CASCADE", sql)
    assert re.search(r'tenant_id\s+text\s+NOT NULL REFERENCES "Tenant"\(id\) ON DELETE CASCADE', sql)
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert f"length(teks) BETWEEN 1 AND {IA.MAKS_TEKS}" in sql and "varchar(120)" in sql
