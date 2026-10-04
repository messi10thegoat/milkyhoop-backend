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
    "dp": {"draft": "Draf", "posted": "Diterima", "partial": "Sebagian terpakai", "applied": "Terpakai",
           "refunded": "Dikembalikan", "void": "Batal"},
}

_DOKUMEN = {"so": "Pesanan", "si": "Faktur", "quote": "Penawaran", "proforma": "Proforma",
            "rp": "Penerimaan", "dp": "Uang muka"}

_PERIODE = {"CLOSED": "ditutup", "LOCKED": "dikunci"}


def status_id(jenis: str, s) -> str:
    """Label layar untuk status server; status tak dikenal dikembalikan apa adanya (jangan menebak)."""
    kunci = str(s or "").strip().lower()
    return _STATUS[jenis].get(kunci, str(s or ""))


def tak_bisa_status(jenis: str, s, aksi: str, nomor: str | None = None) -> str:
    """'Penawaran QUO-2610-0001 berstatus Diterima — tidak bisa dikirim.'"""
    dok = _DOKUMEN[jenis] + (f" {nomor}" if nomor else "")
    return f"{dok} berstatus {status_id(jenis, s)} — tidak bisa {aksi}."


def periode_tertutup(nama, status) -> str:
    """'Periode Okt 2026 sudah ditutup. Buka kembali di Akuntansi › Tutup Buku, atau ubah tanggalnya.'"""
    kata = _PERIODE.get(str(status or "").upper(), "ditutup")
    awal = f"Periode {nama}" if nama else "Periode akuntansi"
    return f"{awal} sudah {kata}. Buka kembali di Akuntansi › Tutup Buku, atau ubah tanggalnya."
