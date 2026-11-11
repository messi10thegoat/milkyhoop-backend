"""Baris TAMPIL laporan keuangan PSAK dari server (gelombang 4 L1, 10 Nov 2026; kontrak KONTRAK-laporan-teks-server-rupiah-20261110.md).

SATU peta label (dulu dirakit FE per layar: judul seksi, Total…, Laba Kotor…, "Untuk Periode yang Berakhir…"). Tiap laporan menambah
(aditif; medan lama float tetap utuh):
  judul, periode_text, satuan_text[, basis_text], baris[] = {jenis, kunci, label, nilai, tanda, kedalaman[, account_id, account_code]}
jenis: judul (kepala seksi) | akun | total (total seksi) | hasil (laba kotor/usaha/bersih, total aset, …) | info (baris tanpa seksi).
nilai = string desimal 2 angka (Decimal; nilai SAJIAN: tanda sudah menurut penyajian PSAK, mis. pendapatan positif, akumulasi penyusutan
negatif); tanda = -1/0/1; judul -> nilai None. Teks angka (nilai_tampil) ditambahkan rute lewat teks_galat.angka_laporan (SATU pemformat).
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from .. import teks_galat as tg

SEN = Decimal("0.01")
SATUAN_TEXT = "(Disajikan dalam Rupiah)"
BASIS_TEXT = {"accrual": "Basis Akrual", "cash": "Basis Kas"}

# ---- SATU peta label
L = {
    # Laba Rugi
    "pendapatan": "PENDAPATAN", "total_pendapatan": "Total Pendapatan",
    "hpp": "HARGA POKOK PENJUALAN", "total_hpp": "Total Harga Pokok Penjualan", "laba_kotor": "Laba Kotor",
    "beban_usaha": "BEBAN USAHA", "total_beban_usaha": "Total Beban Usaha", "laba_usaha": "Laba Usaha",
    "lain": "PENDAPATAN (BEBAN) LAIN-LAIN", "total_lain": "Total Pendapatan (Beban) Lain-lain",
    "laba_sebelum_pajak": "Laba Sebelum Pajak", "beban_pajak": "Beban Pajak Penghasilan", "laba_bersih": "Laba Bersih",
    # Neraca
    "aset": "ASET", "aset_lancar": "Aset Lancar", "total_aset_lancar": "Total Aset Lancar",
    "aset_tidak_lancar": "Aset Tidak Lancar", "total_aset_tidak_lancar": "Total Aset Tidak Lancar", "total_aset": "Total Aset",
    "liabilitas_ekuitas": "LIABILITAS DAN EKUITAS",
    "liabilitas_jp": "Liabilitas Jangka Pendek", "total_liabilitas_jp": "Total Liabilitas Jangka Pendek",
    "liabilitas_jpp": "Liabilitas Jangka Panjang", "total_liabilitas_jpp": "Total Liabilitas Jangka Panjang",
    "total_liabilitas": "Total Liabilitas",
    "ekuitas": "Ekuitas", "laba_periode": "Laba Tahun Berjalan", "laba_ditahan_lalu": "Laba Ditahan",
    "total_ekuitas": "Total Ekuitas",
    "total_liabilitas_ekuitas": "Total Liabilitas dan Ekuitas",
    # Arus Kas
    "operasi": "ARUS KAS DARI AKTIVITAS OPERASI", "investasi": "ARUS KAS DARI AKTIVITAS INVESTASI",
    "pendanaan": "ARUS KAS DARI AKTIVITAS PENDANAAN",
    "kenaikan_kas": "Kenaikan (Penurunan) Neto Kas dan Bank", "kas_awal": "Kas dan Bank Awal Periode",
    "kas_akhir": "Kas dan Bank Akhir Periode",
}
JUDUL = {"laba_rugi": "LAPORAN LABA RUGI", "neraca": "LAPORAN POSISI KEUANGAN", "arus_kas": "LAPORAN ARUS KAS"}
AKTIVITAS = {"operasi": "aktivitas operasi", "investasi": "aktivitas investasi", "pendanaan": "aktivitas pendanaan"}


def nilai(x) -> str:
    return str(Decimal(x).quantize(SEN, rounding=ROUND_HALF_UP))


def tanda(x) -> int:
    d = Decimal(x).quantize(SEN, rounding=ROUND_HALF_UP)
    return (d > 0) - (d < 0)


def judul(kunci: str, kedalaman: int = 0) -> dict:
    return {"jenis": "judul", "kunci": kunci, "label": L[kunci], "nilai": None, "tanda": 0, "kedalaman": kedalaman}


def angka(jenis: str, kunci: str, x, kedalaman: int = 0, label: Optional[str] = None) -> dict:
    return {"jenis": jenis, "kunci": kunci, "label": label or L[kunci], "nilai": nilai(x), "tanda": tanda(x), "kedalaman": kedalaman}


def akun(d: dict, kedalaman: int, negasi: bool = False) -> dict:
    """d = baris compute_balance_detail {account_id, account_code, account_name, balance (Decimal mentah debit-kredit)}."""
    x = -d["balance"] if negasi else d["balance"]
    return {"jenis": "akun", "kunci": "akun", "label": d["account_name"], "nilai": nilai(x), "tanda": tanda(x), "kedalaman": kedalaman,
            "account_id": str(d["account_id"]) if d.get("account_id") else None, "account_code": d["account_code"]}


def kas_neto_label(kunci: str, total) -> str:
    """'Kas neto yang diperoleh dari aktivitas operasi' (>= 0) / '… digunakan untuk …' (< 0) -- dulu FE selalu 'digunakan untuk'."""
    return f"Kas neto yang {'diperoleh dari' if Decimal(total) >= 0 else 'digunakan untuk'} {AKTIVITAS[kunci]}"


def periode_berakhir(akhir) -> str:
    return f"Untuk Periode yang Berakhir {tg.tanggal_panjang(akhir)}"


def per_tanggal(t) -> str:
    return f"Per Tanggal {tg.tanggal_panjang(t)}"


# ---- SATU peta tipe akun + saldo normal (L3: Neraca Saldo, Buku Besar; dulu FE: "Kewajiban", saldo normal dari TIPE)
TIPE_AKUN = {
    "ASSET": "Aset", "RECEIVABLE": "Piutang", "LIABILITY": "Liabilitas", "PAYABLE": "Utang", "EQUITY": "Ekuitas",
    "REVENUE": "Pendapatan", "INCOME": "Pendapatan", "COGS": "Harga Pokok Penjualan", "EXPENSE": "Beban",
    "OTHER_INCOME": "Pendapatan Lain-lain", "OTHER_EXPENSE": "Beban Lain-lain",
}
SALDO_NORMAL = {"DEBIT": "Debit", "CREDIT": "Kredit"}


def tipe_label(tipe) -> str:
    """Kode tipe CoA (huruf besar/kecil) -> label; tipe tak dikenal tampil apa adanya (tak pernah kosong diam)."""
    return TIPE_AKUN.get(str(tipe or "").upper(), str(tipe or ""))


def saldo_normal_label(normal) -> str:
    """Saldo normal dari CoA (bukan diturunkan dari tipe: akun kontra mis. akumulasi penyusutan = ASSET/CREDIT)."""
    return SALDO_NORMAL.get(str(normal or "").upper(), "")
