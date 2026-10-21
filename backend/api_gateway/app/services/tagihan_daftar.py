"""Uang + keadaan Faktur Pembelian (tagihan) dari JURNAL -- SATU sumber untuk daftar, filter/urut status, summary, detail v1/v2, mark_paid
(PEMBELIAN-SERAGAM unit (1), 22 Okt 2026, putusan MASTER; Law 1/16/29).

  sumber   = compute_ap_rincian(tenant) (V440): bill_total = kredit AP jurnal BILL efektif; paid = pembayaran (proporsional) + nota debit +
             uang muka vendor; outstanding = total - paid. Menggantikan BILL_JOURNAL_PAID_CTE (bills.amount CACHE - CTE terbayar; tanpa
             pembagian V245 / nota debit / DP vendor) dan LATERAL detail (pembayaran saja).
  keadaan  = void | draft | paid | overdue | partial | unpaid (enum LAMA, urutan CASE lama):
             - void/draft dibaca dari status_v2 (kolom yang dipakai compute_ap_outstanding); status_v2 NULL -> kolom status lama.
             - tagihan aktif TANPA kredit AP efektif (jurnal BILL dibalik / tak ada) -> 'void' (Batal), sisa 0 -- data TIDAK diubah.
  sisa     = draft -> bills.amount (belum di buku; perilaku lama) · void -> 0 · aktif -> outstanding jurnal.
  dibayar  = paid_amount rincian (0 bila tak ada baris rincian).
Kembaran Python `keadaan_tagihan` / `sisa_tagihan` diuji berpasangan + harness nyata (uji-nyata-tagihan-rincian).
"""
from datetime import date
from decimal import Decimal
from typing import Optional

KEADAAN = ("void", "draft", "paid", "overdue", "partial", "unpaid")
URUT_KEADAAN = {"overdue": 1, "unpaid": 2, "partial": 3, "paid": 4, "draft": 5, "void": 6}  # == urutan kolom status lama


def join_rincian(p_tenant: str = "$1", a: str = "b") -> str:
    """LEFT JOIN rincian AP; alias `apr`. p_tenant = placeholder tenant kueri pemanggil."""
    return f"LEFT JOIN compute_ap_rincian({p_tenant}) apr ON apr.bill_id = {a}.id"


def _sql_void(a: str) -> str:
    return f"({a}.status_v2 = 'void' OR ({a}.status_v2 IS NULL AND {a}.status = 'void'))"


def _sql_draft(a: str) -> str:
    return f"({a}.status_v2 = 'draft' OR ({a}.status_v2 IS NULL AND {a}.status = 'draft'))"


def _sql_tanpa_kredit() -> str:
    return "(apr.bill_id IS NULL OR apr.bill_total = 0)"


def sql_keadaan(hari: str, a: str = "b") -> str:
    """Ungkapan SQL keadaan (butuh join_rincian). `hari` = placeholder tanggal bisnis (mis. '$3::date')."""
    return (f"(CASE WHEN {_sql_void(a)} THEN 'void' WHEN {_sql_draft(a)} THEN 'draft' "
            f"WHEN {_sql_tanpa_kredit()} THEN 'void' "
            f"WHEN apr.outstanding <= 0 THEN 'paid' "
            f"WHEN apr.paid_amount > 0 AND {a}.due_date < {hari} THEN 'overdue' "
            f"WHEN apr.paid_amount > 0 THEN 'partial' "
            f"WHEN {a}.due_date < {hari} THEN 'overdue' ELSE 'unpaid' END)")


def sql_urut_keadaan(hari: str, a: str = "b") -> str:
    return "(CASE " + sql_keadaan(hari, a) + " " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in URUT_KEADAAN.items()) + " END)"


def sql_dibayar() -> str:
    return "COALESCE(apr.paid_amount, 0)"


def sql_sisa(a: str = "b") -> str:
    return (f"(CASE WHEN {_sql_void(a)} THEN 0 WHEN {_sql_draft(a)} THEN COALESCE({a}.amount, 0) "
            f"WHEN {_sql_tanpa_kredit()} THEN 0 ELSE apr.outstanding END)")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def _void(status, status_v2) -> bool:
    return status_v2 == "void" or (status_v2 is None and status == "void")


def _draft(status, status_v2) -> bool:
    return status_v2 == "draft" or (status_v2 is None and status == "draft")


def keadaan_tagihan(status: Optional[str], status_v2: Optional[str], rincian: Optional[dict], due_date: Optional[date],
                    hari_ini: date) -> str:
    """Kembaran Python sql_keadaan. rincian = baris compute_ap_rincian (bill_total/paid_amount/outstanding) atau None."""
    if _void(status, status_v2):
        return "void"
    if _draft(status, status_v2):
        return "draft"
    if not rincian or _d(rincian.get("bill_total")) == 0:
        return "void"
    paid, sisa = _d(rincian.get("paid_amount")), _d(rincian.get("outstanding"))
    lewat = due_date is not None and due_date < hari_ini
    if sisa <= 0:
        return "paid"
    if paid > 0 and lewat:
        return "overdue"
    if paid > 0:
        return "partial"
    return "overdue" if lewat else "unpaid"


def sisa_tagihan(status: Optional[str], status_v2: Optional[str], rincian: Optional[dict], amount) -> Decimal:
    """Kembaran Python sql_sisa."""
    if _void(status, status_v2):
        return Decimal("0")
    if _draft(status, status_v2):
        return _d(amount)
    if not rincian or _d(rincian.get("bill_total")) == 0:
        return Decimal("0")
    return _d(rincian.get("outstanding"))


async def rincian_satu(conn, tenant_id: str, bill_id) -> Optional[dict]:
    r = await conn.fetchrow("SELECT bill_total, paid_amount, outstanding FROM compute_ap_rincian($1) WHERE bill_id = $2",
                            tenant_id, bill_id)
    return dict(r) if r else None
