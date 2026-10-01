"""P1 SO-dokumen: aturan Posisi (02 §Posisi) dan Kirim (01 §A) + putusan pemilik 1 Okt 2026 -- satu tes per aturan.

Angka dari LITERAL spek, bukan dari fungsi yang diuji (gerbang tautologis dilarang).
"""
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.so_posisi import kirim, posisi

HARI = date(2026, 10, 1)  # tanggal usaha (WIB)


def P(status="confirmed", sisa=D("1000"), belum_bayar=(), ada_faktur=False, sisa_faktur=D("0"),
      ada_sj=False, dp=D("0")):
    return posisi(status, sisa, list(belum_bayar), ada_faktur, sisa_faktur, ada_sj, dp)


DP = {"purpose": "DP", "termin_ke": None}
PEL = {"purpose": "PELUNASAN", "termin_ke": None}


def T(k):
    return {"purpose": "TERMIN", "termin_ke": k}


# ---- Posisi: tiap aturan ----
def test_1_batal_muted_menang_atas_semua():
    assert P("cancelled", sisa=D("0"), belum_bayar=[DP], ada_faktur=True, sisa_faktur=D("5")) == ("Batal", True)


def test_2_lunas_muted_bila_sisa_nol_atau_negatif():
    assert P(sisa=D("0"), belum_bayar=[DP]) == ("Lunas", True)
    assert P(sisa=D("-1")) == ("Lunas", True)


def test_4_menunggu_dp():
    assert P(belum_bayar=[DP], ada_faktur=True, sisa_faktur=D("10")) == ("Menunggu DP", False)


def test_4_menunggu_termin_k():
    assert P(belum_bayar=[T(2)]) == ("Menunggu termin 2", False)


def test_4_pelunasan_tanpa_faktur_menunggu_tagihan():
    assert P(belum_bayar=[PEL]) == ("Menunggu tagihan", False)


def test_putusan3_pelunasan_dengan_faktur_bersisa_menunggu_pelunasan():
    # SO-2609-0020 grapgrap: PRO PELUNASAN issued 0/945.000 + faktur sisa 945.000
    assert P(belum_bayar=[PEL], ada_faktur=True, sisa_faktur=D("945000")) == ("Menunggu pelunasan", False)


def test_4_proforma_pertama_yang_belum_bayar_menentukan():
    assert P(belum_bayar=[T(1), DP]) == ("Menunggu termin 1", False)
    assert P(belum_bayar=[PEL, DP], ada_faktur=True, sisa_faktur=D("1")) == ("Menunggu DP", False)


def test_5_menunggu_pelunasan():
    assert P(ada_faktur=True, sisa_faktur=D("1"), ada_sj=True, dp=D("5")) == ("Menunggu pelunasan", False)


def test_6_perlu_faktur_dikirim_tanpa_faktur():
    assert P(ada_sj=True, dp=D("5")) == ("Perlu faktur", False)


def test_6_tak_berlaku_bila_faktur_ada_tapi_lunas():
    # faktur ada, sisanya 0, SO masih bersisa (belum semua ditagih) -> bukan "Perlu faktur"; DP -> Produksi
    assert P(ada_faktur=True, sisa_faktur=D("0"), ada_sj=True, dp=D("5")) == ("Produksi", False)


def test_7_produksi_ada_dp():
    assert P(dp=D("1000000")) == ("Produksi", False)


def test_8_belum_ditagih():
    assert P() == ("Belum ditagih", False)
    assert P("draft") == ("Belum ditagih", False)


# ---- Kirim ----
def K(status="confirmed", d=date(2026, 10, 2), perlu=True, semua=False):
    return kirim(status, d, perlu, semua, HARI)


def test_kirim_batal():
    assert K("cancelled") == ("—", "muted")


def test_putusan2_tanpa_baris_perlu_kirim_strip():
    assert K(perlu=False, d=date(2026, 9, 26)) == ("—", "muted")  # dulu "26 Sep, telat" tebal (SO-0024/0025)


def test_putusan2_semua_terkirim():
    assert K(semua=True, d=date(2026, 9, 1)) == ("Terkirim", "muted")


def test_kirim_tanpa_tanggal():
    assert K(d=None) == ("—", "muted")


def test_kirim_hari_ini_tebal():
    assert K(d=date(2026, 10, 1)) == ("Hari ini", "strong")


def test_kirim_besok():
    assert K(d=date(2026, 10, 2)) == ("Besok", "normal")


def test_kirim_telat_tebal():
    assert K(d=date(2026, 9, 28)) == ("28 Sep, telat", "strong")


def test_kirim_nanti():
    assert K(d=date(2026, 10, 21)) == ("21 Okt", "normal")
    assert K(d=date(2026, 12, 3)) == ("3 Des", "normal")


def test_kirim_besok_lintas_bulan():
    assert kirim("confirmed", date(2026, 11, 1), True, False, date(2026, 10, 31)) == ("Besok", "normal")


@pytest.mark.parametrize("bulan,teks", [(1, "Jan"), (5, "Mei"), (8, "Agu"), (10, "Okt"), (12, "Des")])
def test_nama_bulan_indonesia(bulan, teks):
    assert K(d=date(2027, bulan, 9))[0] == f"9 {teks}"
