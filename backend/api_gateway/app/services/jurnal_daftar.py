"""Daftar JURNAL UMUM standar SO (kontrak + putusan MASTER 7 Nov 2026): SATU penentu kunci aksi, teks posisi, dan aksi tersedia
untuk GET /api/journals (?aksi=, ?status=reversed), GET /api/journals/summary ({k}_count/_value, pill_tampil) dan detail.

Angka dari BARIS jurnal (Law 1), bukan header: alias `jb` = Σ debit/kredit journal_lines per jurnal (SQL_BARIS).
Kunci:
  tidak_seimbang  = Σ debit baris != Σ kredit baris ATAU baris != header        PILL kontrol, desak 1; tampil HANYA bila count > 0
  draf            = status DRAFT (aksi: posting / hapus)                          PILL, desak 2
  manual          = MANUAL/ADJUSTMENT dan BUKAN pembalik (reversal_of_id NULL)    SARING
  dibalik         = reversed_by_id terisi (sudah dibalik)                         SARING (?status=reversed lama dipetakan ke sini)
  pembalik        = reversal_of_id terisi (jurnal ini pembalikan)                SARING
  dibatalkan      = status VOID (data lama: void biaya 3-12 Sep membalik status, melanggar Law 2; kodenya sudah benar)  SARING
  bulan_ini       = journal_date di bulan tanggal-bisnis tenant, bukan VOID       KARTU
POSTED timpang dicegah trigger DB guard_journal_line_header_balance; DRAF boleh timpang (CHECK je_balanced mengecualikan DRAFT)."""
from decimal import Decimal
from typing import Optional

from . import teks_galat as tg
from ..schemas.aksi_tersedia import aksi

AKSI_JURNAL = ("tidak_seimbang", "draf")  # urut desak
SARING_JURNAL = ("manual", "dibalik", "pembalik", "dibatalkan")
KARTU_JURNAL = ("bulan_ini",)
SEMUA_KUNCI = AKSI_JURNAL + SARING_JURNAL + KARTU_JURNAL
TIPE_MANUAL = ("MANUAL", "ADJUSTMENT")
SEN = Decimal("0.01")

SQL_BARIS = """LEFT JOIN LATERAL (
        SELECT COALESCE(SUM(l.debit), 0) AS d, COALESCE(SUM(l.credit), 0) AS k FROM journal_lines l WHERE l.journal_id = je.id
    ) jb ON true"""
# nomor jurnal tertaut (pembalik / asal) -- satu JOIN per halaman, bukan kueri per baris
SQL_TAUTAN = """LEFT JOIN journal_entries rb ON rb.id = je.reversed_by_id AND rb.tenant_id = je.tenant_id
    LEFT JOIN journal_entries ro ON ro.id = je.reversal_of_id AND ro.tenant_id = je.tenant_id"""


def sql_aksi(kunci: str, hari: str) -> str:
    """`hari` = placeholder tanggal bisnis tenant (dipakai bulan_ini saja)."""
    return {
        "tidak_seimbang": "(ABS(jb.d - jb.k) >= 0.01 OR ABS(jb.d - je.total_debit) >= 0.01 OR ABS(jb.k - je.total_credit) >= 0.01)",
        "draf": "(je.status = 'DRAFT')",
        "manual": "(je.source_type IN ('MANUAL', 'ADJUSTMENT') AND je.reversal_of_id IS NULL)",
        "dibalik": "(je.reversed_by_id IS NOT NULL)",
        "pembalik": "(je.reversal_of_id IS NOT NULL)",
        "dibatalkan": "(je.status = 'VOID')",
        "bulan_ini": f"(je.status <> 'VOID' AND je.journal_date >= date_trunc('month', {hari}::date)::date "
                     f"AND je.journal_date < (date_trunc('month', {hari}::date) + interval '1 month')::date)",
    }[kunci]


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def selisih(b: dict) -> Decimal:
    """Selisih terbesar antara debit/kredit baris dan header (0 = seimbang)."""
    d, k = _d(b.get("baris_debit")), _d(b.get("baris_kredit"))
    return max(abs(d - k), abs(d - _d(b.get("total_debit"))), abs(k - _d(b.get("total_credit"))))


