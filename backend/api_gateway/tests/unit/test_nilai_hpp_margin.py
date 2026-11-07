"""BUG-034 nilai persediaan Σ server (stats.inventoryValue / summary.inventory_value) · BUG-038 HPP rata-rata (WAC) + margin di detail
barang · riwayat tanpa membuang sen (6 Nov 2026)."""
import inspect
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.routers import items as RI


@pytest.mark.parametrize("nilai, stok, jual, harap", [
    (D("9270720"), D("240"), None, (D("38628.00"), None, None)),                 # kontrol nyawit Sania (MASTER)
    (D("9270720"), D("240"), D("41500"), (D("38628.00"), D("2872.00"), D("6.9"))),
    (D("1000"), D("3"), D("500"), (D("333.33"), D("166.67"), D("33.3"))),
    (D("43069"), D("1"), D("40000"), (D("43069.00"), D("-3069.00"), D("-7.7"))),  # rugi = negatif, bukan disembunyikan
    (D("0"), D("0"), D("5000"), (None, None, None)),                               # stok 0 -> tanpa WAC
    (None, D("5"), D("5000"), (None, None, None)),
    (D("500"), D("5"), D("0"), (D("100.00"), None, None)),                          # tanpa harga jual
])
def test_hpp_dan_margin(nilai, stok, jual, harap):
    h = RI.hpp_dan_margin(nilai, stok, jual)
    assert (h["average_cost"], h["margin_amount"], h["margin_pct"]) == harap


class _C:
    def __init__(self, baris):
        self.baris, self.sql = baris, []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return self.baris

    async def close(self):
        pass


def _klien(c, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    async def conn():
        return c
    monkeypatch.setattr(RI, "get_db_connection", conn)
    app = FastAPI()

    @app.middleware("http")
    async def masuk(request, call_next):
        request.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    app.include_router(RI.router, prefix="/api")
    return TestClient(app)


BARIS = {"total_items": 3, "total_goods": 2, "total_services": 1, "total_non_inventory": 0, "in_stock": 1, "low_stock": 0, "out_of_stock": 1,
         "inventory_value": D("9270720.5"), "total": 3, "goods_count": 2, "service_count": 1, "tracked_count": 2, "in_stock_count": 1,
         "low_stock_count": 0, "out_of_stock_count": 1, "aktif": 3, "nonaktif": 0, "di_bawah_reorder": 0,
         "stok_habis_count": 1, "stok_habis_value": D("0"), "di_bawah_titik_pesan_count": 0, "di_bawah_titik_pesan_value": D("0"),
         "tanpa_harga_jual_count": 0, "tanpa_harga_jual_value": D("0"), "nonaktif_count": 0, "nonaktif_value": D("0")}


def test_rute_stats_inventory_value_sigma_server(monkeypatch):
    c = _C(BARIS)
    r = _klien(c, monkeypatch).get("/api/items/stats")
    assert r.status_code == 200 and D(str(r.json()["inventoryValue"])) == D("9270720.50")
    assert "SUM(per.total_nilai) FILTER (WHERE p.item_type = 'goods' AND p.track_inventory = true)" in c.sql[0]
    assert "WHERE p.tenant_id = $1 AND p.deleted_at IS NULL" in c.sql[0]


def test_rute_summary_inventory_value_sama(monkeypatch):
    c = _C(BARIS)
    r = _klien(c, monkeypatch).get("/api/items/summary")
    assert r.status_code == 200 and D(str(r.json()["data"]["inventory_value"])) == D("9270720.50")
    assert "SUM(per.total_nilai) FILTER (WHERE p.item_type = 'goods' AND p.track_inventory = true)" in c.sql[0]


def test_stats_tanpa_barang_nol_bukan_null(monkeypatch):
    r = _klien(_C({**BARIS, "inventory_value": None}), monkeypatch).get("/api/items/stats")
    assert D(str(r.json()["inventoryValue"])) == D("0")


def test_detail_memakai_hpp_dan_margin_dari_stock_value_yang_sama():
    src = inspect.getsource(RI.get_item)
    assert '**hpp_dan_margin(row.get("stock_value"), row.get("current_stock"), row.get("sales_price") or row.get("harga_jual"))' in src
    assert 'per.total_nilai as stock_value' in src


def test_riwayat_tanpa_int_buang_sen():
    src = inspect.getsource(RI.get_item_history) if hasattr(RI, "get_item_history") else inspect.getsource(RI)
    assert 'int(row["average_cost"]' not in src and 'int(row["unit_cost"]' not in src
    assert 'float(_rp(row["average_cost"])) if row["average_cost"] else None' in src
