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
    (-2, 0, 0, "0"),            # tanpa batas minimum = tidak dipantau, sekalipun stok negatif
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


# --- Pesan Ulang SO (VONIS gerbang-cloud/diag-pesan-ulang-so §3, 26 Nov) ---------------------------------------------
class KonnPO(Konn):
    def __init__(self, rows, po=()): super().__init__(rows); self.po = list(po)
    async def fetch(self, sql, *a):
        return self.po if sql == RP.SQL_PO_TERBUKA else await super().fetch(sql, *a)


def _k():
    return Konn([dict(r(1, "Beras", 2, 10), kode="BRS-1"), r(2, "Cabai", 0, 4, dipesan=1), r(3, "Minyak", 0, 10, lain=3), r(4, "Aqua", 50, 10)])


def test_teks_server_dan_penanda_pill_per_baris():
    x = next(i for i in jalan(RP.daftar(_k(), "t"))["items"] if i["nama_produk"] == "Beras")
    assert (x["stok_text"], x["minimum_text"], x["dipesan_text"], x["usulan_beli_text"]) == ("2 pcs", "10 pcs", "0 pcs", "8 pcs")
    assert x["belum_dipesan"] and not x["sebagian_dipesan"] and not x["ada_po_satuan_lain"]


def test_cari_nama_atau_kode_dan_saring_status_server():
    assert [x["nama_produk"] for x in jalan(RP.daftar(_k(), "t", search="brs"))["items"]] == ["Beras"]
    h = jalan(RP.daftar(_k(), "t", status="sebagian_dipesan"))
    assert [x["nama_produk"] for x in h["items"]] == ["Cabai"] and h["total"] == 1 and h["jumlah_perlu_pesan"] == 3
    f = {x["kode"]: x["jumlah"] for x in h["filter_tersedia"]}
    assert f == {"belum_dipesan": 2, "sebagian_dipesan": 1, "ada_po_satuan_lain": 1}
    assert h["ringkasan"] == {"jumlah_perlu_pesan_text": "3 barang perlu dipesan", "jumlah_dipantau_text": "4 barang dipantau"}


def test_skip_limit_has_more():
    k = Konn([r(i, f"B{i}", 0, 5) for i in range(5)])
    a, b = jalan(RP.daftar(k, "t", 2)), jalan(RP.daftar(k, "t", 2, skip=4))
    assert a["has_more"] and a["total"] == 5 and len(b["items"]) == 1 and not b["has_more"] and b["skip"] == 4


def test_po_terbuka_hanya_untuk_baris_halaman():
    k = KonnPO([r(1, "Beras", 0, 10, dipesan=2)], [{"item_id": "1", "id": "po1", "nomor": "PO-0001"}])
    x = jalan(RP.daftar(k, "t", po=True))["items"][0]
    assert x["po_terbuka"] == [{"id": "po1", "nomor": "PO-0001"}]
    assert "po.tenant_id = $1" in RP.SQL_PO_TERBUKA and "'sent', 'partial_received'" in RP.SQL_PO_TERBUKA


def test_status_tak_dikenal_ditolak_422_di_router():
    inv = (APP / "routers" / "inventory.py").read_text()
    assert "status not in RP.STATUS" in inv and "status_code=422" in inv


def test_cari_per_kata_semua_kata_dan_barcode():
    perlu = [{"nama_produk": "Kaos Hitam 24s", "kode": "BRG-1", "barcode": "899001", "gudang": None},
             {"nama_produk": "Kaos Putih", "kode": "BRG-2", "barcode": None, "gudang": None}]
    nama = lambda q: [x["nama_produk"] for x in RP.saring(perlu, q, None)]
    assert nama("hitam kaos") == ["Kaos Hitam 24s"]      # urutan kata bebas, semua kata harus cocok
    assert nama("kaos") == ["Kaos Hitam 24s", "Kaos Putih"]
    assert nama("899001") == ["Kaos Hitam 24s"]          # barcode ikut dicari
    assert nama("kaos biru") == []
    assert nama("  ") == ["Kaos Hitam 24s", "Kaos Putih"]
