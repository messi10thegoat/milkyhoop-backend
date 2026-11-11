"""laporan-fix-2 (b) + periode: pembulatan PER BARIS, total = Σ tampil (pola Accurate, putusan MASTER 12 Nov), SATU fungsi untuk L/R, Neraca,
Arus Kas (report_engine.footing); rentang periode satu formatter (teks_galat.rentang_tanggal). Bukti TESTER nyawit: HPP 19.085.162 − 1.202.136 +
14.052, total tampil 17.897.079 != Σ tampil 17.897.078."""
import inspect
from decimal import Decimal as D

import pytest

from app.routers import psak_reports as PR
from app.services import teks_galat as tg
from app.services.report_engine import baris as B
from app.services.report_engine import footing as F


@pytest.mark.parametrize("a,b,harap", [
    ("2026-11-01", "2026-11-12", "1 Nov – 12 Nov 2026"),
    ("2025-12-01", "2026-01-12", "1 Des 2025 – 12 Jan 2026"),
    ("2026-11-12", "2026-11-12", "12 Nov 2026"),
    ("2026-01-01", "2026-12-31", "1 Jan – 31 Des 2026"),
    ("2026-08-05", "2026-09-30", "5 Agu – 30 Sep 2026"),
])
def test_rentang_tanggal(a, b, harap):
    assert tg.rentang_tanggal(a, b) == harap and B.periode_rentang(a, b) == harap
    assert "–" in harap or a == b and "–" not in harap  # en dash (bukan hubung), berspasi


def akun(nama, x, ked=1):
    return {"jenis": "akun", "kunci": "akun", "label": nama, "nilai": B.nilai(x), "tanda": B.tanda(x), "kedalaman": ked}


def baris_hpp():
    """Blok HPP versi TESTER: baris mentah bersen; total_hpp dihitung engine dari jumlah MENTAH."""
    a, b, c = D("19085162.4"), D("-1202136.3"), D("14052.4")
    return [B.judul("hpp"), akun("HPP Barang", a), akun("Retur Pembelian", b), akun("Selisih Stok", c), B.angka("total", "total_hpp", a + b + c)]


def test_bukti_tester_total_dibulatkan_dari_jumlah_mentah_tak_sama_sigma_tampil():
    r = baris_hpp()
    assert tg.angka_laporan(r[-1]["nilai"], 0) == "17.897.079"       # perilaku LAMA: total dibulatkan dari jumlah bersen
    F.footing("laba_rugi", r, 0)
    tampil = [int(Decimal) for Decimal in (x["nilai"].split(".")[0] for x in r[1:4])]
    assert tampil == [19085162, -1202136, 14052] and r[-1]["nilai"] == "17897078.00" and sum(tampil) == 17897078
    assert tg.angka_laporan(r[-1]["nilai"], 0) == "17.897.078"       # total == Σ baris yang dilihat pengguna


def test_footing_rantai_laba_rugi_semua_jumlah_dari_tampil():
    r = [B.judul("pendapatan"), akun("Penjualan", D("1000.5")), akun("Jasa", D("500.5")), B.angka("total", "total_pendapatan", D("1501")),
         B.judul("hpp"), akun("HPP", D("400.5")), B.angka("total", "total_hpp", D("400.5")), B.angka("hasil", "laba_kotor", D("1100.5")),
         B.judul("beban_usaha"), akun("Gaji", D("200.5")), akun("Sewa", D("99.5")), B.angka("total", "total_beban_usaha", D("300")),
         B.angka("hasil", "laba_usaha", D("800.5")),
         B.judul("lain"), akun("Bunga", D("50.5")), akun("Admin Bank", D("-10.5")), B.angka("total", "total_lain", D("40")),
         B.angka("hasil", "laba_sebelum_pajak", D("840.5")), B.angka("info", "beban_pajak", D("0")), B.angka("hasil", "laba_bersih", D("840.5"))]
    F.footing("laba_rugi", r, 0)
    n = {x["kunci"]: D(x["nilai"]) for x in r if x["jenis"] not in ("akun", "judul")}
    pend, hpp, usaha, lain = 1001 + 501, 401, 201 + 100, 51 - 10   # HALF_UP: .5 ke atas ("-10.5" -> -11)
    assert n["total_pendapatan"] == 1502 and n["total_hpp"] == 401 and n["laba_kotor"] == 1101 and n["total_beban_usaha"] == 301
    assert n["laba_usaha"] == 800 and n["total_lain"] == 51 - 11 and n["laba_sebelum_pajak"] == 800 + 40 and n["laba_bersih"] == 840
    assert [pend, hpp, usaha, lain][0] == 1502  # (sanity aturan HALF_UP baris)


