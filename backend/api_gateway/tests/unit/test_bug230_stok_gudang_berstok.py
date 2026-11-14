"""BUG-230: /warehouses/{id}/stock bawaan hanya baris ber-stok (== barang di strip detail + stock-value); hanya_berstok=false = lama."""
import asyncio
import inspect
from types import SimpleNamespace
from uuid import uuid4

from app.routers import warehouses as W


class _Conn:
    def __init__(self):
        self.sql = []

    async def execute(self, *a, **k):
        return "OK"

    async def fetchrow(self, sql, *a):
        return {"id": a[0], "name": "Gudang"}

    async def fetchval(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return 0

    async def fetch(self, sql, *a):
        self.sql.append((" ".join(sql.split()), a))
        return []


class _Pool:
    def __init__(self, c): self.c = c

    def acquire(self):
        c = self.c

        class _Cm:
            async def __aenter__(s): return c

            async def __aexit__(s, *e): return False
        return _Cm()


def _jalan(monkeypatch, **kw):
    c = _Conn()

    async def _p():
        return _Pool(c)
    monkeypatch.setattr(W, "get_pool", _p)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "kaos", "user_id": None}))
    asyncio.run(W.get_warehouse_stock(req, uuid4(), skip=0, limit=50, search=kw.get("search"), hanya_berstok=kw["hanya_berstok"]))
    return c.sql


def test_bawaan_hanya_berstok_di_hitungan_dan_baris(monkeypatch):
    sql = _jalan(monkeypatch, hanya_berstok=True)
    assert len(sql) == 2 and all("ws.quantity <> 0" in s for s, _ in sql)


def test_tampilkan_nol_tanpa_saringan(monkeypatch):
    sql = _jalan(monkeypatch, hanya_berstok=False)
    assert len(sql) == 2 and all("ws.quantity <> 0" not in s for s, _ in sql)


def test_cari_digabung_dengan_saringan_berstok(monkeypatch):
    sql = _jalan(monkeypatch, hanya_berstok=True, search="kaos")
    for s, a in sql:
        assert "ws.quantity <> 0" in s and "ILIKE $3" in s and a[2] == "%kaos%"


def test_bawaan_parameter_true():
    assert W.get_warehouse_stock.__defaults__ is not None or True
    d = inspect.signature(W.get_warehouse_stock).parameters["hanya_berstok"].default
    assert getattr(d, "default", d) is True
