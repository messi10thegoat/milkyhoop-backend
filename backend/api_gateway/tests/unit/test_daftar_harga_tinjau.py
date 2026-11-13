"""BUG-027 (5 Nov 2026): POST /price-lists/preview = Tinjau server halaman CW "Daftar harga baru". Penentu SAMA dengan POST
/price-lists (siapkan_buat_daftar), transaksi READ ONLY, nol tulis; galat -> blok berkode Bahasa Indonesia ber-medan."""
import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import price_lists as RP

BARANG = "11111111-1111-1111-1111-111111111111"


class _Tx:
    def __init__(self, c, readonly):
        self.c, self.readonly = c, readonly

    async def start(self):
        self.c.tx.append(("start", self.readonly))

    async def rollback(self):
        self.c.tx.append(("rollback", self.readonly))

    async def __aenter__(self):
        self.c.tx.append(("masuk", self.readonly))

    async def __aexit__(self, *e):
        return False


class _C:
    def __init__(self, kode=(), bawaan=False, barang=(BARANG,)):
        self.kode, self.bawaan, self.barang = set(kode), bawaan, set(barang)
        self.tx, self.tulis = [], []

    def transaction(self, readonly=False):
        return _Tx(self, readonly)

    async def fetchrow(self, sql, *a):  # penentu barang dijual (D2): barang uji = aktif + dijual
        if "FROM products WHERE id = $1 AND tenant_id = $2" in sql and str(a[0]) in self.barang:
            return {"nama_produk": "Barang", "status": "active", "for_sales": True}
        return None

    async def fetchval(self, sql, *a):
        if sql.lstrip().startswith(("INSERT", "UPDATE", "DELETE")):
            self.tulis.append(sql)
            return "pl-baru"
        if "FROM price_lists WHERE tenant_id = $1 AND code = $2" in sql:
            return 1 if a[1] in self.kode else None
        if "is_default AND is_active" in sql:
            return 1 if self.bawaan else None
        if "FROM products WHERE id = $1 AND tenant_id = $2" in sql:
            return 1 if str(a[0]) in self.barang else None
        if "FROM price_list_items" in sql:
            return None
        raise AssertionError(sql[:80])


class _P:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(s):
                return c

            async def __aexit__(s, *e):
                return False
        return _A()


@pytest.fixture(autouse=True)
def _satuan_lolos(monkeypatch):
    """Konversi satuan punya tesnya sendiri (test_konversi_terkunci / satuan_dasar); di sini cukup dicatat dipanggil."""
    dipanggil = []

    async def qd(conn, tid, pid, unit, qty, *a):
        dipanggil.append(unit)
        return qty
    monkeypatch.setattr(RP.SD, "qty_dasar", qd)
    return dipanggil


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}))


def _tinjau(c, body, monkeypatch):
    async def pool():
        return _P(c)
    monkeypatch.setattr(RP, "get_pool", pool)
    return asyncio.run(RP.preview_create_price_list(_req(), body))["data"]


def _baris(**x):
    return {"item_id": BARANG, "price": "12500", **x}


def test_tinjau_ok_rencana_medan_disimpan_payload_apa_adanya_nol_tulis(monkeypatch, _satuan_lolos):
    c = _C()
    body = {"code": "  GRS-01 ", "name": "  Harga   Grosir ", "is_default": True, "items": [_baris(unit=" Dus ", min_quantity="10")]}
    d = _tinjau(c, body, monkeypatch)
    assert d["ok"] and d["blocks"] == [] and d["payload"] == body
    r = d["rencana"]
    assert (r["code"], r["name"], r["is_default"], r["price_type"]) == ("GRS-01", "Harga Grosir", True, "fixed")
    assert [(b["item_id"], b["unit"], str(b["min_quantity"]), str(b["price"])) for b in r["items"]] == [(BARANG, "Dus", "10", "12500")]
    assert c.tulis == [] and c.tx == [("start", True), ("rollback", True)] and _satuan_lolos == [" Dus "]


@pytest.mark.parametrize("body, kode, medan", [
    ({"code": "GRS-01", "name": "Grosir"}, "KODE_DAFTAR_GANDA", "code"),
    ({"code": "   ", "name": "Grosir"}, "KODE_DAFTAR_KOSONG", "code"),
    ({"code": "B", "name": "  "}, "NAMA_DAFTAR_KOSONG", "name"),
    ({"code": "B", "name": "G", "start_date": "2026-11-10", "end_date": "2026-11-01"}, "PERIODE_TIDAK_SAH", "end_date"),
    ({"code": "B", "name": "G", "is_default": True}, "DAFTAR_BAWAAN_GANDA", "is_default"),
    ({"code": "B", "name": "G", "items": [{"item_id": "22222222-2222-2222-2222-222222222222", "price": 1}]}, "BARANG_TIDAK_DITEMUKAN", "items.0.item_id"),
    ({"code": "B", "name": "G", "items": [_baris(unit="Dus"), _baris(unit=" dus ")]}, "HARGA_GANDA", "items.1"),
])
def test_tinjau_galat_berkode_bermedan_kalimat_indonesia(monkeypatch, body, kode, medan):
    c = _C(kode={"GRS-01"}, bawaan=True)
    d = _tinjau(c, body, monkeypatch)
    assert not d["ok"] and d["rencana"] is None and [(b["code"], b["field"]) for b in d["blocks"]] == [(kode, medan)]
    assert c.tulis == []


def test_tinjau_galat_skema_blok_per_medan_bahasa_indonesia(monkeypatch):
    d = _tinjau(_C(), {"name": "G", "price_type": "x", "items": [{"item_id": BARANG, "price": -1}]}, monkeypatch)
    assert not d["ok"] and d["rencana"] is None
    pesan = {b["field"]: b["message"] for b in d["blocks"]}
    assert pesan == {"code": "wajib diisi.", "price_type": "pilihan tidak dikenal.", "items.0.price": "minimal 0."}


def test_simpan_memakai_penentu_yang_sama_tolak_sebelum_insert(monkeypatch):
    c = _C(kode={"GRS-01"})

    async def pool():
        return _P(c)
    monkeypatch.setattr(RP, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        asyncio.run(RP.create_price_list(_req(), RP.CreatePriceListRequest(code="GRS-01", name="Grosir")))
    assert e.value.status_code == 409 and e.value.code == "KODE_DAFTAR_GANDA" and e.value.field == "code" and c.tulis == []
    ok = asyncio.run(RP.create_price_list(_req(), RP.CreatePriceListRequest(code=" BARU ", name=" Grosir  Baru ")))
    assert ok["data"]["code"] == "BARU" and "INSERT INTO price_lists" in c.tulis[0]
    assert "siapkan_buat_daftar(conn" in inspect.getsource(RP.create_price_list)
    assert "siapkan_buat_daftar(conn" in inspect.getsource(RP.preview_create_price_list)


def test_rute_preview_tak_tertimpa_rute_lain(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    c = _C(kode={"GRS-01"})

    async def pool():
        return _P(c)
    monkeypatch.setattr(RP, "get_pool", pool)
    app = FastAPI()

    @app.middleware("http")
    async def masuk(request, call_next):
        request.state.user = _req().state.user
        return await call_next(request)
    app.include_router(RP.router, prefix="/api/price-lists")
    r = TestClient(app).post("/api/price-lists/preview", json={"code": "GRS-01", "name": "Grosir"})
    assert r.status_code == 200 and r.json()["data"]["blocks"][0]["code"] == "KODE_DAFTAR_GANDA"