def test_footing_presisi_sen_dan_tanda_dan_label_kas_neto():
    r = [B.judul("operasi"), akun("Penerimaan", D("100.126")), akun("Pembayaran", D("-200.004")), B.angka("total", "kas_neto_operasi", D("-99.878"), label="x"),
         B.angka("hasil", "kenaikan_kas", D("-99.878")), B.angka("info", "kas_awal", D("1000.4")), B.angka("hasil", "kas_akhir", D("900.52"))]
    F.footing("arus_kas", r, 2)
    n = {x["kunci"]: x for x in r if x["jenis"] not in ("akun", "judul")}
    assert n["kas_neto_operasi"]["nilai"] == "-99.87" and n["kenaikan_kas"]["nilai"] == "-99.87" and n["kas_neto_operasi"]["tanda"] == -1
    assert n["kas_neto_operasi"]["label"] == "Kas neto yang digunakan untuk aktivitas operasi"
    assert (n["kas_awal"]["nilai"], n["kas_akhir"]["nilai"]) == ("1000.40", "900.52")   # saldo: hanya dibulatkan, bukan dijumlah ulang
    r2 = [B.judul("operasi"), akun("A", D("0.4")), akun("B", D("0.4")), B.angka("total", "kas_neto_operasi", D("0.8"), label="x"), B.angka("hasil", "kenaikan_kas", D("0.8"))]
    F.footing("arus_kas", r2, 0)    # 0,4 + 0,4 -> 0 + 0 (bukan 1): total = Σ tampil; label ikut tanda sesudah pembulatan
    assert r2[3]["nilai"] == "0.00" and r2[3]["label"] == "Kas neto yang diperoleh dari aktivitas operasi"


def test_footing_neraca_total_dari_baris_tampil():
    r = [B.judul("aset"), B.judul("aset_lancar", 1), akun("Kas", D("100.5"), 2), akun("Piutang", D("200.5"), 2),
         B.angka("total", "total_aset_lancar", D("301"), 1), B.judul("aset_tidak_lancar", 1), akun("Mesin", D("1000.5"), 2),
         akun("Akum", D("-100.5"), 2), B.angka("total", "total_aset_tidak_lancar", D("900"), 1), B.angka("hasil", "total_aset", D("1201")),
         B.judul("liabilitas_ekuitas"), B.judul("liabilitas_jp", 1), akun("Utang", D("150.5"), 2), B.angka("total", "total_liabilitas_jp", D("150.5"), 1),
         B.judul("liabilitas_jpp", 1), B.angka("total", "total_liabilitas_jpp", D("0"), 1), B.angka("total", "total_liabilitas", D("150.5")),
         B.judul("ekuitas", 1), akun("Modal", D("900.5"), 2), akun("Laba Tahun Berjalan", D("150.5"), 2),
         B.angka("total", "total_ekuitas", D("1051"), 1), B.angka("hasil", "total_liabilitas_ekuitas", D("1201.5"))]
    F.footing("neraca", r, 0)
    n = {x["kunci"]: D(x["nilai"]) for x in r if x["jenis"] not in ("akun", "judul")}
    assert n["total_aset_lancar"] == 101 + 201 and n["total_aset_tidak_lancar"] == 1001 - 101 and n["total_aset"] == 302 + 900  # -100,5 HALF_UP -> -101
    assert n["total_liabilitas"] == 151 and n["total_ekuitas"] == 901 + 151 and n["total_liabilitas_ekuitas"] == 151 + 1052


def test_jenis_kosong_tak_menyentuh_baris_dan_rute_memakai_footing():
    lap = {"baris": [{"nilai": "-5000.00"}, {"nilai": None}]}
    assert PR._tampil(lap, 0)["baris"][0]["nilai_tampil"] == "(5.000)" and "kunci" not in lap["baris"][0]
    src = inspect.getsource(PR.get_financial_statements)
    for n, v, j in (("balance_sheet", "bs", "neraca"), ("income_statement", "pl", "laba_rugi"), ("cash_flow", "cf", "arus_kas")):
        assert f'"{n}": _tampil({v}, presisi, "{j}")' in src


def test_arus_kas_label_liabilitas_nama_akun_bukan_pembayaran_umum():
    """laporan-fix-2 (c): DP pelanggan (LIABILITY, uang MASUK) = baris sendiri "Uang Muka Pelanggan", bukan "Pembayaran liabilitas"; piutang tak tercampur."""
    from app.services.report_engine import cash_flow as CF
    assert CF._get_label("LIABILITY", None, "Uang Muka Pelanggan") == "Uang Muka Pelanggan"
    assert CF._get_label("LIABILITY", None) == "Pembayaran liabilitas"                       # tanpa nama: label lama (tak pernah kosong)
    assert CF._get_label("RECEIVABLE", "TRADE_RECEIVABLE", "Piutang Usaha") == "Penerimaan piutang usaha"   # piutang tetap satu baris sendiri
    assert CF._get_label("PAYABLE", "TRADE_PAYABLE", "Utang Usaha") == "Pembayaran utang usaha"
    assert CF._get_label("ASSET", None, "Uang Muka Pembelian") == "Uang Muka Pembelian"
    src = inspect.getsource(CF.generate_cash_flow)
    assert src.count("account_type IN ('ASSET', 'LIABILITY') THEN account_name") == 2  # SELECT dan GROUP BY satu ekspresi
