"""11 Okt 2026 (pemilik LANGSUNG via MASTER): barang di Cari/⌘K -- grup `items` di /api/search.
Izin = modul `item` (rute daftar GET /api/items); tanpa izin -> grup HILANG (SQL products tak dijalankan);
barang terhapus tak muncul (deleted_at IS NULL); scope=items sah; url_hint = rute FE /persediaan/barang/:id."""
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from app.routers import search as SR

BARIS = {"id": "b1", "title": "Kaos Polos Hitam", "subtitle": "BRG-0001 · pcs", "number": "BRG-0001",
         "amount": None, "status": None, "due_date": None, "matched_field": "name"}


class _Eng:
    def __init__(self, boleh):
        self.boleh = boleh

    async def get_user_context(self, **k):
        return SimpleNamespace()

    async def can(self, ctx, aksi, modul):
        return modul in self.boleh


class _Conn:
    def __init__(self):
        self.sql = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a):
        pass

    async def fetch(self, sql, *a):
        s = " ".join(sql.split())
        self.sql.append(s)
        return [dict(BARIS)] if "FROM products" in s else []


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return self.c


class _SetUser(BaseHTTPMiddleware):
    async def dispatch(self, req, nxt):
        req.state.user = {"user_id": "u", "tenant_id": "t", "role": "ADMIN"}
        return await nxt(req)


def _cari(monkeypatch, boleh, url):
    c = _Conn()

    async def pool():
        return _Pool(c)

    monkeypatch.setattr(SR, "get_policy_engine", lambda: _Eng(boleh))
    monkeypatch.setattr(SR, "get_pool", pool)
    app = FastAPI()
    app.include_router(SR.router, prefix="/api/search")
    app.add_middleware(_SetUser)
    return TestClient(app).get(url), c


def test_peran_berizin_item_mendapat_grup_items(monkeypatch):
    r, c = _cari(monkeypatch, {"item"}, "/api/search?q=kaos")
    assert r.status_code == 200, r.text
    g = r.json()["groups"]
    assert [x["type"] for x in g] == ["items"]
    it = g[0]["items"][0]
    assert it == {**BARIS, "type": "items", "url_hint": "/persediaan/barang/b1"}
    assert sum("FROM products" in s for s in c.sql) == 1


def test_peran_tanpa_izin_item_tak_mencari_barang(monkeypatch):
    r, c = _cari(monkeypatch, {"sales_invoice", "customer"}, "/api/search?q=kaos")
    assert r.status_code == 200, r.text
    assert "items" not in [x["type"] for x in r.json()["groups"]]
    assert not any("FROM products" in s for s in c.sql)
    assert any("FROM sales_invoices" in s for s in c.sql)  # kontrol positif: yang boleh tetap dicari


def test_scope_items_sah_dan_hanya_barang(monkeypatch):
    r, c = _cari(monkeypatch, {"item", "customer"}, "/api/search?q=kaos&scope=items")
    assert r.status_code == 200, r.text
    assert [x["type"] for x in r.json()["groups"]] == ["items"]
    jalan = [s for s in c.sql if "FROM customers WHERE tenant_id=$1 AND coalesce" not in s]  # lewati cross-match
    assert len(jalan) == 1 and "FROM products" in jalan[0]


def test_sql_barang_filter_tenant_dan_terhapus():
    modul, url, sql = SR.GROUPS["items"]
    s = " ".join(sql.split())
    assert modul == "item" and url == "/persediaan/barang/"
    assert "WHERE tenant_id=$1 AND deleted_at IS NULL" in s
    for kolom in ("nama_produk", "item_code", "sku", "barcode"):
        assert kolom in s.split("LIKE")[-2], kolom  # semua medan cari ada di ekspresi LIKE
    assert "LIMIT $4" in s


def test_urutan_grup_barang_sesudah_kontak():
    assert SR.GROUP_ORDER[:3] == ["customers", "vendors", "items"]
    assert set(SR.GROUP_ORDER) == set(SR.GROUPS)