def aksi_semua(b: dict) -> list:
    """Setara sql_aksi untuk kunci PILL (uji berpasangan). b: status, baris_debit, baris_kredit, total_debit, total_credit."""
    hasil = []
    if selisih(b) >= SEN:
        hasil.append("tidak_seimbang")
    if b.get("status") == "DRAFT":
        hasil.append("draf")
    return hasil


def position_text(b: dict) -> str:
    """"Tidak seimbang · selisih Rp 1.000" · "Draf · belum diposting" · "Pembalik JV-…" · "Diposting · dibalik oleh JV-…" ·
    "Diposting" · "Dibatalkan". b juga membawa nomor_pembalik/nomor_asal (JOIN SQL_TAUTAN)."""
    if b.get("status") == "VOID":
        return "Dibatalkan"
    s = selisih(b)
    if s >= SEN:
        return f"Tidak seimbang · selisih {tg.rp(s)}"
    if b.get("status") == "DRAFT":
        return "Draf · belum diposting"
    if b.get("reversal_of_id"):
        return f"Pembalik {b.get('nomor_asal') or 'jurnal lain'}"
    if b.get("reversed_by_id"):
        return f"Diposting · dibalik oleh {b.get('nomor_pembalik') or 'jurnal lain'}"
    return "Diposting"


# Pembalikan jurnal UMUM hanya untuk jurnal tanpa dokumen/lapisan pemilik (14 Sep 2026 Gate 5; dipindah dari routers/journals.py
# agar router dan aksi_tersedia memakai SATU daftar). Allowlist = fail-closed. REVERSAL dikeluarkan (Law 26).
REVERSAL_ALLOWLIST = {"MANUAL", "ADJUSTMENT", "RECONCILIATION_ADJUSTMENT", "RECLASSIFY_CN_COGS_GAP", "RECLASSIFY_TAX_D1_DRIFT", "RECLASSIFY_BILL_INVENTORY"}
_VOID_PANDUAN = [
    (("BILL", "PURCHASE_INVOICE", "PAYMENT_BILL", "BILL_PAYMENT", "BILL_PAYMENT_VOID"), "batalkan tagihan/pembayarannya (void bill)"),
    (("INVOICE", "SALES_INVOICE", "SALES_INVOICE_COGS", "INVOICE_FULFILLMENT", "INVOICE_REVENUE", "CASH_SALE", "POS_SALE", "POS_COGS", "SALES_RECEIPT", "SALES_RECEIPT_COGS"), "batalkan fakturnya (void invoice)"),
    (("RECEIVE_PAYMENT", "PAYMENT_RECEIVED", "PAYMENT_MADE"), "batalkan pembayarannya (void payment)"),
    (("EXPENSE", "EXPENSE_REVERSAL"), "batalkan bebannya (void expense)"),
    (("CUSTOMER_DEPOSIT", "VENDOR_DEPOSIT", "DEPOSIT_APPLICATION", "DEPOSIT_REFUND"), "batalkan/kembalikan uang mukanya lewat modulnya"),
    (("CREDIT_NOTE", "CREDIT_NOTE_COGS", "CREDIT_NOTE_REFUND", "VENDOR_CREDIT", "VENDOR_CREDIT_COGS", "VENDOR_CREDIT_REFUND"), "batalkan nota kredit/kredit vendornya lewat modulnya"),
    (("PRODUCTION_OUTPUT", "PRODUCTION_LABOR", "PRODUCTION_OVERHEAD", "PRODUCTION_VARIANCE", "PRODUCTION_OUTPUT_VOID", "PRODUCTION_RECONCILE", "MATERIAL_ISSUE", "FG_RECEIPT"), "batalkan lewat modul produksi (batalkan work order: POST /api/production/{id}/cancel, atau void rekonsiliasi bulanan)"),
    (("BANK_TRANSACTION", "BANK_TRANSFER", "BRANCH_TRANSFER"), "batalkan lewat modul kas & bank (void transaksi/transfer)"),
    (("CHEQUE_RECEIVED", "CHEQUE_ISSUED", "CHEQUE_DEPOSIT", "CHEQUE_BOUNCE_AR", "CHEQUE_BOUNCE_CHARGES", "CHEQUE_BOUNCE_REVERSAL", "CHEQUE_CANCEL", "CHEQUE_DELETE"), "batalkan lewat modul cek"),
    (("PAYROLL", "PAYROLL_PAYMENT", "PAYROLL_BPJS_PAYMENT", "PAYROLL_TAX_PAYMENT"), "batalkan lewat modul penggajian"),
    (("FIXED_ASSET", "ASSET_DISPOSAL", "ASSET_SALE", "DEPRECATION"), "batalkan lewat modul aset tetap"),
    (("STOCK_ADJUSTMENT", "STOCK_TRANSFER"), "batalkan lewat modul stok"),
    (("DELIVERY_NOTE", "DELIVERY_NOTE_REVERSAL", "DELIVERY_INVOICED", "DELIVERY_INVOICED_REVERSAL"), "batalkan Surat Jalan atau faktur terkait lewat modulnya (void)"),
    (("OPENING", "OPENING_BALANCE", "VENDOR_OPENING_BALANCE"), "perbaiki lewat modul saldo awal"),
    (("INTERCOMPANY",), "batalkan lewat modul antar-perusahaan"),
    (("CLOSING",), "buka kembali lewat proses tutup buku"),
]


