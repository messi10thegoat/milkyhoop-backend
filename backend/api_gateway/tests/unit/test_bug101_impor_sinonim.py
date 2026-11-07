"""BUG-101 (K-10 TESTER, MASTER 7 Nov; pola Accurate): impor Excel BUKAN templat. Judul kolom bebas lewat SINONIM (normalisasi lalu cocok
PERSIS, sinonim pendek tak menelan judul spesifik); kolom tak dikenal DISEBUT (tak_dipakai/peringatan), bukan dibuang diam; pemetaan
judul berkas -> medan dikirim; dua judul ke satu medan = KOLOM_GANDA. Fixture = berkas repro TESTER apa adanya (qa/bukti/k10-impor)."""
import io
import pathlib

import pytest
from fastapi import HTTPException
from openpyxl import Workbook

from app.services import impor_barang as IB

DATA = pathlib.Path(__file__).resolve().parent / "data"


def _xlsx(*rows) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_r1_judul_panjang_isi_dan_harga_terbaca():
    h = IB.baca_xlsx((DATA / "r1-judul-panjang.xlsx").read_bytes())
    b = h["baris"][0]
    assert (b["nama"], b["satuan_dasar"], b["satuan_besar"], b["isi"], b["harga_jual"], b["harga_beli"], b["kategori"]) == (
        "UJI10 Minyak Sawit 1L", "botol", "karton", 12, 18000, 15500, "Minyak Goreng")
    assert len(h["baris"]) == 5 and h["tak_dipakai"] == [] and h["peringatan"] == []


def test_r2_judul_nama_diterima_tujuh_kolom_terpetakan():
    h = IB.baca_xlsx((DATA / "r2-judul-nama.xlsx").read_bytes())
    assert [(p["judul_berkas"], p["medan"]) for p in h["pemetaan"]] == [
        ("Nama", "nama"), ("Satuan dasar", "satuan_dasar"), ("Satuan besar", "satuan_besar"), ("Isi", "isi"),
        ("Harga jual", "harga_jual"), ("Harga beli", "harga_beli"), ("Kategori", "kategori")]
    assert h["baris"][0]["isi"] == 12 and h["baris"][0]["harga_beli"] == 15500


def test_kolom_tak_dikenal_disebut_bukan_diam():
    h = IB.baca_xlsx(_xlsx(["Nama", "Satuan", "Catatan gudang"], ["Gula", "kg", "rak 3"]))
    assert h["tak_dipakai"] == ["Catatan gudang"] and h["peringatan"] == ["Kolom 'Catatan gudang' tidak dipakai"]
    assert h["baris"] == [{"nama": "Gula", "satuan_dasar": "kg"}]


def test_sinonim_pendek_tak_menelan_judul_spesifik():
    h = IB.baca_xlsx(_xlsx(["Satuan", "Satuan besar", "Harga", "Harga beli", "Nama barang"], ["pcs", "dus", 5000, 4000, "Kopi"]))
    assert {p["judul_berkas"]: p["medan"] for p in h["pemetaan"]} == {
        "Satuan": "satuan_dasar", "Satuan besar": "satuan_besar", "Harga": "harga_jual", "Harga beli": "harga_beli", "Nama barang": "nama"}


def test_dua_judul_satu_medan_kolom_ganda():
    with pytest.raises(HTTPException) as e:
        IB.baca_xlsx(_xlsx(["Nama", "Satuan", "Harga", "Harga jual"], ["Kopi", "pcs", 1, 2]))
    assert e.value.detail["code"] == "KOLOM_GANDA" and "'Harga'" in e.value.detail["message"] and "'Harga jual'" in e.value.detail["message"]


def test_kolom_wajib_tak_ada_menyebut_judul_dikenali():
    with pytest.raises(HTTPException) as e:
        IB.baca_xlsx(_xlsx(["foo", "bar"], [1, 2]))
    assert e.value.detail["code"] == "KOLOM_TIDAK_DIKENAL" and "Nama barang" in e.value.detail["message"]


def test_judul_templat_lama_tetap_cocok_semua():
    h = IB.baca_xlsx(IB.templat_xlsx([{"abbreviation": "Pcs"}]))
    assert {p["medan"] for p in h["pemetaan"]} == {k for k, _, _ in IB.KOLOM} and h["tak_dipakai"] == []


def test_satu_sinonim_satu_medan():
    peta = IB._peta_sinonim()
    assert [j for j, m in peta.items() if len(m) > 1] == []
