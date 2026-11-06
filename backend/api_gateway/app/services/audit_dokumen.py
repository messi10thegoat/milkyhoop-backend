"""AUDIT-DOKUMEN (1 Nov 2026, GO MASTER): SATU penentu jejak audit siklus hidup dokumen Pembelian + posting Faktur Penjualan (Law 12).

Dulu jalur tunggal posting/void/hapus FPB, PK, UMP, ND dan kirim/batal/tutup PO tak menulis audit_logs (hanya massal B1 menulis BULK_*);
grapgrap 18 FPB + 18 PK posted = 0 baris audit Pembelian. Kini tiap aksi menulis SATU baris lewat catat_dokumen di transaksi pemanggil
(so_riwayat.catat_riwayat: gagal catat = aksi batal, bukan jejak yang hilang diam).

Aturan:
- EVENT = SATU-SATUNYA sumber nama event Pembelian (uji: tak ada string event Pembelian di luar tabel ini). Event lama (PO_UPDATED,
  PO_RECEIVED, VD_VOIDED, VD_UNAPPLIED, GRN_DIBATALKAN) ikut di tabel; pemanggil lamanya mengambil nama dari sini (isi baris tak berubah).
- Metadata baris baru HANYA {jenis, aksi, ringkas, user_id}: siapa (userId), apa (eventType), kapan (createdAt), nomor (entity_number).
  Tanpa nominal/baris/data pihak (putusan MASTER).
- riwayat=False: jejak audit saja, TIDAK digabung ke riwayat dokumen (so_riwayat._selesaikan). Kejadian itu sudah tampil dari kolom
  beraktor (posted_by/sent_by/voided_by/...) -- tanpa ini riwayat FE mendapat baris GANDA dengan jenis yang belum dikenal FE.
- Hapus FPB / PO / Faktur Penjualan TIDAK di sini: trigger trg_log_deletion (V230) menulis DOCUMENT_DELETED beraktor GUC app.user_id.
  PK / UMP / ND tak punya trigger itu -> event *_DELETED di sini.
Terap/lepas/refund ND & UMP (1 Nov 2026, tiket (a), OK MASTER): event riwayat=False (kolom baris aplikasi/refund beraktor sudah tampil di
riwayat); metadata MENAMBAH "ke" (nomor dokumen sasaran) + "kunci" (id baris aplikasi/refund) -- identitas, bukan nilai uang; aksi tanpa
sasaran TIDAK punya field itu (bukan null palsu). VD_UNAPPLIED -> riwayat=False: riwayat UMP sudah punya kolom UM_DILEPAS (tanpa ganda).
Di luar cakupan (tiket): checksum audit_logs (0 baris terisi, klaim Law 12 = Law 34)."""
from typing import NamedTuple, Optional


class Ev(NamedTuple):
    nama: str  # audit_logs."eventType"
    entitas: str  # audit_logs.entity_type (cocok so_riwayat.ENTITAS_AUDIT)
    ringkas: str
    riwayat: bool  # True = ikut riwayat dokumen (event lama); False = jejak audit saja
    identitas: tuple = ()  # metadata identitas WAJIB (persis) selain {jenis, aksi, ringkas, user_id}: "ke" (nomor sasaran), "kunci" (id baris)


