"""LP-4: ekspor laporan nilai & mutasi persediaan (berkas == teks server; total + rekonsiliasi ikut)."""
import csv
import io

from app.services import ekspor_laporan_persediaan as E

NILAI = {"per_tanggal": "2026-11-27", "kelompok": "gudang", "satuan_text": "(Disajikan dalam Rupiah)", "total_nilai_text": "1.500",
         "dalam_perjalanan": {"qty_text": "3", "nilai_text": "300"},
         "baris": [{"kode": "A1", "nama": "Kaos", "kategori": "K", "satuan": "pcs", "qty_text": "10", "hpp_satuan_text": "150", "nilai_text": "1.500", "gudang": {"id": "g", "nama": "Utama"}}],
         "rekonsiliasi": {"status_text": "Cocok dengan buku besar", "sub_ledger_text": "1.500", "gl_text": "1.500", "selisih_text": "0"}}

MUTASI = {"kelompok": "barang", "periode_text": "1 Nov - 27 Nov 2026", "satuan_text": "(Rp)", "dalam_perjalanan": None,
          "kolom_text": {"awal": "Saldo awal", "akhir": "Saldo akhir"},
          "baris": [{"kode": "A1", "nama": "Kaos", "satuan": "pcs", "awal": {"qty_text": "1", "nilai_text": "100"}, "akhir": {"qty_text": "2", "nilai_text": "200"}}],
          "total": {"awal": {"nilai_text": "100"}, "akhir": {"nilai_text": "200"}},
          "rekonsiliasi": {"status_text": "Cocok", "awal": {"ledger_text": "100", "gl_text": "100", "selisih_text": "0"}, "akhir": {"ledger_text": "200", "gl_text": "200", "selisih_text": "0"}}}


def test_tabel_nilai_kolom_gudang_total_transit_rekon():
    k, b, bawah = E.tabel_nilai(NILAI)
    assert k[0] == "Gudang" and k[-1] == "Nilai" and b == [["Utama", "A1", "Kaos", "K", "pcs", "10", "150", "1.500"]]
    teks = str(bawah)
    assert "Total nilai" in teks and "Dalam perjalanan (Transit)" in teks and "Cocok dengan buku besar" in teks


def test_tabel_nilai_tanpa_gudang_tanpa_transit():
    d = dict(NILAI, kelompok="barang", dalam_perjalanan=None)
    k, b, bawah = E.tabel_nilai(d)
    assert k[0] == "Kode" and "Transit" not in str(bawah)


def test_tabel_mutasi_kolom_berpasangan_dan_total_sejajar():
    k, b, bawah = E.tabel_mutasi(MUTASI)
    assert k == ["Kode", "Barang", "Satuan", "Saldo awal (qty)", "Saldo awal (nilai)", "Saldo akhir (qty)", "Saldo akhir (nilai)"]
    assert b == [["A1", "Kaos", "pcs", "1", "100", "2", "200"]]
    tot = [r for r in bawah if r and r[0] == "Total nilai"][0]
    assert len(tot) == len(k) and tot[4] == "100" and tot[6] == "200"


def test_csv_bom_dan_isi():
    k, b, bawah = E.tabel_nilai(NILAI)
    raw = E.bangun_csv(k, b, bawah)
    assert raw.startswith("﻿".encode("utf-8"))
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0] == k and rows[1] == b[0]


def test_xlsx_terbuka():
    from openpyxl import load_workbook
    k, b, bawah = E.tabel_mutasi(MUTASI)
    ws = load_workbook(io.BytesIO(E.bangun_xlsx("mutasi-persediaan", k, b, bawah))).active
    assert [c.value for c in ws[1]] == k and ws["A2"].value == "A1"
