"""
KasBank V2 Router - Cash & Bank Module V2

Unified endpoints for bank account management, manual transactions,
and bank transfers. Uses the DRAFT -> POST -> VOID workflow for
manual transactions and transfers.

Balances are derived from journal_entries/journal_lines (not denormalized).
Bank transaction running_balance is maintained by DB trigger
(trg_update_bank_balance) which atomically updates bank_accounts.current_balance.

Endpoints:
- GET    /bank-accounts                           - List all bank accounts
- GET    /bank-accounts/{id}                      - Get bank account detail
- GET    /bank-accounts/{id}/transactions          - List transactions for account
- POST   /bank-accounts/{id}/transactions          - Create manual transaction (draft)
- POST   /bank-transactions/{id}/post              - Post a draft transaction
- POST   /bank-transactions/{id}/void              - Void a posted transaction
- GET    /bank-transactions/{id}                   - Get transaction detail
- POST   /bank-transfers                           - Create transfer (draft)
- POST   /bank-transfers/{id}/post                 - Post a draft transfer
- POST   /bank-transfers/{id}/void                 - Void a posted transfer
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from pydantic import BaseModel
from uuid import UUID
from datetime import date
import logging
import asyncpg
import uuid as uuid_module

from ..utils.tanggal_tenant import tanggal_dokumen
from ..schemas.kasbank_v2 import (
    CreateManualTransactionRequest,
    VoidTransactionRequest,
    CreateTransferRequest,
    VoidTransferRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class PostTransactionRequest(BaseModel):
    recon_session_id: Optional[str] = None
    statement_line_id: Optional[str] = None


# Connection pool


# =============================================================================
# HELPERS
# =============================================================================


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


async def check_period_is_open(conn, tenant_id: str, transaction_date) -> None:
    """Check if the accounting period for the transaction date is open."""
    period = await conn.fetchrow(
        """
        SELECT id, period_name, status FROM fiscal_periods
        WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
        ORDER BY start_date DESC LIMIT 1
        """,
        tenant_id,
        transaction_date,
    )
    if period and period["status"] in ("CLOSED", "LOCKED"):
        raise HTTPException(
            status_code=403,
            detail=f"Cannot post to {period['status'].lower()} period ({period['period_name']})",
        )


async def generate_transaction_number(conn, tenant_id: str) -> str:
    """Generate a unique transaction number: BT-YYMM-NNNN."""
    from datetime import datetime as dt

    now = await tanggal_dokumen(conn, tenant_id)  # t10-tanggal-bisnis
    prefix = f"BT-{now.strftime('%y%m')}-"

    last = await conn.fetchval(
        """
        SELECT transaction_number FROM bank_transactions
        WHERE tenant_id = $1 AND transaction_number LIKE $2
        ORDER BY transaction_number DESC LIMIT 1
        """,
        tenant_id,
        prefix + "%",
    )

    if last:
        try:
            seq = int(last.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1

    return f"{prefix}{seq:04d}"


def _serialize_tx(row) -> dict:
    """Serialize a bank_transactions row to dict for response.
    Iron Law 1: Uses journal_amount when available (ledger supremacy).
    """
    # Use journal-derived amount if present, else fall back to bt.amount
    amount = row.get("journal_amount", row["amount"])
    return {
        "id": str(row["id"]),
        "transaction_number": row["transaction_number"],
        "transaction_date": row["transaction_date"].isoformat()
        if row["transaction_date"]
        else None,
        "transaction_type": row["transaction_type"],
        "amount": int(amount),
        "running_balance": int(row["running_balance"]),
        "description": row["description"],
        "reference_type": row.get("reference_type"),
        "reference_id": str(row["reference_id"]) if row.get("reference_id") else None,
        "reference_number": row.get("reference_number"),
        "payee_payer": row.get("payee_payer"),
        "status": row["status"],
        "origin_type": row["origin_type"],
        "source_module": row.get("source_module"),
        "is_reconciled": row.get("is_reconciled", False),
        "reconciliation_status": row.get("reconciliation_status", "UNRECONCILED"),
        "journal_id": str(row["journal_id"]) if row.get("journal_id") else None,
        "posted_at": row["posted_at"].isoformat() if row.get("posted_at") else None,
        "voided_at": row["voided_at"].isoformat() if row.get("voided_at") else None,
        "void_reason": row.get("void_reason"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


# Transaction type -> (bank_tx_type, amount_sign)
# deposit types: money comes IN (positive)
# withdrawal types: money goes OUT (negative)
TX_TYPE_MAP = {
    "other_income": ("deposit", 1),
    "interest_income": ("deposit", 1),
    "owner_contribution": ("deposit", 1),
    "bank_admin_fee": ("withdrawal", -1),
    "owner_drawing": ("withdrawal", -1),
    "card_payment": ("withdrawal", -1),
    "expense": ("withdrawal", -1),
}

# Default contra account codes per transaction type
# These are HINTS resolved at runtime from chart_of_accounts (Law 27)
TRANSACTION_TYPE_CONTRA_DEFAULTS: dict[str, str] = {
    # Law 27: Account codes resolved at runtime via resolve_account_id
    "other_income": "4-90200",  # Pendapatan Lainnya
    "interest_income": "4-90100",  # Pendapatan Bunga
    "owner_contribution": "3-10100",  # Modal Pemilik
    "owner_drawing": "3-40000",  # Prive
    "bank_admin_fee": "5-20800",  # Biaya Admin Bank
    "card_payment": "2-10700",  # Utang Kartu Kredit
}


async def resolve_contra_account(
    conn, tenant_id: str, transaction_type: str, contra_account_id: str | None
) -> str:
    """
    Resolve contra account: explicit payload > type default > error.
    All resolution via chart_of_accounts query (Law 27).
    """
    # 1. Explicit from request payload - validate and return
    if contra_account_id:
        account = await conn.fetchrow(
            "SELECT id FROM chart_of_accounts WHERE id = $1::uuid AND tenant_id = $2 AND is_active = true",
            contra_account_id,
            tenant_id,
        )
        if not account:
            raise HTTPException(400, "Akun lawan tidak ditemukan atau tidak aktif.")
        return contra_account_id

    # 2. Default from transaction type
    default_code = TRANSACTION_TYPE_CONTRA_DEFAULTS.get(transaction_type)
    if default_code:
        account_id = await conn.fetchval(
            "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND account_code = $2 AND is_active = true",
            tenant_id,
            default_code,
        )
        if account_id:
            return str(account_id)
        # Default account not found - informative error
        raise HTTPException(
            400,
            f"Akun default '{default_code}' tidak ditemukan untuk tipe '{transaction_type}'. "
            f"Pastikan akun ini aktif di Daftar Akun.",
        )

    # 3. Types that REQUIRE explicit contra account (expense, other_income)
    raise HTTPException(400, "Akun lawan wajib dipilih untuk tipe transaksi ini.")


# =============================================================================
# HEALTH CHECK
# =============================================================================


@router.post("/bank-transactions/{transaction_id}/void", tags=["kasbank-v2"])
async def void_transaction(
    request: Request,
    transaction_id: UUID,
    body: VoidTransactionRequest,
):
    """
    Void SATU transaksi bank MANUAL ("Uang Masuk/Keluar").

    Hanya transaksi yang asalnya sah sebagai transaksi manual yang boleh
    di-void di sini (daftar-PUTIH, diperiksa di dalam lock + FOR UPDATE):
      origin_type = 'MANUAL', reference_type/reference_id NULL, dan jurnalnya
      milik transaksi ini sendiri (source_type='BANK_TRANSACTION', source_id=id).
    Transaksi milik modul lain (DP, beban, faktur, tagihan, transfer, ...) DITOLAK
    400: membatalkannya di sini membalik jurnal tapi meninggalkan dokumen asalnya.

    Membuat jurnal pembalik RV-<nomor jurnal asli> (DRAFT -> baris -> POSTED).
    Jurnal ASLI TETAP POSTED (Law 2), ditandai reversed_by_id + reversed_at.
    Mirror bank_transaction (Rule 3), lalu transaksi ditandai VOIDED + pelaku + alasan.

    Cabang DRAFT di bawah TIDAK TERJANGKAU: penyaring asal mewajibkan jurnal
    milik transaksi ini, dan draf tak punya jurnal -> selalu ditolak lebih dulu.
    Dibiarkan (di luar lingkup unit 13 Sep 2026), diakui sebagai kode mati.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: advisory lock prevents concurrent duplicate
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BANK_TX_VOID:{transaction_id}",
                )
                # Lock the transaction
                tx = await conn.fetchrow(
                    """
                    SELECT bt.*, ba.coa_id as bank_coa_id
                    FROM bank_transactions bt
                    JOIN bank_accounts ba ON bt.bank_account_id = ba.id
                    WHERE bt.id = $1 AND bt.tenant_id = $2
                    FOR UPDATE OF bt
                    """,
                    transaction_id,
                    ctx["tenant_id"],
                )
                if not tx:
                    raise HTTPException(status_code=404, detail="Transaction not found")

                # Penyaring ASAL (daftar-putih), di dalam lock + FOR UPDATE, sebelum
                # cabang apa pun. Terukur 13 Sep 2026: memisahkan 1 dari 201 baris.
                orig_je = None
                if tx["journal_id"]:
                    orig_je = await conn.fetchrow(
                        """
                        SELECT id, journal_number, source_type, source_id, reversed_by_id
                        FROM journal_entries
                        WHERE id = $1 AND tenant_id = $2
                        FOR UPDATE
                        """,
                        tx["journal_id"],
                        ctx["tenant_id"],
                    )
                asal_sah = (
                    tx["origin_type"] == "MANUAL"
                    and tx["reference_type"] is None
                    and tx["reference_id"] is None
                    and orig_je is not None
                    and orig_je["source_type"] == "BANK_TRANSACTION"
                    and str(orig_je["source_id"]) == str(tx["id"])
                )
                if not asal_sah:
                    raise HTTPException(
                        status_code=400,
                        detail="Transaksi ini dibuat oleh modul lain. Batalkan dari dokumen asalnya.",
                    )

                if tx["status"] == "VOIDED":
                    raise HTTPException(
                        status_code=400, detail="Transaction already voided"
                    )

                # Guard: Opening balance transactions cannot be voided
                if tx["transaction_type"] == "opening":
                    raise HTTPException(
                        status_code=400,
                        detail="Saldo awal tidak bisa di-void. Gunakan Edit Akun untuk mengubah saldo awal.",
                    )

                if tx["status"] == "DRAFT":
                    # Just delete the draft (no journal to reverse, balance wasn't affected)
                    # But we need to reverse the trigger's balance impact first
                    # Actually during create we already reversed it. So just delete.
                    await conn.execute(
                        "DELETE FROM bank_transactions WHERE id = $1",
                        transaction_id,
                    )
                    return {
                        "success": True,
                        "message": "Draft transaction deleted",
                        "data": {"id": str(transaction_id), "status": "DELETED"},
                    }

                # POSTED transaction - need reversal journal
                if tx["reconciliation_status"] == "RECONCILED":
                    raise HTTPException(
                        status_code=400, detail="Cannot void a reconciled transaction"
                    )

                # Check period
                await check_period_is_open(
                    conn, ctx["tenant_id"], tx["transaction_date"]
                )

                # Jurnal pembalik bertanggal HARI INI (tanggal bisnis tenant): periodenya
                # juga harus terbuka -> galat rapi, bukan 500 dari trigger periode.
                hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
                await check_period_is_open(conn, ctx["tenant_id"], hari_ini)

                # Sudah dibalik lewat pintu lain (mis. pembalikan generik) -> 409,
                # bukan 500 dari indeks satu-pembalikan (Law 26).
                if orig_je["reversed_by_id"]:
                    raise HTTPException(
                        status_code=409,
                        detail="Jurnal transaksi ini sudah dibalik",
                    )

                # Get original journal lines
                original_lines = await conn.fetch(
                    "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                    tx["journal_id"],
                )

                if not original_lines:
                    raise HTTPException(
                        status_code=500, detail="Original journal lines not found"
                    )

                abs_amount = sum(line["debit"] or 0 for line in original_lines)

                # Create reversal journal
                reversal_journal_id = uuid_module.uuid4()
                # BUKAN transaction_number: NULL untuk transaksi manual -> "RV-None",
                # dan void manual kedua melanggar uq_je_tenant_number (500).
                # Nomor jurnal asli unik + maks satu pembalikan (Law 26) -> unik.
                reversal_number = f"RV-{orig_je['journal_number']}"

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, reversal_of_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $9, $4, 'BANK_TRANSACTION', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                    reversal_journal_id,
                    ctx["tenant_id"],
                    reversal_number,
                    f"Void {orig_je['journal_number']} - {body.reason}",
                    transaction_id,
                    tx["journal_id"],
                    abs_amount,
                    ctx["user_id"],
                    hari_ini,  # t10-tanggal-bisnis
                )

                # Swap debit/credit for each line
                for idx, line in enumerate(original_lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        uuid_module.uuid4(),
                        reversal_journal_id,
                        idx,
                        line["account_id"],
                        (line["credit"] or 0),  # Swap
                        (line["debit"] or 0),  # Swap
                        f"Reversal - {line['memo'] or ''}",
                    )

                # Link original journal to its reversal (keep POSTED so journal-derived
                # balance nets to zero: original + reversal = 0)

                # Post the journal (triggers hash chain: Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    reversal_journal_id,
                )
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET reversed_by_id = $2, reversed_at = NOW()  -- Law 2: asli TETAP POSTED
                    WHERE id = $1
                    """,
                    tx["journal_id"],
                    reversal_journal_id,
                )

                # BankSync Rule 3: Create mirror bank_transaction for void
                # (trigger trg_update_bank_balance handles current_balance automatically)
                mirror_type = (
                    "withdrawal" if tx["transaction_type"] == "deposit" else "deposit"
                )
                await conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        id, tenant_id, bank_account_id, transaction_date, transaction_type,
                        amount, running_balance, reference_type, reference_id, reference_number,
                        description, journal_id, status, origin_type, source_module,
                        created_by, posted_by, posted_at
                    ) VALUES ($1, $2, $3, $11, $4,
                              $5, 0, 'manual_void', $6, $7, $8, $9,
                              'POSTED', 'SYSTEM', 'manual', $10, $10, NOW())
                    """,
                    uuid_module.uuid4(),
                    ctx["tenant_id"],
                    tx["bank_account_id"],
                    mirror_type,
                    -(
                        tx["amount"]
                    ),  # Negate: deposit becomes negative, withdrawal becomes positive
                    transaction_id,
                    f"VOID-{orig_je['journal_number']}",
                    f"Void - {body.reason}",
                    reversal_journal_id,
                    ctx["user_id"],
                    hari_ini,  # t10-tanggal-bisnis
                )

                # Mark transaction as VOIDED
                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET status = 'VOIDED',
                        voided_by = $2,
                        voided_at = NOW(),
                        void_reason = $3
                    WHERE id = $1
                    """,
                    transaction_id,
                    ctx["user_id"],
                    body.reason,
                )

                row = await conn.fetchrow(
                    "SELECT * FROM bank_transactions WHERE id = $1", transaction_id
                )

                logger.info(
                    f"Transaction voided: {transaction_id}, reversal={reversal_journal_id}"
                )

                return {
                    "success": True,
                    "message": "Transaction voided successfully",
                    "data": _serialize_tx(row),
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding transaction {transaction_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void transaction")


# =============================================================================
# BANK TRANSFERS - CREATE / POST / VOID
# =============================================================================
