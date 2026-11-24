"""Reorder persediaan (24 Nov 2026): usulan beli = max(minimum − (stok + dipesan), 0); satu penentu untuk GET /api/inventory/reorder dan reorder_count ringkasan."""
import asyncio
import os
from decimal import Decimal as D
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import reorder_persediaan as RP  # noqa: E402

APP = Path(__file__).parents[2] / "app"


def jalan(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


@pytest.mark.parametrize("stok,minimum,dipesan,harap", [
    (3, 10, 0, "7"), (3, 10, 4, "3"), (3, 10, 7, "0"), (3, 10, 20, "0"), (10, 10, 0, "0"),
    (-2, 10, 0, "12"),          # stok negatif (ledger jujur) menambah kekurangan
    (5, 0, 0, "0"), (5, None, 0, "0"), (0, 2.5, 0.5, "2"),
])
def test_usulan_beli(stok, minimum, dipesan, harap):
    assert RP.usulan_beli(stok, minimum, dipesan) == D(harap)


class Konn:
    def __init__(self, rows): self.rows, self.sql = rows, None
    async def fetch(self, sql, *a): self.sql = sql; return self.rows


def r(i, nama, stok, minimum, dipesan=0, lain=0, unit="pcs"):
    return {"id": str(i), "nama_produk": nama, "unit": unit, "stok": D(str(stok)), "minimum": D(str(minimum)), "dipesan": D(str(dipesan)), "dipesan_lain": D(str(lain))}


def test_daftar_hanya_yang_perlu_dipesan_terurut_kekurangan_terbesar_dan_hitung_dipantau():
    k = Konn([r(1, "Aqua", 50, 10), r(2, "Beras", 2, 10), r(3, "Cabai", 0, 4, dipesan=1), r(4, "Daun", 1, 3, dipesan=5)])
    h = jalan(RP.daftar(k, "t-1"))
    assert [x["nama_produk"] for x in h["items"]] == ["Beras", "Cabai"]            # 8 lalu 3; Aqua cukup, Daun tertutup pesanan
    assert h["jumlah_perlu_pesan"] == 2 and h["jumlah_dipantau"] == 4
    assert h["items"][0]["usulan_beli"] == D("8") and "usul pesan 8 pcs" in h["items"][0]["teks"]
    assert "sudah dipesan 1" in h["items"][1]["teks"]


def test_limit_memotong_daftar_tetapi_jumlah_tetap_utuh():
    k = Konn([r(i, f"B{i}", 0, 5) for i in range(5)])
    h = jalan(RP.daftar(k, "t-1", 2))
    assert len(h["items"]) == 2 and h["jumlah_perlu_pesan"] == 5


def test_pesanan_satuan_lain_tidak_dikonversi_diam_diam_tapi_diberi_catatan():
    h = jalan(RP.daftar(Konn([r(1, "Minyak", 0, 10, dipesan=0, lain=3)]), "t-1"))
    x = h["items"][0]
    assert x["usulan_beli"] == D("10") and x["dipesan_satuan_lain"] == D("3") and "satuan lain" in x["teks"]


def test_sql_stok_dari_ledger_po_terbuka_saja_dan_tanpa_cache_warehouse_stock():
    s = RP._SQL
    assert "inventory_ledger" in s and "warehouse_stock" not in s                      # Law 16
    assert "po.status IN ('sent', 'partial_received')" in s and "quantity_received" in s
    assert "p.tenant_id = $1" in s and "po.tenant_id = p.tenant_id" in s and "l.tenant_id = p.tenant_id" in s   # Law 24 lapis aplikasi


def test_tanpa_float_dan_tanpa_nilai_rupiah():
    src = (APP / "services" / "reorder_persediaan.py").read_text()
    assert "float(" not in src and "purchase_price" not in src and "unit_price" not in src


def test_endpoint_dan_ringkasan_memakai_penentu_yang_sama_serta_izin_item_R():
    inv = (APP / "routers" / "inventory.py").read_text()
    assert '@router.get("/reorder")' in inv and inv.count("RP.daftar(conn, tenant_id") == 2 and "reorder_count=reorder_perlu" in inv
    assert "reorder_count=0" not in inv
    assert '^/api/inventory/reorder$", ["GET"], "item", "R"' in (APP / "middleware" / "permission_middleware.py").read_text()
