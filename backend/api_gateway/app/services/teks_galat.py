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


def satuan_tak_terkonversi(satuan, nama, dasar) -> str:
    """QTY-DASAR: satuan dokumen tanpa konversi ke satuan dasar barang (dulu dianggap 1:1 diam -> stok & HPP salah)."""
    return f"Satuan {satuan} untuk {nama} belum punya konversi ke {dasar}. Atur konversinya di Barang › Satuan."


def satuan_belum_didukung(aksi, satuan, dasar) -> str:
    """QTY-DASAR R1 (kalimat MASTER): jalur KELUAR menolak satuan selain dasar sampai konversinya terpasang (R2)."""
    return f"{aksi} dengan satuan {satuan} belum didukung; pakai {dasar}."


def stok_kurang_satuan(nama, gudang, tersedia, dasar, jumlah, satuan, jumlah_dasar) -> str:
    """QTY-DASAR R2 (kalimat OK MASTER): stok kurang untuk baris bersatuan BUKAN dasar -- angka dalam dasar + padanannya."""
    return (f"Stok {nama} di {gudang or 'gudang'} hanya {qty(tersedia)} {dasar}, diminta {qty(jumlah)} {satuan} "
            f"(= {qty(jumlah_dasar)} {dasar}).")