EVENT = {
    ("bill", "posted"): Ev("BILL_POSTED", "bills", "Faktur pembelian diposting", False),
    # BUG-058 (6 Nov 2026, Law 12): ubah draf tagihan TAK punya kolom beraktor -> riwayat=True (satu-satunya jejak); metadata += medan,
    # total_sebelum, total_sesudah (bukan "fields": ringkasnya memuat total, tak ditimpa _selesaikan).
    ("bill", "updated"): Ev("BILL_UPDATED", "bills", "Faktur pembelian diubah", True),
    ("bill", "voided"): Ev("BILL_VOIDED", "bills", "Faktur pembelian dibatalkan (void)", False),
    ("payment_out", "posted"): Ev("PAYMENT_OUT_POSTED", "bill_payments_v2", "Pembayaran keluar diposting", False),
    ("payment_out", "voided"): Ev("PAYMENT_OUT_VOIDED", "bill_payments_v2", "Pembayaran keluar dibatalkan (void)", False),
    ("payment_out", "deleted"): Ev("PAYMENT_OUT_DELETED", "bill_payments_v2", "Draf pembayaran keluar dihapus", False),
    ("vendor_deposit", "posted"): Ev("VD_POSTED", "vendor_deposit", "Uang muka pembelian diposting", False),
    ("vendor_deposit", "voided"): Ev("VD_VOIDED", "vendor_deposit", "Uang muka pembelian dibatalkan (void)", True),
    ("vendor_deposit", "unapplied"): Ev("VD_UNAPPLIED", "vendor_deposit", "Penerapan uang muka pembelian dilepas", False),
    ("vendor_deposit", "applied"): Ev("VD_APPLIED", "vendor_deposit", "Uang muka pembelian diterapkan", False, ("ke", "kunci")),
    ("vendor_deposit", "refunded"): Ev("VD_REFUNDED", "vendor_deposit", "Uang muka pembelian dikembalikan vendor", False, ("kunci",)),
    ("vendor_deposit", "deleted"): Ev("VD_DELETED", "vendor_deposit", "Draf uang muka pembelian dihapus", False),
    ("vendor_credit", "posted"): Ev("VC_POSTED", "vendor_credits", "Nota debit diposting", False),
    ("vendor_credit", "voided"): Ev("VC_VOIDED", "vendor_credits", "Nota debit dibatalkan (void)", False),
    ("vendor_credit", "deleted"): Ev("VC_DELETED", "vendor_credits", "Draf nota debit dihapus", False),
    ("vendor_credit", "applied"): Ev("VC_APPLIED", "vendor_credits", "Nota debit diterapkan", False, ("ke", "kunci")),
    ("vendor_credit", "unapplied"): Ev("VC_UNAPPLIED", "vendor_credits", "Penerapan nota debit dilepas", False, ("ke", "kunci")),
    ("vendor_credit", "refunded"): Ev("VC_REFUNDED", "vendor_credits", "Pengembalian nota debit diterima", False, ("kunci",)),
    ("purchase_order", "updated"): Ev("PO_UPDATED", "purchase_order", "Pesanan pembelian diubah", True),
    ("purchase_order", "received"): Ev("PO_RECEIVED", "purchase_order", "Barang diterima", True),
    ("purchase_order", "sent"): Ev("PO_SENT", "purchase_order", "Pesanan pembelian dikirim", False),
    ("purchase_order", "cancelled"): Ev("PO_CANCELLED", "purchase_order", "Pesanan pembelian dibatalkan", False),
    ("purchase_order", "closed"): Ev("PO_CLOSED", "purchase_order", "Pesanan pembelian ditutup", False),
    ("goods_receipt", "voided"): Ev("GRN_DIBATALKAN", "purchase_order", "Penerimaan barang dibatalkan", True),
    ("sales_invoice", "posted"): Ev("SALES_INVOICE_POSTED", "sales_invoices", "Faktur diposting", False),
}

# eventType yang TIDAK digabung ke riwayat dokumen (dibaca so_riwayat._selesaikan)
TANPA_RIWAYAT = frozenset(e.nama for e in EVENT.values() if not e.riwayat)
# label event yang tampil di riwayat (dibaca so_riwayat.RINGKAS_AUDIT)
RINGKAS_RIWAYAT = {e.nama: e.ringkas for e in EVENT.values() if e.riwayat}


def nama(jenis: str, aksi: str) -> str:
    """Nama event dari tabel (KeyError = pasangan tak terdaftar -> tambahkan ke EVENT, jangan menulis string di pemanggil)."""
    return EVENT[(jenis, aksi)].nama


async def catat_dokumen(conn, tenant_id: str, jenis: str, aksi: str, doc_id, nomor: Optional[str], user_id,
                        source: Optional[str] = None, ke: Optional[str] = None, kunci=None) -> None:
    """Satu baris audit_logs untuk aksi siklus hidup, di transaksi PEMANGGIL sesudah perubahan status (gagal = aksi batal).
    ke = nomor dokumen sasaran (terap/lepas), kunci = id baris aplikasi/refund; hanya ditulis bila ada."""
    from .so_riwayat import catat_riwayat  # lambat: so_riwayat mengimpor modul ini (TANPA_RIWAYAT, RINGKAS_RIWAYAT)
    ev = EVENT[(jenis, aksi)]
    ada = tuple(k for k, v in (("ke", ke), ("kunci", kunci)) if v is not None)
    if ada != ev.identitas:  # identitas per event = TABEL (satu sumber); salah pasang = galat, bukan baris audit yang menyimpang diam
        raise ValueError(f"audit {ev.nama}: identitas {ada} != tabel {ev.identitas}")
    ringkas = f"{ev.ringkas} {nomor}" if nomor else ev.ringkas
    meta = {"jenis": jenis, "aksi": aksi}
    if ke is not None:
        meta["ke"] = ke
    if kunci is not None:
        meta["kunci"] = str(kunci)
    await catat_riwayat(conn, tenant_id, ev.entitas, doc_id, nomor, ev.nama, user_id, ringkas, meta, source=source or f"api:{jenis}.{aksi}")
