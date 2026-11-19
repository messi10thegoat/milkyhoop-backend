"""Urutan baris GET detail daftar harga (permintaan FE berjangka, 16 Nov 2026): satu barang+satuan+min qty dengan banyak tanggal mulai tampil berurutan
(tanpa tanggal lebih dulu = harga dasar), pasti dan tak berubah antar panggilan (id sebagai pemutus terakhir)."""
import inspect
import re

from app.routers import price_lists as PL


def test_urutan_detail_memuat_start_date_nulls_first_dan_id_pemutus():
    s = inspect.getsource(PL.get_price_list)
    m = re.search(r"ORDER BY ([^\"]+)\"\"\"", s)
    assert m and " ".join(m.group(1).split()) == "p.nama_produk, pli.item_id, (COALESCE(NULLIF(pli.unit, ''), COALESCE(NULLIF(p.base_unit, ''), p.satuan)) <> COALESCE(NULLIF(p.base_unit, ''), p.satuan)), COALESCE(NULLIF(pli.unit, ''), COALESCE(NULLIF(p.base_unit, ''), p.satuan)), pli.min_quantity, pli.start_date NULLS FIRST, pli.id"


def test_urutan_tanggal_mulai_dasar_dulu_lalu_menaik_simulasi():
    baris = [("A", None, 1, "2026-11-25"), ("A", None, 1, None), ("A", None, 1, "2026-11-20"), ("A", None, 10, None)]
    urut = sorted(baris, key=lambda r: (r[0], r[1] is not None, r[1] or "", r[2], r[3] is not None, r[3] or ""))
    assert [r[3] for r in urut if r[2] == 1] == [None, "2026-11-20", "2026-11-25"]


def test_urutan_baris_impor_unit_null_tak_lompat_ke_atas_simulasi():
    # BUG-311: baris impor menyimpan unit None untuk satuan dasar; baris lama menyimpan 'Pcs' eksplisit -> kunci memakai satuan efektif
    dasar = "Pcs"
    baris = [("A", "Pcs", 1), ("A", "Pcs", 12), ("A", None, 200), ("A", None, 300), ("A", "Dus", 1)]
    def kunci(r):
        u = r[1] or dasar
        return (r[0], u != dasar, u, r[2])
    assert [(r[1] or dasar, r[2]) for r in sorted(baris, key=kunci)] == [("Pcs", 1), ("Pcs", 12), ("Pcs", 200), ("Pcs", 300), ("Dus", 1)]