def panduan_void(source_type: str) -> str:
    for tipe, pesan in _VOID_PANDUAN:
        if source_type in tipe:
            return pesan
    return "batalkan lewat dokumen/modul sumbernya, bukan pembalikan jurnal umum"


def alasan_tak_bisa_balik(b: dict) -> Optional[str]:
    """SATU penentu untuk aksi_tersedia.balik DAN penolak POST /reverse (kalimat sama). None = boleh dibalik."""
    if b.get("status") == "DRAFT":
        return "Jurnal draf belum diposting; hapus atau posting dulu."
    if b.get("status") == "VOID":
        return "Jurnal sudah dibatalkan."
    if b.get("reversed_by_id"):
        return f"Jurnal sudah dibalik oleh {b.get('nomor_pembalik') or 'jurnal lain'}."
    if b.get("reversal_of_id"):
        return "Jurnal ini sendiri adalah pembalikan; pembalikan atas pembalikan tidak diizinkan (Law 26)."
    st = b.get("source_type") or ""
    if st not in REVERSAL_ALLOWLIST:
        return f"Jurnal {st} milik dokumen/modul dan tak bisa dibalik lewat pembalikan jurnal umum; {panduan_void(st)}."
    return None


def aksi_tersedia(b: dict, periode_tutup: bool = False) -> dict:
    """Detail: {posting, hapus, balik} dalam bentuk bersama schemas/aksi_tersedia."""
    draf = b.get("status") == "DRAFT"
    timpang = selisih(b) >= SEN
    alasan_posting = ("Hanya jurnal draf yang bisa diposting." if not draf else
                      f"Jurnal tidak seimbang (selisih {tg.rp(selisih(b))}); perbaiki barisnya dulu." if timpang else
                      "Periode akuntansi tanggal jurnal ini sudah ditutup." if periode_tutup else None)
    balik = alasan_tak_bisa_balik(b)
    return {
        "posting": aksi(alasan_posting is None, alasan_posting),
        "hapus": aksi(draf, "Hanya jurnal draf yang bisa dihapus; jurnal terposting dibalik."),
        "balik": aksi(balik is None, balik),
    }
