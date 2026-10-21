"""Uang Muka Pembelian (vendor deposit): akun PERAN + sisa dari JURNAL (P-U1 PEMBELIAN-SERAGAM, 22 Okt 2026).

Dulu `vendor_deposits.py` memposting ke kode HARDCODE '1-10800' -- di semua tenant itu 'PPN Masukan' (VAT_INPUT), BUKAN uang muka
(peran AP_PREPAID = 1-10550 'Uang Muka Pembelian'). Law 27: akun SELALU dari peran (`account_roles`), tak ada fallback diam; peran belum
dipetakan -> 409 berkode (posting berhenti). Sisa uang muka (guard apply/refund) = TURUNAN JURNAL (Law 1/16/29), bukan kolom cache
`vendor_deposits.remaining_amount`: Σ(debit - kredit) pada akun AP_PREPAID dari jurnal EFEKTIF bersumber deposit itu
(VENDOR_DEPOSIT = +, DEPOSIT_APPLICATION / DEPOSIT_REFUND = -); jurnal yang sudah dibalik (void) tak ikut.
Predikat 'efektif' ditulis inline (setara is_effective_journal untuk tiga source_type ini) agar harness sintetis (CTE) dapat menjalankan SQL yang SAMA.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException

from .role_resolver import AccountRole, AccountRoleUnmappedError, resolve_account_id_by_role

SUMBER_JURNAL = ("VENDOR_DEPOSIT", "DEPOSIT_APPLICATION", "DEPOSIT_REFUND")
NAMA_PERAN = {
    AccountRole.AP_PREPAID: "Uang Muka Pembelian",
    AccountRole.AP_TRADE: "Hutang Usaha",
    AccountRole.CASH_GENERAL: "Kas",
}

SQL_SISA_JURNAL = """
    SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
    FROM journal_entries je
    JOIN journal_lines jl ON jl.journal_id = je.id
    WHERE je.tenant_id = $1 AND je.source_id = $2 AND je.source_type = ANY($3::text[])
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL
      AND jl.account_id = $4
"""


async def akun_peran(conn, tenant_id: str, peran: str) -> UUID:
    """account_id dari peran; belum dipetakan -> 409 PERAN_AKUN_BELUM_DIPETAKAN (posting berhenti, tanpa fallback)."""
    try:
        return await resolve_account_id_by_role(conn, tenant_id, peran)
    except AccountRoleUnmappedError:
        raise HTTPException(status_code=409, detail={
            "code": "PERAN_AKUN_BELUM_DIPETAKAN",
            "message": f"Akun untuk peran '{NAMA_PERAN.get(peran, peran)}' belum dipetakan. Atur pemetaan akun dulu.",
            "role": peran})


async def sisa_jurnal(conn, tenant_id: str, deposit_id) -> Decimal:
    """Sisa uang muka vendor dari jurnal (Σ debit - kredit pada akun AP_PREPAID, jurnal efektif bersumber deposit ini)."""
    akun = await akun_peran(conn, tenant_id, AccountRole.AP_PREPAID)
    v = await conn.fetchval(SQL_SISA_JURNAL, tenant_id, UUID(str(deposit_id)), list(SUMBER_JURNAL), akun)
    return Decimal(str(v or 0))
