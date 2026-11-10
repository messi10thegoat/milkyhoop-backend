"""Teks galat Penjualan berbahasa Indonesia — SATU sumber untuk pesan 4xx yang tampil di CW.

FE CW menampilkan `detail` (string) atau `detail.message` (dict) APA ADANYA, jadi teks di sini = teks di layar.
Aturan (MASTER 4 Okt 2026):
- Rupiah baku "Rp 1.250.000"; sen hanya bila != 0 ("Rp 1.250,50"). Decimal + ROUND_HALF_UP, tanpa float (Law 25/9).
- Label status = label status di layar CW (resep FE recipes/*.ts: STATUS_TAMPIL/statusFaktur/statusPenawaran/
  statusProforma/statusPenerimaan/statusUangMuka + LABEL_* Draft->Draf). Ubah di sini = cocokkan resep FE.
- Hanya TEKS; status_code dan `code` tetap milik pemanggil.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_SEN = Decimal("0.01")


def _desimal(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x if x is not None else 0))
    except (InvalidOperation, ValueError):
        raise ValueError(f"bukan angka: {x!r}")


def _ribuan(bulat: int) -> str:
    return f"{bulat:,}".replace(",", ".")


def rp(x) -> str:
    """Rupiah untuk pesan: 'Rp 1.250.000' / 'Rp 1.250,50' / 'Rp -5.000'."""
    d = _desimal(x).quantize(_SEN, rounding=ROUND_HALF_UP)
    tanda = "-" if d < 0 else ""
    d = abs(d)
    bulat = int(d)
    sen = int((d - bulat) * 100)
    teks = _ribuan(bulat) + (f",{sen:02d}" if sen else "")
    return f"Rp {tanda}{teks}"


# ---------------------------------------------------------------- angka LAPORAN keuangan (gelombang 4 L1, 10 Nov 2026)
# SATU pemformat tampilan angka laporan. GAYA diputus belakangan (putusan MASTER: ikut tampilan Accurate yang diukur TESTER); sampai itu
# "akuntansi" = gaya tabel laporan hari ini (FE fmtPSAK): tanpa "Rp", ribuan titik, negatif dalam kurung. Presisi = tenant (0 bawaan,
# 2 bila uang_bersen; services/uang_presisi). Nilai MENTAH (string desimal + tanda) tetap dikirim server di samping teks ini.
GAYA_ANGKA_LAPORAN = "akuntansi"  # "akuntansi" | "rp"


def angka_laporan(x, presisi: int = 0, gaya: str | None = None) -> str:
    """'1.250.000' / '(5.000)' (akuntansi) atau 'Rp 1.250.000' / 'Rp -5.000' (rp); presisi 2 -> ',50' hanya bila sen != 0. Decimal."""
    gaya = gaya or GAYA_ANGKA_LAPORAN
    d = _desimal(x).quantize(Decimal(1) if presisi == 0 else _SEN, rounding=ROUND_HALF_UP)
    negatif = d < 0
    d = abs(d)
    bulat = int(d)
    sen = int((d - bulat) * 100)
    teks = _ribuan(bulat) + (f",{sen:02d}" if sen else "")
    if gaya == "rp":
        return f"Rp {'-' if negatif else ''}{teks}"
    return f"({teks})" if negatif else teks


BULAN_PANJANG = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November",
                 "Desember")


def tanggal_pendek(t) -> str:
    """date / 'YYYY-MM-DD' -> '12 Okt 2026' (bulan = 3 huruf pertama BULAN_PANJANG; tanpa larik bulan baru)."""
    from datetime import date as _date
    d = t if isinstance(t, _date) else _date.fromisoformat(str(t)[:10])
    return f"{d.day} {BULAN_PANJANG[d.month - 1][:3]} {d.year}"


def tanggal_panjang(t) -> str:
    """date / 'YYYY-MM-DD' -> '31 Oktober 2026' (judul periode laporan)."""
    from datetime import date as _date
    d = t if isinstance(t, _date) else _date.fromisoformat(str(t)[:10])
    return f"{d.day} {BULAN_PANJANG[d.month - 1]} {d.year}"


def penerapan_melebihi_sisa(jumlah, sisa, label: str = "uang muka") -> str:
    """Satu pola DP Penjualan + Uang Muka Vendor (U-kode, 2 Nov 2026). label Penjualan = label_deposit (uang muka/pelunasan)."""
    return f"Nilai penerapan {rp(jumlah)} melebihi sisa {label} {rp(sisa)}."


def pengembalian_melebihi_sisa(jumlah, sisa, label: str = "uang muka") -> str:
    return f"Pengembalian {rp(jumlah)} melebihi sisa {label} {rp(sisa)}."


def penerapan_melebihi_sisa_tagihan(jumlah, sisa) -> str:
    return f"Nilai penerapan {rp(jumlah)} melebihi sisa tagihan faktur {rp(sisa)}."


def qty(x) -> str:
    """Kuantitas: '5' / '2,5' / '1.250' (tanpa nol ekor, koma desimal, titik ribuan)."""
    d = _desimal(x)
    if d == d.to_integral_value():
        return ("-" if d < 0 else "") + _ribuan(abs(int(d)))
    tanda = "-" if d < 0 else ""
    d = abs(d).normalize()
    bulat, _, pecahan = format(d, "f").partition(".")
    return f"{tanda}{_ribuan(int(bulat))},{pecahan}"


# Label status di layar CW per jenis dokumen (lihat docstring modul untuk sumber FE).
_STATUS = {
    "so": {"draft": "Draf", "confirmed": "Dikonfirmasi", "partial_shipped": "Dikonfirmasi", "shipped": "Dikirim",
           "partial_invoiced": "Ditagih", "invoiced": "Ditagih", "completed": "Selesai", "cancelled": "Batal"},
    "si": {"draft": "Draf", "posted": "Belum lunas", "sent": "Belum lunas", "unpaid": "Belum lunas",
           "partial": "Sebagian", "partially_paid": "Sebagian", "paid": "Lunas", "fully_paid": "Lunas",
           "overdue": "Jatuh tempo", "void": "Batal", "voided": "Batal"},
    "quote": {"draft": "Draf", "sent": "Terkirim", "viewed": "Terkirim", "accepted": "Diterima",
              "declined": "Ditolak", "expired": "Kedaluwarsa", "converted": "Dikonversi", "void": "Batal"},
    "proforma": {"draft": "Draf", "issued": "Terbit", "expired": "Kedaluwarsa", "cancelled": "Batal", "void": "Batal"},
    "rp": {"draft": "Draf", "posted": "Diterima", "void": "Batal", "voided": "Batal", "cancelled": "Batal"},
    # Label tunggal uang muka = putusan MASTER 4 Okt (FE menyatukan konstantanya di rilis U3a).
    # Nota kredit: DISETUJUI MASTER 5 Okt (FE: recipes/statusNotaKredit.ts, satu konstanta bersama).
    "cn": {"draft": "Draf", "posted": "Terbit", "partial": "Sebagian terpakai", "applied": "Terpakai", "void": "Batal",
           "partially_refunded": "Sebagian dikembalikan", "refunded": "Dikembalikan"},
    "dp": {"draft": "Draf", "posted": "Diterima", "partial": "Sebagian terpakai", "applied": "Terpakai",
           "partially_refunded": "Sebagian dikembalikan", "refunded": "Dikembalikan", "void": "Batal"},
    # Nota debit (U3c 23 Okt 2026): kata SAMA dengan nota kredit (kembar Penjualan<->Pembelian)
    "vc": {"draft": "Draf", "posted": "Terbit", "partial": "Sebagian terpakai", "applied": "Terpakai", "void": "Batal",
           "partially_refunded": "Sebagian dikembalikan", "refunded": "Dikembalikan"},
    # 6a Pembelian (25 Okt 2026): label == FE recipes/bill.ts STATUS_TAGIHAN dan billPayment.ts STATUS_BAYAR_KELUAR (kembar si / rp)
    "bill": {"draft": "Draf", "posted": "Belum lunas", "unpaid": "Belum lunas", "partial": "Sebagian", "paid": "Lunas",
             "overdue": "Jatuh tempo", "void": "Batal", "voided": "Batal"},
    "bp": {"draft": "Draf", "posted": "Dibayar", "void": "Batal", "voided": "Batal"},
}

_DOKUMEN = {"so": "Pesanan", "si": "Faktur", "quote": "Penawaran", "proforma": "Proforma",
            "rp": "Penerimaan", "dp": "Uang muka", "cn": "Nota kredit", "vc": "Nota debit",
            "bill": "Faktur pembelian", "bp": "Pembayaran"}

_PERIODE = {"CLOSED": "ditutup", "LOCKED": "dikunci"}

# Label layar alasan nota kredit (credit_notes.reason) -- reason_label NK + info dokumen terkait SO (5 Okt 2026).
ALASAN_NK = {"return": "Retur", "pricing_error": "Salah harga", "discount": "Diskon", "damaged": "Rusak",
             "other": "Lainnya"}


def status_id(jenis: str, s) -> str:
    """Label layar untuk status server; status tak dikenal dikembalikan apa adanya (jangan menebak)."""
    kunci = str(s or "").strip().lower()
    return _STATUS[jenis].get(kunci, str(s or ""))


def tak_bisa_status(jenis: str, s, aksi: str, nomor: str | None = None) -> str:
    """'Penawaran QUO-2610-0001 berstatus Diterima — tidak bisa dikirim.'"""
    dok = _DOKUMEN[jenis] + (f" {nomor}" if nomor else "")
    return f"{dok} berstatus {status_id(jenis, s)} — tidak bisa {aksi}."


def kata_periode(status) -> str:
    """'CLOSED' -> 'ditutup', 'LOCKED' -> 'dikunci' (status periode TAK pernah tampil mentah)."""
    return _PERIODE.get(str(status or "").upper(), "ditutup")


def diskon_baris_melebihi(diskon, nilai) -> str:
    """U-SI-LAW (putusan pemilik LANGSUNG 2 Nov "B1 Tolak", pola NetSuite/SAP): diskon baris tak boleh melebihi nilai baris --
    SATU kalimat untuk semua pintu dokumen (penjualan lewat sales_doc_calc.line_net, PO lewat po_hitung.hitung_baris)."""
    return f"Diskon baris {rp(diskon)} melebihi nilai baris {rp(nilai)}."


def sudah_dipakai(pihak: str, medan: str, nilai, aktif: bool = True) -> str:
    """Kalimat bentrok master data, SATU untuk pelanggan dan vendor (3 Nov 2026):
    aktif   : 'Nama vendor "PT A" sudah dipakai vendor lain. Gunakan nama lain.'
    nonaktif: 'Nama vendor "PT A" sudah dipakai vendor nonaktif — aktifkan lagi dari Kontak atau gunakan nama lain.'"""
    awal = f'{medan.capitalize()} {pihak} "{nilai}" sudah dipakai {pihak}'
    if aktif:
        return f"{awal} lain. Gunakan {medan} lain."
    return f"{awal} nonaktif — aktifkan lagi dari Kontak atau gunakan {medan} lain."


def faktur_nol(jenis: str) -> str:
    """'Faktur penjualan bernilai Rp 0 tidak bisa diterbitkan. Isi harga atau hapus diskon.' (SI-FPB-RP0, satu kalimat SI + FPB)"""
    dok = {"si": "Faktur penjualan", "bill": "Faktur pembelian"}[jenis]
    return f"{dok} bernilai Rp 0 tidak bisa diterbitkan. Isi harga atau hapus diskon."


def nd_barang_belum_masuk(nama, nomor_tagihan) -> str:
    """ND-GUDANG (pola NK 'belum pernah dikirim lewat faktur'): retur barang yang tak pernah masuk lewat tagihan asal."""
    return (f"Barang {nama} belum pernah masuk lewat tagihan {nomor_tagihan or ''}".rstrip()
            + ", jadi tidak bisa diretur ke vendor.")


def nd_retur_melebihi(nama, jumlah, sisa) -> str:
    """ND-GUDANG (pola NK 'melebihi yang terkirim dan belum diretur')."""
    return f"Retur {nama} {qty(jumlah)} melebihi yang masuk dan belum diretur ({qty(sisa)})."


def nd_stok_kurang(nama, gudang, stok, jumlah) -> str:
    """ND-GUDANG: stok gudang asal tak cukup (dulu 500 chk_ws_quantity)."""
    return f"Stok {nama} di {gudang or 'gudang'} tinggal {qty(stok)}; retur {qty(jumlah)} tidak bisa diposting."


def periode_tertutup(nama, status) -> str:
    """'Periode Okt 2026 sudah ditutup. Buka kembali di Akuntansi › Tutup Buku, atau ubah tanggalnya.'"""
    kata = _PERIODE.get(str(status or "").upper(), "ditutup")
    awal = f"Periode {nama}" if nama else "Periode akuntansi"
    return f"{awal} sudah {kata}. Buka kembali di Akuntansi › Tutup Buku, atau ubah tanggalnya."


def konversi_terpakai(satuan) -> str:
    """P1 (pola NetSuite): konversi yang sudah dipakai transaksi tak boleh diubah / dihapus."""
    return f"Konversi {satuan} sudah dipakai transaksi. Buat satuan baru."


def satuan_dasar_terpakai(dasar) -> str:
    """Satuan dasar barang yang sudah punya riwayat stok/dokumen tak boleh diganti (pola NetSuite)."""
    return f"Satuan dasar {dasar} sudah dipakai transaksi. Buat barang baru untuk satuan lain."


def satuan_tak_terkonversi(satuan, nama, dasar) -> str:
    """QTY-DASAR: satuan dokumen tanpa konversi ke satuan dasar barang (dulu dianggap 1:1 diam -> stok & HPP salah)."""
    return f"Satuan {satuan} untuk {nama} belum punya konversi ke {dasar}. Atur konversinya di Barang › Satuan."


def satuan_tidak_habis(qty, satuan, nama, satuan_baris, faktor_baris, dasar) -> str:
    """BUG-085: qty ketikan tak bisa dinyatakan persis (4 desimal) dalam satuan baris dokumen asal."""
    return (f"{qty.normalize():f} {satuan} {nama} tidak pas dalam {satuan_baris} (1 {satuan_baris} = {faktor_baris.normalize():f} {dasar}). "
            f"Isi jumlah yang pas dalam {satuan_baris}.")


def satuan_belum_didukung(aksi, satuan, dasar) -> str:
    """QTY-DASAR R1 (kalimat MASTER): jalur KELUAR menolak satuan selain dasar sampai konversinya terpasang (R2)."""
    return f"{aksi} dengan satuan {satuan} belum didukung; pakai {dasar}."


def _qty2(x) -> str:
    """Isyarat stok: kuantitas dalam satuan baris boleh pecahan (187 botol = 15,58 dus) -> 2 desimal, tanpa nol buntut."""
    return qty(_desimal(x).quantize(Decimal("0.01")))


def stok_per_tanggal(nama, tanggal, stok_tanggal, stok_kini, satuan) -> str:
    """BUG-075 (pola Accurate "Stok : 0" oranye): transaksi bertanggal mundur melebihi stok pada tanggal dokumen. ISYARAT, bukan tolak."""
    from .tanggal_uang_muka import tgl
    return f"Stok {nama} per {tgl(tanggal)}: {_qty2(stok_tanggal)} {satuan} (hari ini {_qty2(stok_kini)})."


def stok_bisa_dijual(nama, bisa, dipesan, diminta, satuan) -> str:
    """BUG-049: bisa dijual = stok kini - dipesan pesanan terbuka. ISYARAT, bukan tolak."""
    return f"Bisa dijual {nama}: {_qty2(bisa)} {satuan} (dipesan {_qty2(dipesan)}), diminta {_qty2(diminta)} {satuan}."


def stok_so_tanpa_gudang(n) -> str:
    return f"{n} pesanan tanpa gudang tidak dihitung di gudang ini; lihat semua gudang."


def stok_kirim_tanpa_tautan() -> str:
    return "Sebagian pengiriman pesanan tidak bertaut ke baris pesanan; jumlah dipesan bisa lebih besar dari sebenarnya."


def barang_tidak_ditemukan() -> str:
    return "Barang tidak ditemukan di usaha ini."


def stok_kurang(nama, gudang, tersedia, jumlah, satuan=None) -> str:
    """terbit-pratinjau-1: stok kurang untuk baris bersatuan DASAR (atau tanpa satuan) -- satu kalimat penulis faktur, pratinjau terbit, SJ."""
    s = f" {satuan}" if satuan else ""
    return f"Stok {nama} di {gudang or 'gudang'} hanya {qty(tersedia)}{s}, diminta {qty(jumlah)}{s}."


def stok_kurang_satuan(nama, gudang, tersedia, dasar, jumlah, satuan, jumlah_dasar) -> str:
    """QTY-DASAR R2 (kalimat OK MASTER): stok kurang untuk baris bersatuan BUKAN dasar -- angka dalam dasar + padanannya."""
    return (f"Stok {nama} di {gudang or 'gudang'} hanya {qty(tersedia)} {dasar}, diminta {qty(jumlah)} {satuan} "
            f"(= {qty(jumlah_dasar)} {dasar}).")
