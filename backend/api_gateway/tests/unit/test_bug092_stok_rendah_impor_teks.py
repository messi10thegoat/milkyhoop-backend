"""BUG-092 kartu STOK RENDAH: hitungan stok hanya barang AKTIF; 'di bawah titik pesan ulang' (belowReorder) hanya barang
ber-reorder_level > 0 (pola NetSuite/SAP) -> barang baru stok 0 tanpa titik pesan ulang tidak 'perlu perhatian';
ITEM AKTIF/nonaktif = hitungan server, tak bergantung filter daftar. BUG-093 + TAK_TERBACA: teks impor dari server."""
import io

from app.routers import items as RI
from app.services import impor_barang as IB
from tests.unit.test_nilai_hpp_margin import BARIS, _C, _klien


def test_stats_aktif_nonaktif_dan_di_bawah_reorder(monkeypatch):
    c = _C({**BARIS, "aktif": 11, "nonaktif": 5, "di_bawah_reorder": 2})
    r = _klien(c, monkeypatch).get("/api/items/stats")
    j = r.json()
    assert r.status_code == 200
    assert (j["activeItems"], j["inactiveItems"], j["stock"]["belowReorder"]) == (11, 5, 2)


def test_stats_sql_hitungan_stok_hanya_barang_aktif():
    sql = RI.STATS_SQL
    for alias in ("in_stock", "low_stock", "out_of_stock", "di_bawah_reorder"):
        blok = sql.split(f") as {alias}")[0].rsplit("COUNT(*) FILTER (", 1)[1]
        assert "p.status = 'active'" in blok, alias
    blok = sql.split(") as di_bawah_reorder")[0].rsplit("COUNT(*) FILTER (", 1)[1]
    assert "p.reorder_level > 0" in blok and "COALESCE(per.jumlah, 0) <= p.reorder_level" in blok
    assert "COUNT(*) FILTER (WHERE p.status = 'active') as aktif" in sql


def test_bug093_petunjuk_templat_tanpa_kolom_berbintang():
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(IB.templat_xlsx([{"abbreviation": "pcs"}])))
    a1 = wb["Petunjuk"]["A1"].value
    assert a1 == "Satu baris = satu barang baru. Wajib: Nama barang, Satuan dasar."


def test_tak_terbaca_menyebut_jalan_keluar():
    b = IB.tafsir_baris("teh pucuk jual 4rb blabla")
    _, bl, _ = IB.ke_badan(b)
    assert bl[0]["code"] == "TAK_TERBACA"
    assert bl[0]["message"] == 'Tak terbaca: "blabla". Ketik ulang baris ini atau hapus "blabla".'
