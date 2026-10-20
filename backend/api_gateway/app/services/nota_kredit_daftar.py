"""Data daftar/detail NOTA KREDIT seragam dengan Pesanan/Faktur (S2-NK, 20 Okt 2026, MASTER). Server menghitung, FE memformat.

  sisa jurnal  = NK bebas (tanpa faktur) terbit: net Piutang (kredit - debit) jurnal EFEKTIF bersumber NK itu
                 (CREDIT_NOTE kredit; CREDIT_NOTE_REFUND debit). Draf / void / BERTAUT faktur = 0: menerapkan NK ke faktur
                 TIDAK membuat jurnal, aplikasi menulis original_invoice_id (pagar V250) dan kreditnya ikut faktur
                 (cabang 2 compute_ar_outstanding). Digerbang == sisa_kredit_nk (penjaga refund/apply, TIDAK diubah).
  aksi         = 'belum_terpakai' : terbit AND tak bertaut AND sisa jurnal > 0 (pill + ?aksi= + summary: predikat SAMA)
  sisa_keadaan = draf | utuh | sebagian | terpakai | dikembalikan | sebagian_dikembalikan | batal
  status_text  = teks_galat.status_id("cn", status_detail) == peta FE r288, KECUALI NK bertaut 'posted' -> "Terpakai"
                 (putusan MASTER 20 Okt: pola NetSuite Credit Memo "Fully Applied"; sisa 0, kredit sudah memotong faktur).
"""
from decimal import Decimal
from typing import Optional

AKSI_BELUM_TERPAKAI = "belum_terpakai"
AKSI_NK = (AKSI_BELUM_TERPAKAI,)
KEADAAN_NK = ("draf", "utuh", "sebagian", "terpakai", "dikembalikan", "sebagian_dikembalikan", "batal")
_KEADAAN_DARI_DETAIL = {"draft": "draf", "posted": "utuh", "partial": "sebagian", "applied": "terpakai",
                        "refunded": "dikembalikan", "partially_refunded": "sebagian_dikembalikan", "void": "batal"}


def sql_sisa_nk(a: str = "credit_notes") -> str:
    """Sisa JURNAL satu NK (ungkapan SQL; $1 = tenant). Kembaran Python: sisa_nk_jurnal_many."""
    return (f"(CASE WHEN {a}.status IN ('draft', 'void') OR {a}.original_invoice_id IS NOT NULL THEN 0 ELSE ("
            "SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) FROM journal_entries je "
            "JOIN journal_lines jl ON jl.journal_id = je.id "
            "JOIN chart_of_accounts coa ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE' "
            f"WHERE je.tenant_id = $1 AND je.source_id = {a}.id AND is_effective_journal(je.id)) END)")


async def sisa_nk_jurnal_many(conn, tenant_id: str, ids: list) -> dict:
    """{str(id): Decimal} sisa jurnal NK -- ungkapan SQL YANG SAMA (sql_sisa_nk)."""
    if not ids:
        return {}
    rows = await conn.fetch(
        f"SELECT credit_notes.id, {sql_sisa_nk()} AS sisa FROM credit_notes "
        "WHERE credit_notes.tenant_id = $1 AND credit_notes.id = ANY($2::uuid[])", tenant_id, list(ids))
    return {str(r["id"]): Decimal(str(r["sisa"])) for r in rows}


def sql_aksi(aksi: str, a: str = "credit_notes") -> str:
    if aksi != AKSI_BELUM_TERPAKAI:
        raise ValueError(aksi)
    return f"({a}.status NOT IN ('draft', 'void') AND {a}.original_invoice_id IS NULL AND {sql_sisa_nk(a)} > 0)"


def aksi_nk(status: str, original_invoice_id, sisa) -> Optional[str]:
    if status in ("draft", "void") or original_invoice_id is not None or sisa is None:
        return None
    return AKSI_BELUM_TERPAKAI if Decimal(str(sisa)) > 0 else None


def _detail_efektif(status: str, status_detail: str, original_invoice_id) -> str:
    # NK bertaut berstatus 'posted' = kredit sudah memotong faktur (sisa 0) -> tampil seperti 'applied'
    return "applied" if status == "posted" and original_invoice_id is not None else status_detail


def keadaan_nk(status: str, status_detail: str, original_invoice_id) -> str:
    sd = _detail_efektif(status, status_detail, original_invoice_id)
    return _KEADAAN_DARI_DETAIL.get(sd, sd)


def status_text_nk(status: str, status_detail: str, original_invoice_id) -> str:
    from . import teks_galat as tg
    return tg.status_id("cn", _detail_efektif(status, status_detail, original_invoice_id))


def keadaan_cocok_sisa(keadaan: str, total, sisa) -> bool:
    """Gerbang: keadaan konsisten dengan sisa JURNAL (draf: sisa jurnal 0, belum di buku)."""
    s, a = Decimal(str(sisa or 0)), Decimal(str(total or 0))
    if keadaan in ("draf", "batal", "terpakai", "dikembalikan"):
        return s == 0
    if keadaan == "utuh":
        return s == a and a > 0
    return 0 < s < a
