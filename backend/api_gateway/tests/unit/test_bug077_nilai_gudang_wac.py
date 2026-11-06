"""BUG-077 (6 Nov 2026): nilai stok per gudang = qty gudang x WAC BARANG (get_weighted_average_cost, sama dengan COGS), bukan literal 0
(/stock) atau purchase_price (/stock-value). Rupiah 0 dp per baris dari WAC 2 dp; satu ekspresi untuk kedua endpoint.
Invarian data (baca saja, kaos): tests/unit/data/gerbang-bug077-nilai-gudang.sql -- per barang Σ gudang vs ROUND(Q x WAC, 0) dengan toleransi
0,5 x n_gudang (pembulatan per baris), dan vs nilai kartu stok dengan tambahan Q x 0,005 (WAC 2 dp, batas Check 15); hanya barang yang
memenuhi Inv-1 (Σ warehouse_stock = qty ledger; stok negatif dijepit 0 / baris tanpa gudang = di luar janji)."""
import asyncio
import inspect
import os
from decimal import ROUND_HALF_UP, Decimal as D
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.routers import warehouses as W  # noqa: E402

GERBANG = Path(__file__).resolve().parent / "data" / "gerbang-bug077-nilai-gudang.sql"


def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def test_ekspresi_bersama_wac_barang_bertenant():
    assert W.WAC_GUDANG == "CROSS JOIN LATERAL (SELECT get_weighted_average_cost($2, ws.item_id) AS wac) w"
    assert W.NILAI_GUDANG == "ROUND(ws.quantity * w.wac, 0)"   # dari WAC 2 dp, bukan unit_cost yang sudah dibulatkan


def test_kedua_endpoint_memakai_ekspresi_yang_sama_tanpa_nol_atau_harga_beli():
    for fn in (W.get_warehouse_stock, W.get_warehouse_stock_value):
        s = _src(fn)
        assert "{WAC_GUDANG}" in s and "{NILAI_GUDANG}" in s and "ws.tenant_id = $2" in s
    s = " ".join(inspect.getsource(W).split())
    assert "0 as unit_cost" not in s and "0 as total_value" not in s and "purchase_price" not in s
    assert "ROUND(w.wac, 0)::BIGINT as unit_cost" in _src(W.get_warehouse_stock)


def test_gerbang_data_memakai_rumus_yang_sama_dengan_router():
    g = GERBANG.read_text()
    assert W.NILAI_GUDANG in g and "get_weighted_average_cost($1, ws.item_id) AS wac) w" in g
    assert "BEGIN READ ONLY;" in g and "0.5 * g.n" in g and "k.q * 0.005" in g


def _r0(x):
    return x.quantize(D("1"), rounding=ROUND_HALF_UP)


@pytest.mark.parametrize("qs,wac", [
    ([D("1"), D("1"), D("1")], D("10000.50")),        # tiap baris membulat ke atas: Σ 30.003 vs 30.002 (selisih 1 <= 1,5)
    ([D("3.25"), D("0.37")], D("128000.33")),
    ([D("1000")], D("174000")),
])
def test_toleransi_pembulatan_per_gudang(qs, wac):
    per_gudang = sum(_r0(q * wac) for q in qs)
    assert abs(per_gudang - _r0(sum(qs) * wac)) <= D("0.5") * len(qs)


class _Conn:
    def __init__(self, rows=None, agg=None):
        self.sql, self.rows, self.agg = [], rows or [], agg

    async def execute(self, sql, *a):
        return None

    async def fetchrow(self, sql, *a):
        self.sql.append((sql, a))
        return {"id": a[0], "name": "Gudang Utama"} if "FROM warehouses" in sql else self.agg

    async def fetchval(self, sql, *a):
        return len(self.rows)

    async def fetch(self, sql, *a):
        self.sql.append((sql, a))
        return self.rows


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return _A()


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "kaos", "user_id": None}))


def test_stock_mengirim_nilai_wac_ke_respons(monkeypatch):
    wid, iid = uuid4(), uuid4()
    c = _Conn(rows=[{"item_id": iid, "item_code": "MK", "item_name": "Minyak Kita", "quantity": D("1000"), "reserved_quantity": D("0"),
                     "available_quantity": D("1000"), "unit": "dus", "unit_cost": 174000, "total_value": 174000000, "reorder_level": None,
                     "reorder_quantity": None, "last_stock_date": None}])

    async def _p():
        return _Pool(c)
    monkeypatch.setattr(W, "get_pool", _p)
    r = asyncio.run(W.get_warehouse_stock(_req(), wid, skip=0, limit=50, search=None))
    assert (r.data[0].unit_cost, r.data[0].total_value) == (174000, 174000000)
    sql, a = c.sql[-1]
    assert W.WAC_GUDANG in sql and W.NILAI_GUDANG in sql and a[:2] == (wid, "kaos")


def test_stock_value_menjumlah_nilai_wac(monkeypatch):
    wid = uuid4()
    c = _Conn(agg={"total_items": 9, "total_quantity": D("1082.62"), "total_value": 182287539})

    async def _p():
        return _Pool(c)
    monkeypatch.setattr(W, "get_pool", _p)
    r = asyncio.run(W.get_warehouse_stock_value(_req(), wid))
    assert r.total_value == 182287539
    sql, a = c.sql[-1]
    assert f"SUM({W.NILAI_GUDANG})" in sql and W.WAC_GUDANG in sql and "purchase_price" not in sql and a == (wid, "kaos")
