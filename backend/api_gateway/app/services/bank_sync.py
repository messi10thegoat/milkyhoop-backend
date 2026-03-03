"""
Bank Sync Service — Shared helper for bank_transaction ↔ journal synchronization.

INVARIANT: Every POSTED journal that touches a bank CoA MUST have a corresponding
bank_transaction record with journal_id linked. Violation = ledger-register divergence.

Usage:
    from app.services.bank_sync import resolve_bank_coa, create_bank_transaction_for_journal

    async with conn.transaction():
        # 1. Resolve CoA
        coa_id = await resolve_bank_coa(conn, bank_account_id)

        # 2. Create journal (flow-specific logic)
        journal_id = await create_my_journal(conn, coa_id=coa_id, ...)

        # 3. Create linked bank_transaction (MUST be in same transaction)
        bank_tx_id = await create_bank_transaction_for_journal(
            conn=conn,
            tenant_id=tenant_id,
            bank_account_id=bank_account_id,
            journal_id=journal_id,
            transaction_date=date,
            transaction_type='withdrawal',  # or 'deposit', 'payment_made', etc.
            amount=-total_amount,  # SIGNED: positive=inflow, negative=outflow
            reference_type='expense',
            reference_id=expense_id,
            reference_number=expense_number,
            description=f'Expense: {expense_number}',
            payee_payer=vendor_name,
            created_by=user_id,
        )

Follows:
    - Iron Law 1 (Ledger Supremacy): journal = source of truth
    - Iron Law 8 (No Silent Mutation): every bank balance change via journal
    - Iron Law 13 (Concurrency Safety): caller must hold transaction lock
    - Iron Law 23 (Transaction Atomicity): journal + bank_txn in same DB transaction
"""

import uuid as uuid_module
from datetime import date
from typing import Optional
from uuid import UUID

import logging

logger = logging.getLogger(__name__)


async def resolve_bank_coa(conn, bank_account_id) -> str:
    """
    Resolve the Chart of Accounts ID for a bank account.

    Every bank account has a linked CoA record. This function returns the CoA UUID
    that MUST be used in journal lines when debiting/crediting this bank account.

    Args:
        conn: Database connection (within active transaction)
        bank_account_id: UUID of the bank account

    Returns:
        str: The chart_of_account_id (CoA UUID) for this bank account

    Raises:
        ValueError: If bank account not found or has no linked CoA
    """
    row = await conn.fetchrow(
        "SELECT chart_of_account_id FROM bank_accounts WHERE id = $1",
        bank_account_id if isinstance(bank_account_id, UUID) else UUID(str(bank_account_id))
    )
    if not row or not row["chart_of_account_id"]:
        raise ValueError(
            f"Bank account {bank_account_id} not found or has no chart_of_account_id. "
            f"Every bank account MUST have a linked CoA record."
        )
    return str(row["chart_of_account_id"])


async def create_bank_transaction_for_journal(
    conn,
    *,
    tenant_id: str,
    bank_account_id,
    journal_id,
    transaction_date,
    transaction_type: str,
    amount: int,
    reference_type: str,
    reference_id,
    created_by,
    reference_number: Optional[str] = None,
    description: Optional[str] = None,
    payee_payer: Optional[str] = None,
) -> UUID:
    """
    Create a bank_transaction record linked to a journal entry.

    MUST be called within an existing database transaction (conn.transaction()).
    The database trigger trg_update_bank_balance handles atomic balance update.

    Sign convention:
        - Positive amount = inflow (deposit, payment_received, opening)
        - Negative amount = outflow (withdrawal, payment_made, transfer_out)

    Allowed transaction_type values:
        deposit, withdrawal, transfer_in, transfer_out, adjustment,
        opening, payment_received, payment_made, fee, interest, charge

    Args:
        conn: Database connection (within active transaction)
        tenant_id: Tenant ID string
        bank_account_id: UUID of the bank account
        journal_id: UUID of the linked journal entry (REQUIRED)
        transaction_date: Date of the transaction
        transaction_type: One of the allowed types
        amount: Signed integer amount (positive=inflow, negative=outflow)
        reference_type: Source type (e.g., 'expense', 'bill_payment', 'invoice')
        reference_id: UUID of the source document
        created_by: UUID of the user creating this transaction
        reference_number: Optional reference/document number
        description: Optional description text
        payee_payer: Optional payee/payer name

    Returns:
        UUID: The ID of the created bank_transaction

    Raises:
        ValueError: If journal_id is None (every bank_txn MUST link to a journal)
    """
    if journal_id is None:
        raise ValueError(
            "journal_id is required. Every bank_transaction MUST link to a journal entry. "
            "This prevents ledger-register divergence (Iron Law 1 & 23)."
        )

    bank_tx_id = uuid_module.uuid4()

    # Normalize UUIDs
    ba_id = bank_account_id if isinstance(bank_account_id, UUID) else UUID(str(bank_account_id))
    j_id = journal_id if isinstance(journal_id, UUID) else UUID(str(journal_id))
    ref_id = reference_id if isinstance(reference_id, UUID) else UUID(str(reference_id))
    cb_id = created_by if isinstance(created_by, UUID) else UUID(str(created_by))

    # Normalize date
    if isinstance(transaction_date, str):
        transaction_date = date.fromisoformat(transaction_date)

    await conn.execute(
        """
        INSERT INTO bank_transactions (
            id, tenant_id, bank_account_id, transaction_date,
            transaction_type, amount, running_balance,
            reference_type, reference_id, reference_number,
            description, payee_payer, journal_id, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, 0, $7, $8, $9, $10, $11, $12, $13)
        """,
        bank_tx_id,
        tenant_id,
        ba_id,
        transaction_date,
        transaction_type,
        amount,
        reference_type,
        ref_id,
        reference_number,
        description,
        payee_payer,
        j_id,
        cb_id,
    )

    logger.info(
        "bank_sync: Created bank_transaction %s for journal %s (bank_account=%s, amount=%s, type=%s)",
        bank_tx_id, j_id, ba_id, amount, transaction_type,
    )

    return bank_tx_id


