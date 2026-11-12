"""BUG-181: pencarian per KATA satu fungsi (services/cari_kata) untuk autocomplete barang + daftar barang: semua kata ada, urutan bebas, spasi ganda dirapikan, % _ \\ di-escape."""
import asyncio
import inspect
from types import SimpleNamespace

from starlette.requests import Request

import app.routers.items as IT
from app.services import cari_kata as CK


def test_escape():
    assert CK.escape_like("50%_x\\") == "50\\%\\_x\\\\"
    assert CK.syarat_kata("%", ("a",), 1)[1] == ["%\\%%"]          # % literal, bukan wildcard


def test_per_kata_urutan_bebas_dan_spasi_dirapikan():
    s1, p1 = CK.syarat_kata("Kaos  Polos", ("p.n", "p.k"), 3)
    s2, p2 = CK.syarat_kata("  Polos Kaos ", ("p.n", "p.k"), 3)
    assert p1 == ["%Kaos%", "%Polos%"] and p2 == ["%Polos%", "%Kaos%"]
    assert s1 == s2 == "AND ((p.n ILIKE $3 OR p.k ILIKE $3) AND (p.n ILIKE $4 OR p.k ILIKE $4))"
    assert CK.syarat_kata("", ("a",), 1) == ("", []) and CK.syarat_kata(None, ("a",), 1) == ("", []) and CK.syarat_kata("   ", ("a",), 1) == ("", [])


def test_daftar_barang_memakai_fungsi_yang_sama():
    assert IT._syarat_cari("Polos Kaos", 2) == CK.syarat_kata("Polos Kaos", ("p.nama_produk", "p.barcode"), 2)
    assert 'syarat_kata(q, ("p.nama_produk", "p.item_code", "p.sku"), 3)' in inspect.getsource(IT.autocomplete_items)


class _Kon:
    def __init__(self):
        self.q = []

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        return []


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


def _jalan(monkeypatch, q, tipe=None):
    k = _Kon()

    async def gp():
        return SimpleNamespace(acquire=lambda: _Acq(k))
    monkeypatch.setattr(IT, "get_pool", gp)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "u1"}
    asyncio.run(IT.autocomplete_items(r, q, 10, tipe))
    return k.q[0]


def test_autocomplete_parameter_urut(monkeypatch):
    sql, a = _jalan(monkeypatch, "Polos  Kaos", "goods")
    assert a == ("t1", 10, "%Polos%", "%Kaos%", "goods")
    assert "LIMIT $2" in sql and "item_type = $5" in sql and "p.nama_produk ILIKE $3" in sql and "p.sku ILIKE $4" in sql
    sql, a = _jalan(monkeypatch, "50%")
    assert a == ("t1", 10, "%50\\%%") and "item_type = $" not in sql
    sql, a = _jalan(monkeypatch, "")
    assert a == ("t1", 10) and "ILIKE" not in sql