async def create_reversal_bank_transaction(
    conn,
    *,
    tenant_id: str,
    original_bank_transaction_id,
    reversal_journal_id,
    created_by,
    description_prefix: str = "Reversal:",
) -> Optional[UUID]:
    """
    Create a mirror (reversal) bank_transaction for a voided/reversed journal.

    When a journal that touched a bank account is reversed, a mirror bank_transaction
    MUST be created with negated amount to keep bank_transactions in sync with the ledger.

    Args:
        conn: Database connection (within active transaction)
        tenant_id: Tenant ID string
        original_bank_transaction_id: UUID of the original bank_transaction to reverse
        reversal_journal_id: UUID of the reversal journal entry
        created_by: UUID of the user
        description_prefix: Prefix for the reversal description

    Returns:
        UUID: The ID of the reversal bank_transaction, or None if original not found
    """
    if reversal_journal_id is None:
        raise ValueError("reversal_journal_id is required for reversal bank_transaction.")

    # Fetch original bank_transaction
    orig_id = (
        original_bank_transaction_id
        if isinstance(original_bank_transaction_id, UUID)
        else UUID(str(original_bank_transaction_id))
    )

    original = await conn.fetchrow(
        "SELECT * FROM bank_transactions WHERE id = $1 AND tenant_id = $2",
        orig_id, tenant_id,
    )

    if not original:
        logger.warning(
            "bank_sync: Cannot create reversal — original bank_transaction %s not found for tenant %s",
            orig_id, tenant_id,
        )
        return None

    # Determine reversal transaction_type
    type_reversal_map = {
        "withdrawal": "deposit",
        "deposit": "withdrawal",
        "payment_made": "payment_received",
        "payment_received": "payment_made",
        "transfer_out": "transfer_in",
        "transfer_in": "transfer_out",
        "fee": "deposit",
        "charge": "deposit",
        "interest": "withdrawal",
        "adjustment": "adjustment",
        "opening": "adjustment",
    }
    reversal_type = type_reversal_map.get(original["transaction_type"], "adjustment")

    reversal_tx_id = await create_bank_transaction_for_journal(
        conn,
        tenant_id=tenant_id,
        bank_account_id=original["bank_account_id"],
        journal_id=reversal_journal_id,
        transaction_date=date.today(),
        transaction_type=reversal_type,
        amount=-original["amount"],  # Negate the original amount
        reference_type=f"{original['reference_type'] or 'unknown'}_reversal",
        reference_id=original["reference_id"] or orig_id,
        reference_number=original.get("reference_number"),
        description=f"{description_prefix} {original.get('description', '')}".strip(),
        payee_payer=original.get("payee_payer"),
        created_by=created_by,
    )

    logger.info(
        "bank_sync: Created reversal bank_transaction %s for original %s (amount=%s → %s)",
        reversal_tx_id, orig_id, original["amount"], -original["amount"],
    )

    return reversal_tx_id


# =============================================================================
# LEDGER BALANCE — Shared helper (Law 1: Ledger Supremacy, Law 16)
# =============================================================================

async def get_ledger_balance(conn, bank_account_id: str) -> float:
    """
    Derive bank account balance from journal_lines (Law 1: Ledger Supremacy, Law 16).
    This is the ONLY authoritative balance source.
    Returns the true book balance for this bank account.
    """
    row = await conn.fetchrow("""
        SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
        FROM journal_lines jl
        JOIN journal_entries je ON jl.journal_id = je.id
        JOIN bank_accounts ba ON ba.coa_id = jl.account_id
        WHERE ba.id = $1
          AND je.status = 'POSTED'
          AND je.tenant_id = ba.tenant_id
    """, bank_account_id)
    return float(row["ledger_balance"]) if row else 0.0
