"""
Customer Deposits Router - Uang Muka Pelanggan

Endpoints for managing customer deposits (advance payments).
Customer deposits can be applied to invoices or refunded back.

Flow:
1. Create draft customer deposit
2. Post to accounting (receive deposit, creates journal)
3. Apply to invoice(s) OR issue refund to customer
4. Void if needed (only if unapplied/unrefunded)

Journal Entry on POST (Receive):
    Dr. Kas/Bank (user-picked account_id)         amount
        Cr. CUSTOMER_DEPOSIT_LIABILITY (Uang Muka Pelanggan)  amount

Journal Entry on APPLY (to Invoice):
    Dr. CUSTOMER_DEPOSIT_LIABILITY (Uang Muka Pelanggan)  applied_amount
        Cr. AR_TRADE (Piutang Usaha)              applied_amount

Journal Entry on REFUND:
    Dr. CUSTOMER_DEPOSIT_LIABILITY (Uang Muka Pelanggan)  refund_amount
        Cr. Kas/Bank (user-picked account_id)         refund_amount

Fase C1.4: hardcoded CoA codes replaced with role resolver.
- CUSTOMER_DEPOSIT_LIABILITY (was hardcoded 2-10500)
- AR_TRADE (was hardcoded 1-10300 -- WRONG name: 1-10300 is Kas Kecil;
  Piutang Usaha is 1-10400. Latent bug never triggered: zero apply
  journals in DB at migration time.)

Endpoints:
- GET    /customer-deposits              - List customer deposits
- GET    /customer-deposits/summary      - Summary statistics
- GET    /customer-deposits/{id}         - Get deposit detail
- POST   /customer-deposits              - Create draft deposit
- PATCH  /customer-deposits/{id}         - Update draft deposit
- DELETE /customer-deposits/{id}         - Delete draft deposit
- POST   /customer-deposits/{id}/post    - Post to accounting
- POST   /customer-deposits/{id}/apply   - Apply to invoice(s)
- POST   /customer-deposits/{id}/refund  - Issue refund to customer
- POST   /customer-deposits/{id}/void    - Void deposit
- GET    /customers/{id}/deposits        - List deposits for customer
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
import uuid as uuid_module

from ..schemas.customer_deposits import (
    CreateCustomerDepositRequest,
    UpdateCustomerDepositRequest,
    ApplyCustomerDepositRequest,
    RefundCustomerDepositRequest,
    VoidCustomerDepositRequest,
    CustomerDepositResponse,
    CustomerDepositDetailResponse,
    CustomerDepositListResponse,
    CustomerDepositSummaryResponse,
)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
    resolve_account_id_by_role_if_pkp,  # FIX_P1_DEPOSIT 2026-06-16 (d)
)
from ..services.role_precondition import assert_required_roles_for_path
from ..services.bank_sync import (
    create_bank_transaction_for_journal,
    create_reversal_bank_transaction,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool

# Fase C1.4: required role mappings for customer_deposits.py posting paths.
#
# CUSTOMER_DEPOSIT_LIABILITY -- Cr on post, Dr on apply/refund.
# AR_TRADE                   -- Cr on apply-to-invoice (settles Piutang Usaha).
# CASH_GENERAL               -- fallback when create payload has no
#                               user-picked account_id (cash path).
#
# Note: BANK_OPERATIONAL is intentionally EXCLUDED from the precondition
# list (fallback-only pattern matches Fase C1.2/C1.3). Cash/bank posting
# uses the user-picked dep["account_id"] (set at create) as primary; the
# role fallback only fires if a future payload arrives without account_id.
# NULL guard: raise 422 (Law 4 consistency), never silent-skip Dr/Cr.
CUSTOMER_DEPOSITS_REQUIRED_ROLES = [
    AccountRole.CUSTOMER_DEPOSIT_LIABILITY,
    AccountRole.AR_TRADE,
    AccountRole.CASH_GENERAL,
]

# One-time precondition flag (mirrors Fase C1.2/C1.3 pattern).
_precondition_checked_tenants: set = set()


async def _ensure_role_preconditions(pool, tenant_id=None) -> None:
    """Run role-mapping precondition for customer_deposits.

    Scopes the audit to the ACTING tenant when tenant_id is supplied (cached
    per-tenant); tenant_id=None preserves the legacy all-tenants behavior.
    Fails loud (PreconditionFailedError) if the audited tenant lacks any
    required role mapping; a tenant added later without mapping will still
    fail loud at resolve_account_id_by_role(...) via AccountRoleUnmappedError.
    """
    if tenant_id is None:
        await assert_required_roles_for_path(
            pool, "customer_deposits", CUSTOMER_DEPOSITS_REQUIRED_ROLES
        )
        return
    if tenant_id in _precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "customer_deposits", CUSTOMER_DEPOSITS_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _precondition_checked_tenants.add(tenant_id)


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


async def get_invoice_remaining_from_journal(conn, tenant_id: str, invoice_id) -> int:
    """Invoice remaining balance — delegates to CANONICAL compute_ar_outstanding().

    FIX_P35_ARCANON 2026-06-17 — Layer 2: collapse parallel settlement SQL.
    This was a duplicate of the per-invoice net-AR CTE (invoice + receive-payment +
    credit-note + deposit-application + reversal + legacy inline). It now reads the
    single canonical source so deposit-apply validation and AR reads cannot diverge.

    Used by deposit-apply validation BEFORE the new application journal is posted;
    compute_ar_outstanding() reads POSTED journals only, so it correctly reflects the
    pre-application remaining (behavior-preserving vs the old query).
    """
    result = await conn.fetchval(
        """
        SELECT COALESCE(SUM(outstanding), 0)
        FROM compute_ar_outstanding($1)
        WHERE invoice_id = $2
        """,
        tenant_id,
        invoice_id,
    )
    return int(result or 0)


# FIX_P1_DEPOSIT 2026-06-16 (b): journal-derived deposit balance.
async def compute_customer_deposit_balance(conn, tenant_id: str, customer_id) -> int:
    """Available customer-deposit balance, journal-derived (Law 1/16/29).

    Computed as the NET MOVEMENT on the CUSTOMER_DEPOSIT_LIABILITY (2-10500)
    account = SUM(credit) - SUM(debit), over is_effective journals only,
    scoped to all deposits belonging to this customer.

    Net-account-movement is immune to future source_types (kills the BL-08
    class by construction). Because is_effective_journal() already drops
    reversed pairs (reversed_by_id OR reversal_of_id), the net is
    automatically correct after un-apply — no source_type enumeration.

    Source linkage: the deposit POST and forward DEPOSIT_APPLICATION /
    DEPOSIT_REFUND journals carry journal_entries.source_id =
    customer_deposits.id, so we join je.source_id -> customer_deposits.id
    -> customer_deposits.customer_id. Their is_effective REVERSALS carry
    source_id = the INVOICE id (Option B obligation reference for the AR
    guard) and therefore do NOT join customer_deposits here -- but that is
    harmless and correct: a reversal has reversal_of_id set and its
    original has reversed_by_id set, so is_effective_journal() drops BOTH.
    The restored balance comes from is_effective dropping the now-reversed
    forward journal, leaving only the still-effective POST.

    Liability account is credit-normal:
      post   : Cr 2-10500  (+available)
      apply  : Dr 2-10500  (-available)
      refund : Dr 2-10500  (-available)
    => available = SUM(credit) - SUM(debit).
    """
    deposit_account_id = await resolve_account_id_by_role(
        conn, tenant_id, AccountRole.CUSTOMER_DEPOSIT_LIABILITY
    )
    result = await conn.fetchval(
        """
        SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN customer_deposits cd ON cd.id = je.source_id
        WHERE je.tenant_id = $1
          AND cd.tenant_id = $1
          AND cd.customer_id = $2
          AND jl.account_id = $3
          AND is_effective_journal(je.id)
        """,
        tenant_id,
        customer_id,
        deposit_account_id,
    )
    return int(result or 0)


# FIX_P1_DEPOSIT 2026-06-16 (b): per-deposit journal-derived remaining.
async def compute_deposit_remaining(conn, tenant_id: str, deposit_id) -> int:
    """Available remaining for a SINGLE deposit, journal-derived (Law 1/16).

    Net movement on CUSTOMER_DEPOSIT_LIABILITY scoped to one deposit
    (je.source_id = deposit_id), is_effective journals only. Used as the
    AUTHORITATIVE balance for apply-validation (replaces cache-column read).
    """
    deposit_account_id = await resolve_account_id_by_role(
        conn, tenant_id, AccountRole.CUSTOMER_DEPOSIT_LIABILITY
    )
    result = await conn.fetchval(
        """
        SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        WHERE je.tenant_id = $1
          AND je.source_id = $2
          AND jl.account_id = $3
          AND is_effective_journal(je.id)
        """,
        tenant_id,
        deposit_id,
        deposit_account_id,
    )
    return int(result or 0)


# FIX_P1_DEPOSIT 2026-06-16 (d): Invariant guard #7 — AR-side must be AR_TRADE.
async def _assert_ar_side_is_ar_trade(conn, tenant_id: str, ar_line_account_id) -> None:
    """Guard: the AR-side journal line of an apply/un-apply MUST resolve to
    role AR_TRADE, and MUST NOT be REVENUE_DEFERRED (2-10750).

    Customer-deposit apply/un-apply settles Piutang Usaha (AR_TRADE). If a
    refactor ever swaps the AR line for Pendapatan Diterima Dimuka
    (REVENUE_DEFERRED / 2-10750) — a plausible PSAK-72 mix-up — this raises
    loudly instead of silently mis-posting the contract-liability account.
    """
    ar_trade_id = await resolve_account_id_by_role(
        conn, tenant_id, AccountRole.AR_TRADE
    )
    if ar_line_account_id != ar_trade_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "Invariant #7 violated: deposit apply/un-apply AR-side line "
                f"account {ar_line_account_id} does not resolve to role "
                "AR_TRADE. Refusing to post a mis-routed deposit settlement."
            ),
        )
    # Defensive: explicitly forbid the deferred-revenue account on the AR side.
    deferred_id = await resolve_account_id_by_role_if_pkp(
        conn, tenant_id, AccountRole.REVENUE_DEFERRED
    )
    if deferred_id is not None and ar_line_account_id == deferred_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "Invariant #7 violated: deposit AR-side line resolved to "
                "REVENUE_DEFERRED (2-10750). Deposit settlement must hit "
                "AR_TRADE (Piutang Usaha), never deferred revenue."
            ),
        )


# =============================================================================
# LIST CUSTOMER DEPOSITS
# =============================================================================


@router.get("", response_model=CustomerDepositListResponse)
async def list_customer_deposits(
    request: Request,
    status: Optional[
        Literal["all", "draft", "posted", "partial", "applied", "void"]
    ] = Query("all"),
    customer_id: Optional[str] = Query(None),
    search: Optional[str] = Query(
        None, description="Search by number or customer name"
    ),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["deposit_date", "deposit_number", "amount", "created_at"] = Query(
        "created_at"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List customer deposits with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Set tenant context
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Build query conditions
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}")
                params.append(customer_id)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(deposit_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(deposit_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")
                param_idx += 1

            if date_from:
                conditions.append(f"deposit_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"deposit_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "deposit_date": "deposit_date",
                "deposit_number": "deposit_number",
                "amount": "amount",
                "created_at": "created_at",
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM customer_deposits WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, deposit_number, customer_id, customer_name,
                       deposit_date, amount, amount_applied, amount_refunded,
                       status, payment_method, reference, created_at
                FROM customer_deposits
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "deposit_number": row["deposit_number"],
                    "customer_id": row["customer_id"],
                    "customer_name": row["customer_name"],
                    "deposit_date": row["deposit_date"].isoformat(),
                    "amount": row["amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["amount"]
                    - (row["amount_applied"] or 0)
                    - (row["amount_refunded"] or 0),
                    "status": row["status"],
                    "payment_method": row["payment_method"],
                    "reference": row["reference"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing customer deposits: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list customer deposits")


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary", response_model=CustomerDepositSummaryResponse)
async def get_customer_deposits_summary(request: Request):
    """Get summary statistics for customer deposits."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE status = 'applied') as applied_count,
                    COALESCE(SUM(amount), 0) as total_value,
                    COALESCE(SUM(amount_applied), 0) as total_applied,
                    COALESCE(SUM(amount_refunded), 0) as total_refunded
                FROM customer_deposits
                WHERE tenant_id = $1 AND status != 'void'
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            # FIX_P1_DEPOSIT 2026-06-16 (b): authoritative available_balance is
            # journal-derived (net movement on CUSTOMER_DEPOSIT_LIABILITY over
            # is_effective journals), NOT the cache-column subtraction. Immune
            # to reversed/un-applied pairs by construction (Law 1/16).
            deposit_account_id = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.CUSTOMER_DEPOSIT_LIABILITY
            )
            available_balance = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN customer_deposits cd ON cd.id = je.source_id
                WHERE je.tenant_id = $1
                  AND cd.tenant_id = $1
                  AND jl.account_id = $2
                  AND is_effective_journal(je.id)
                """,
                ctx["tenant_id"],
                deposit_account_id,
            )

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "partial_count": row["partial_count"] or 0,
                    "applied_count": row["applied_count"] or 0,
                    "total_value": int(row["total_value"] or 0),
                    "total_applied": int(row["total_applied"] or 0),
                    "total_refunded": int(row["total_refunded"] or 0),
                    "available_balance": int(available_balance or 0),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting customer deposits summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET CUSTOMER DEPOSIT DETAIL
# =============================================================================


@router.get("/{deposit_id}", response_model=CustomerDepositDetailResponse)
async def get_customer_deposit(request: Request, deposit_id: UUID):
    """Get detailed information for a customer deposit."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get deposit
            dep = await conn.fetchrow(
                """
                SELECT d.*,
                       c.account_code, c.name as account_name,
                       b.account_name as bank_account_name,
                       j.journal_number
                FROM customer_deposits d
                LEFT JOIN chart_of_accounts c ON d.account_id = c.id
                LEFT JOIN bank_accounts b ON d.bank_account_id = b.id
                LEFT JOIN journal_entries j ON d.journal_id = j.id
                WHERE d.id = $1 AND d.tenant_id = $2
            """,
                deposit_id,
                ctx["tenant_id"],
            )

            if not dep:
                raise HTTPException(
                    status_code=404, detail="Customer deposit not found"
                )

            # Get applications with invoice numbers
            applications = await conn.fetch(
                """
                SELECT a.*, s.invoice_number
                FROM customer_deposit_applications a
                LEFT JOIN sales_invoices s ON a.invoice_id = s.id
                WHERE a.deposit_id = $1
                ORDER BY a.application_date
            """,
                deposit_id,
            )

            # Get refunds
            refunds = await conn.fetch(
                """
                SELECT * FROM customer_deposit_refunds
                WHERE deposit_id = $1
                ORDER BY refund_date
            """,
                deposit_id,
            )

            # Build response
            remaining = (
                dep["amount"]
                - (dep["amount_applied"] or 0)
                - (dep["amount_refunded"] or 0)
            )

            return {
                "success": True,
                "data": {
                    "id": str(dep["id"]),
                    "deposit_number": dep["deposit_number"],
                    "customer_id": dep["customer_id"],
                    "customer_name": dep["customer_name"],
                    "amount": dep["amount"],
                    "amount_applied": dep["amount_applied"] or 0,
                    "amount_refunded": dep["amount_refunded"] or 0,
                    "remaining_amount": remaining,
                    "deposit_date": dep["deposit_date"].isoformat(),
                    "payment_method": dep["payment_method"],
                    "account_id": str(dep["account_id"]) if dep["account_id"] else None,
                    "account_code": dep["account_code"],
                    "account_name": dep["account_name"],
                    "bank_account_id": str(dep["bank_account_id"])
                    if dep["bank_account_id"]
                    else None,
                    "bank_account_name": dep["bank_account_name"],
                    "reference": dep["reference"],
                    "notes": dep["notes"],
                    "status": dep["status"],
                    "journal_id": str(dep["journal_id"]) if dep["journal_id"] else None,
                    "journal_number": dep["journal_number"],
                    "applications": [
                        {
                            "id": str(app["id"]),
                            "invoice_id": str(app["invoice_id"]),
                            "invoice_number": app["invoice_number"],
                            "amount_applied": app["amount_applied"],
                            "application_date": app["application_date"].isoformat(),
                            "created_at": app["created_at"].isoformat(),
                        }
                        for app in applications
                    ],
                    "refunds": [
                        {
                            "id": str(ref["id"]),
                            "amount": ref["amount"],
                            "refund_date": ref["refund_date"].isoformat(),
                            "payment_method": ref["payment_method"],
                            "account_id": str(ref["account_id"]),
                            "reference": ref["reference"],
                            "notes": ref["notes"],
                            "created_at": ref["created_at"].isoformat(),
                        }
                        for ref in refunds
                    ],
                    "posted_at": dep["posted_at"].isoformat()
                    if dep["posted_at"]
                    else None,
                    "posted_by": str(dep["posted_by"]) if dep["posted_by"] else None,
                    "voided_at": dep["voided_at"].isoformat()
                    if dep["voided_at"]
                    else None,
                    "voided_reason": dep["voided_reason"],
                    "created_at": dep["created_at"].isoformat(),
                    "updated_at": dep["updated_at"].isoformat(),
                    "created_by": str(dep["created_by"]) if dep["created_by"] else None,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get customer deposit")


# =============================================================================
# CREATE CUSTOMER DEPOSIT (DRAFT)
# =============================================================================


@router.post("", response_model=CustomerDepositResponse, status_code=201)
async def create_customer_deposit(request: Request, body: CreateCustomerDepositRequest):
    """
    Create a new customer deposit in draft status.

    Draft deposits can be edited before posting.
    If auto_post=True, will immediately post to accounting.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        # Fase C1.4: precondition gate (one-time per process). Required
        # because auto_post=True triggers _post_deposit which resolves
        # CUSTOMER_DEPOSIT_LIABILITY via role mapping.
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # FIX_P3_BRIDGE 2026-06-16 (b): create idempotency. A deposit is
                # money-in; a double-submit (double-click / retry) must NOT
                # record cash twice. Law 13 advisory lock serializes concurrent
                # creates that share an idempotency_key so the pre-check below is
                # race-safe; the partial UNIQUE index (V179) is the backstop.
                if body.idempotency_key:
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"CUSTOMER_DEPOSIT_CREATE:{ctx['tenant_id']}:{body.idempotency_key}",
                    )
                    existing = await conn.fetchrow(
                        """
                        SELECT id, deposit_number, amount, status
                        FROM customer_deposits
                        WHERE tenant_id = $1 AND idempotency_key = $2
                        """,
                        ctx["tenant_id"],
                        body.idempotency_key,
                    )
                    if existing:
                        logger.info(
                            f"Customer deposit idempotent hit: key={body.idempotency_key} "
                            f"-> existing {existing['deposit_number']} ({existing['id']})"
                        )
                        return {
                            "success": True,
                            "message": (
                                f"Customer deposit {existing['deposit_number']} "
                                "already exists (idempotent)"
                            ),
                            "data": {
                                "id": str(existing["id"]),
                                "deposit_number": existing["deposit_number"],
                                "amount": int(existing["amount"]),
                                "status": existing["status"],
                            },
                        }

                # Validate account exists and is asset type
                account = await conn.fetchrow(
                    """
                    SELECT id, account_code, account_type FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """,
                    UUID(body.account_id),
                    ctx["tenant_id"],
                )

                if not account:
                    raise HTTPException(
                        status_code=400, detail="Payment account not found"
                    )

                if account["account_type"] != "ASSET":
                    raise HTTPException(
                        status_code=400,
                        detail="Payment account must be an asset account (Kas/Bank)",
                    )

                # Generate deposit number
                dep_number = await conn.fetchval(
                    "SELECT generate_customer_deposit_number($1, 'DEP')",
                    ctx["tenant_id"],
                )

                # Insert deposit
                # FIX_P3_BRIDGE 2026-06-16 (a): persist spine linkage
                # (quote_id / sales_order_id) and the create idempotency_key.
                try:
                    dep_id = await conn.fetchval(
                        """
                        INSERT INTO customer_deposits (
                            tenant_id, deposit_number, customer_id, customer_name,
                            amount, deposit_date, payment_method,
                            account_id, bank_account_id, reference, notes,
                            status, created_by,
                            quote_id, sales_order_id, idempotency_key
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12, $13, $14, $15)
                        RETURNING id
                    """,
                        ctx["tenant_id"],
                        dep_number,
                        body.customer_id,
                        body.customer_name,
                        body.amount,
                        body.deposit_date,
                        body.payment_method,
                        UUID(body.account_id),
                        UUID(body.bank_account_id) if body.bank_account_id else None,
                        body.reference,
                        body.notes,
                        ctx["user_id"],
                        UUID(body.quote_id) if body.quote_id else None,
                        UUID(body.sales_order_id) if body.sales_order_id else None,
                        body.idempotency_key,
                    )
                except asyncpg.exceptions.UniqueViolationError:
                    # FIX_P3_BRIDGE 2026-06-16 (b): lost the create race on the
                    # idempotency_key (partial UNIQUE index V179). Re-fetch and
                    # return the row the winning txn inserted (money-in stays
                    # single-recorded). The advisory lock above usually prevents
                    # reaching here, but the index is the hard guarantee.
                    winner = await conn.fetchrow(
                        """
                        SELECT id, deposit_number, amount, status
                        FROM customer_deposits
                        WHERE tenant_id = $1 AND idempotency_key = $2
                        """,
                        ctx["tenant_id"],
                        body.idempotency_key,
                    )
                    if winner:
                        return {
                            "success": True,
                            "message": (
                                f"Customer deposit {winner['deposit_number']} "
                                "already exists (idempotent)"
                            ),
                            "data": {
                                "id": str(winner["id"]),
                                "deposit_number": winner["deposit_number"],
                                "amount": int(winner["amount"]),
                                "status": winner["status"],
                            },
                        }
                    raise

                logger.info(f"Customer deposit created: {dep_id}, number={dep_number}")

                result = {
                    "success": True,
                    "message": "Customer deposit created successfully",
                    "data": {
                        "id": str(dep_id),
                        "deposit_number": dep_number,
                        "amount": body.amount,
                        "status": "draft",
                    },
                }

                # Auto post if requested
                if body.auto_post:
                    # FIX_P1_DEPOSIT 2026-06-16 (c): create+auto_post must hold
                    # the SAME advisory lock as the standalone /post endpoint
                    # (Law 13). dep_id is server-generated, so the lock is
                    # acquired here once it is known, before _post_deposit.
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"DEPOSIT_POST:{dep_id}",
                    )
                    post_result = await _post_deposit(conn, ctx, dep_id)
                    result["data"]["status"] = "posted"
                    result["data"]["journal_id"] = post_result.get("journal_id")
                    result["message"] = "Customer deposit created and posted"

                return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating customer deposit: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create customer deposit")


# =============================================================================
# UPDATE CUSTOMER DEPOSIT (DRAFT ONLY)
# =============================================================================


@router.patch("/{deposit_id}", response_model=CustomerDepositResponse)
async def update_customer_deposit(
    request: Request, deposit_id: UUID, body: UpdateCustomerDepositRequest
):
    """
    Update a draft customer deposit.

    Only draft deposits can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Check status
                dep = await conn.fetchrow(
                    """
                    SELECT id, status FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """,
                    deposit_id,
                    ctx["tenant_id"],
                )

                if not dep:
                    raise HTTPException(
                        status_code=404, detail="Customer deposit not found"
                    )

                if dep["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft deposits can be updated"
                    )

                # Build update
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(deposit_id)},
                    }

                # Validate account if provided
                if "account_id" in update_data and update_data["account_id"]:
                    account = await conn.fetchrow(
                        """
                        SELECT id, account_type FROM chart_of_accounts
                        WHERE id = $1 AND tenant_id = $2
                    """,
                        UUID(update_data["account_id"]),
                        ctx["tenant_id"],
                    )

                    if not account:
                        raise HTTPException(
                            status_code=400, detail="Payment account not found"
                        )

                    if account["account_type"] != "ASSET":
                        raise HTTPException(
                            status_code=400,
                            detail="Payment account must be an asset account",
                        )

                # Build update query
                updates = []
                params = []
                param_idx = 1

                for field, value in update_data.items():
                    if field in ("account_id", "bank_account_id") and value:
                        updates.append(f"{field} = ${param_idx}")
                        params.append(UUID(value))
                    else:
                        updates.append(f"{field} = ${param_idx}")
                        params.append(value)
                    param_idx += 1

                params.extend([deposit_id, ctx["tenant_id"]])
                query = f"""
                    UPDATE customer_deposits
                    SET {', '.join(updates)}, updated_at = NOW()
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(query, *params)

                logger.info(f"Customer deposit updated: {deposit_id}")

                return {
                    "success": True,
                    "message": "Customer deposit updated successfully",
                    "data": {"id": str(deposit_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error updating customer deposit {deposit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to update customer deposit")


# =============================================================================
# DELETE CUSTOMER DEPOSIT (DRAFT ONLY)
# =============================================================================


@router.delete("/{deposit_id}", response_model=CustomerDepositResponse)
async def delete_customer_deposit(request: Request, deposit_id: UUID):
    """
    Delete a draft customer deposit.

    Only draft deposits can be deleted. Use void for posted deposits.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Check status
            dep = await conn.fetchrow(
                """
                SELECT id, status, deposit_number FROM customer_deposits
                WHERE id = $1 AND tenant_id = $2
            """,
                deposit_id,
                ctx["tenant_id"],
            )

            if not dep:
                raise HTTPException(
                    status_code=404, detail="Customer deposit not found"
                )

            if dep["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft deposits can be deleted. Use void for posted.",
                )

            # Delete
            await conn.execute(
                "DELETE FROM customer_deposits WHERE id = $1", deposit_id
            )

            logger.info(f"Customer deposit deleted: {deposit_id}")

            return {
                "success": True,
                "message": "Customer deposit deleted successfully",
                "data": {
                    "id": str(deposit_id),
                    "deposit_number": dep["deposit_number"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error deleting customer deposit {deposit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to delete customer deposit")


# =============================================================================
# INTERNAL: POST DEPOSIT
# =============================================================================


async def _post_deposit(conn, ctx: dict, deposit_id: UUID) -> dict:
    """Internal function to post a deposit to accounting."""
    # Get deposit
    dep = await conn.fetchrow(
        """
        SELECT * FROM customer_deposits
        WHERE id = $1 AND tenant_id = $2
    """,
        deposit_id,
        ctx["tenant_id"],
    )

    if not dep:
        raise HTTPException(status_code=404, detail="Customer deposit not found")

    if dep["status"] != "draft":
        raise HTTPException(
            status_code=400, detail=f"Cannot post deposit with status '{dep['status']}'"
        )

    # Law 5: Period lock check
    period_row = await conn.fetchrow(
        "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
        ctx["tenant_id"],
        dep["deposit_date"],
    )
    if period_row and period_row["status"] != "OPEN":
        raise HTTPException(
            status_code=400, detail=f"Periode akuntansi sudah {period_row['status']}"
        )

    # Fase C1.4: Resolve CUSTOMER_DEPOSIT_LIABILITY via role mapping (Law 27).
    # Precondition gate ensures every tenant is mapped; raises
    # AccountRoleUnmappedError loud if any regression slips in.
    deposit_account_id = await resolve_account_id_by_role(
        conn, ctx["tenant_id"], AccountRole.CUSTOMER_DEPOSIT_LIABILITY
    )

    # Fase C1.4 NULL guard (Law 4 consistency C1.2/C1.3): dep["account_id"]
    # is the user-picked Dr Kas/Bank — validated as ASSET on create. If
    # somehow NULL at this point, raise 422 rather than silently inserting
    # a NULL account_id (which would fail FK or produce unbalanced journal).
    if not dep["account_id"]:
        raise HTTPException(
            status_code=422,
            detail=(
                "Akun kas/bank tidak tersedia untuk customer deposit. "
                "account_id is required on the deposit record."
            ),
        )

    # Create journal entry
    journal_id = uuid_module.uuid4()
    trace_id = uuid_module.uuid4()

    journal_number = await conn.fetchval(
        """
        SELECT get_next_journal_number($1, 'DEP')
    """,
        ctx["tenant_id"],
    )

    if not journal_number:
        journal_number = f"DEP-{dep['deposit_number']}"

    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            status, total_debit, total_credit, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'CUSTOMER_DEPOSIT', $6, $7, 'DRAFT', $8, $8, $9)
    """,
        journal_id,
        ctx["tenant_id"],
        journal_number,
        dep["deposit_date"],
        f"Customer Deposit {dep['deposit_number']} - {dep['customer_name']}",
        deposit_id,
        str(trace_id),
        dep["amount"],
        ctx["user_id"],
    )

    # Dr. Cash/Bank
    await conn.execute(
        """
        INSERT INTO journal_lines (
            id, journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, 1, $3, $4, 0, $5)
    """,
        uuid_module.uuid4(),
        journal_id,
        dep["account_id"],
        dep["amount"],
        f"Terima Uang Muka - {dep['deposit_number']}",
    )

    # Cr. Customer Deposit Liability
    await conn.execute(
        """
        INSERT INTO journal_lines (
            id, journal_id, line_number, account_id, debit, credit, memo
        ) VALUES ($1, $2, 2, $3, 0, $4, $5)
    """,
        uuid_module.uuid4(),
        journal_id,
        deposit_account_id,
        dep["amount"],
        f"Uang Muka Pelanggan - {dep['customer_name']}",
    )

    # Law 20: Promote DRAFT -> POSTED after all lines inserted
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
    )

    # === bank_transaction mirror (BankSync Rule 1, R9) ===
    # FIX_R9_DEP_MIRROR (2026-06-20): the old mirror was gated on dep["bank_account_id"]
    # (the bank_accounts PK). But the FE nulls bank_account_id for cash/kas-method
    # deposits while still debiting a bank-linked CoA (dep["account_id"]) -> orphan
    # journal -> BankSync Rule 9 gap. Reverse-lookup the bank_accounts row by the
    # DEBITED CoA (same pattern as receive_payments FIX_R9_RCV_MIRROR); this subsumes
    # the old bank_account_id case (FE sets account_id = that account's coaId) AND
    # catches the null-bank_account_id/account_id-only route. A plain cash/petty-cash
    # CoA with no bank_accounts row legitimately gets no mirror. Idempotent: skip if a
    # mirror for this journal already exists. Canonical helper computes running_balance
    # via trigger (the old raw INSERT hardcoded running_balance=0).
    bank_acct = await conn.fetchrow(
        "SELECT id FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2",
        dep["account_id"],
        ctx["tenant_id"],
    )
    if bank_acct:
        already = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM bank_transactions WHERE journal_id = $1 AND bank_account_id = $2)",
            journal_id,
            bank_acct["id"],
        )
        if not already:
            await create_bank_transaction_for_journal(
                conn,
                tenant_id=ctx["tenant_id"],
                bank_account_id=bank_acct["id"],
                journal_id=journal_id,
                transaction_date=dep["deposit_date"],
                transaction_type="deposit",
                amount=dep["amount"],
                reference_type="CUSTOMER_DEPOSIT",
                reference_id=deposit_id,
                reference_number=dep["reference"],
                description=f"Customer Deposit - {dep['customer_name']}",
                payee_payer=dep["customer_name"],
                created_by=ctx["user_id"],
            )

    # Update deposit status
    await conn.execute(
        """
        UPDATE customer_deposits
        SET status = 'posted', journal_id = $2,
            posted_at = NOW(), posted_by = $3, updated_at = NOW()
        WHERE id = $1
    """,
        deposit_id,
        journal_id,
        ctx["user_id"],
    )

    return {"journal_id": str(journal_id), "journal_number": journal_number}


# =============================================================================
# POST CUSTOMER DEPOSIT TO ACCOUNTING
# =============================================================================


@router.post("/{deposit_id}/post", response_model=CustomerDepositResponse)
async def post_customer_deposit(request: Request, deposit_id: UUID):
    """
    Post customer deposit to accounting.

    Creates journal entry:
    - Dr. Cash/Bank
    - Cr. Customer Deposit Liability

    Changes status from 'draft' to 'posted'.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        # Fase C1.4: precondition gate (one-time per process).
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"DEPOSIT_POST:{deposit_id}",
                )

                result = await _post_deposit(conn, ctx, deposit_id)

                logger.info(
                    f"Customer deposit posted: {deposit_id}, journal={result['journal_id']}"
                )

                return {
                    "success": True,
                    "message": "Customer deposit posted to accounting",
                    "data": {
                        "id": str(deposit_id),
                        "journal_id": result["journal_id"],
                        "journal_number": result["journal_number"],
                        "status": "posted",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post customer deposit")


# =============================================================================
# APPLY CUSTOMER DEPOSIT TO INVOICE(S)
# =============================================================================


@router.post("/{deposit_id}/apply", response_model=CustomerDepositResponse)
async def apply_customer_deposit(
    request: Request, deposit_id: UUID, body: ApplyCustomerDepositRequest
):
    """
    Apply customer deposit to one or more invoices.

    Creates journal entry:
    - Dr. Customer Deposit Liability
    - Cr. Accounts Receivable

    Reduces the invoice's outstanding balance.
    Deposit must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        # Fase C1.4: precondition gate (one-time per process).
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"DEPOSIT:{deposit_id}",
                )

                # Get deposit
                dep = await conn.fetchrow(
                    """
                    SELECT * FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """,
                    deposit_id,
                    ctx["tenant_id"],
                )

                if not dep:
                    raise HTTPException(
                        status_code=404, detail="Customer deposit not found"
                    )

                if dep["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot apply deposit with status '{dep['status']}'",
                    )

                # FIX_P1_DEPOSIT 2026-06-16 (b): authoritative remaining is
                # journal-derived (net movement on CUSTOMER_DEPOSIT_LIABILITY
                # over is_effective journals for this deposit), NOT the cache
                # subtraction. Correct by construction after un-apply (Law 16).
                remaining = await compute_deposit_remaining(
                    conn, ctx["tenant_id"], deposit_id
                )
                total_to_apply = sum(app.amount for app in body.applications)

                if total_to_apply > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Application amount ({total_to_apply}) exceeds remaining balance ({remaining})",
                    )

                application_date = body.application_date or date.today()
                applications_created = []

                # Fase C1.4: Resolve via role mapping (Law 27).
                # FIX: legacy AR_ACCOUNT_CODE constant (hardcoded 1-10300)
                # pointed at Kas Kecil
                # (Petty Cash), NOT Piutang Usaha (which is 1-10400). Apply-
                # deposit credited the wrong account whenever it was used.
                # No production records exist for this path (zero apply
                # journals in DB at migration time) — historical data fix
                # tracked separately if any tenant triggers it later.
                deposit_account_id = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.CUSTOMER_DEPOSIT_LIABILITY
                )
                ar_account_id = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.AR_TRADE
                )

                # FIX_P1_DEPOSIT 2026-06-16 (d): invariant guard #7 — the
                # Cr line below MUST be AR_TRADE, never REVENUE_DEFERRED.
                await _assert_ar_side_is_ar_trade(conn, ctx["tenant_id"], ar_account_id)

                for app in body.applications:
                    # Validate invoice
                    # FIX_P1_DEPOSIT 2026-06-16: latent column bug — sales_invoices
                    # has total_amount, not grand_total (apply path never exercised
                    # against real data before P1). Was raising 500 on every apply.
                    invoice = await conn.fetchrow(
                        """
                        SELECT id, customer_id, customer_name, invoice_number,
                               total_amount, status
                        FROM sales_invoices
                        WHERE id = $1 AND tenant_id = $2
                    """,
                        UUID(app.invoice_id),
                        ctx["tenant_id"],
                    )

                    if not invoice:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invoice {app.invoice_id} not found",
                        )

                    # Check invoice has balance (Law 16: journal-based)
                    invoice_remaining = await get_invoice_remaining_from_journal(
                        conn, ctx["tenant_id"], UUID(app.invoice_id)
                    )
                    if app.amount > invoice_remaining:
                        raise HTTPException(
                            status_code=400,
                            detail="Application amount exceeds invoice remaining balance",
                        )

                    # Check for existing application.
                    # FIX_P1_DEPOSIT 2026-06-16: exclude reversed (un-applied)
                    # applications so the same deposit can be re-applied to the
                    # same invoice after an un-apply (dead-end fixed).
                    existing = await conn.fetchval(
                        """
                        SELECT id FROM customer_deposit_applications
                        WHERE deposit_id = $1 AND invoice_id = $2
                          AND COALESCE(status, 'active') <> 'reversed'
                    """,
                        deposit_id,
                        UUID(app.invoice_id),
                    )

                    if existing:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Deposit already applied to invoice {app.invoice_id}",
                        )

                    # Create journal entry for application
                    journal_id = uuid_module.uuid4()
                    trace_id = uuid_module.uuid4()

                    journal_number = (
                        await conn.fetchval(
                            """
                        SELECT get_next_journal_number($1, 'DA')
                    """,
                            ctx["tenant_id"],
                        )
                        or f"DA-{dep['deposit_number']}"
                    )

                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'DEPOSIT_APPLICATION', $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                        journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        application_date,
                        f"Apply Deposit {dep['deposit_number']} to {invoice['invoice_number']}",
                        deposit_id,
                        str(trace_id),
                        app.amount,
                        ctx["user_id"],
                    )

                    # Dr. Customer Deposit Liability
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        deposit_account_id,
                        app.amount,
                        f"Aplikasi Uang Muka - {invoice['invoice_number']}",
                    )

                    # Cr. Accounts Receivable
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        ar_account_id,
                        app.amount,
                        f"Pelunasan dari Deposit - {dep['deposit_number']}",
                    )

                    # Law 20: Promote DRAFT -> POSTED after all lines inserted
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        journal_id,
                    )

                    # Create application record
                    app_id = uuid_module.uuid4()

                    await conn.execute(
                        """
                        INSERT INTO customer_deposit_applications (
                            id, tenant_id, deposit_id, invoice_id, invoice_number,
                            amount_applied, application_date, journal_id, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                        app_id,
                        ctx["tenant_id"],
                        deposit_id,
                        UUID(app.invoice_id),
                        invoice["invoice_number"],
                        app.amount,
                        application_date,
                        journal_id,
                        ctx["user_id"],
                    )

                    # Update invoice (derive amount_paid from journal-based remaining)
                    new_amount_paid = (
                        invoice["total_amount"] - int(invoice_remaining) + app.amount
                    )
                    new_status = (
                        "paid"
                        if new_amount_paid >= invoice["total_amount"]
                        else invoice["status"]
                    )

                    await conn.execute(
                        """
                        UPDATE sales_invoices
                        SET amount_paid = $2, status = $3, updated_at = NOW()
                        WHERE id = $1
                    """,
                        UUID(app.invoice_id),
                        new_amount_paid,
                        new_status,
                    )

                    # Update AR if exists
                    await conn.execute(
                        """
                        UPDATE accounts_receivable
                        SET amount_paid = amount_paid + $2,
                            status = CASE
                                WHEN amount_paid + $2 >= amount THEN 'PAID'
                                ELSE 'PARTIAL'
                            END,
                            updated_at = NOW()
                        WHERE source_id = $1 AND source_type = 'INVOICE'
                    """,
                        UUID(app.invoice_id),
                        app.amount,
                    )

                    applications_created.append(
                        {
                            "application_id": str(app_id),
                            "invoice_id": app.invoice_id,
                            "invoice_number": invoice["invoice_number"],
                            "amount": app.amount,
                        }
                    )

                # Deposit status will be updated by trigger
                logger.info(
                    f"Customer deposit applied: {deposit_id}, applications={len(applications_created)}"
                )

                return {
                    "success": True,
                    "message": f"Deposit applied to {len(applications_created)} invoice(s)",
                    "data": {
                        "id": str(deposit_id),
                        "applications": applications_created,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error applying customer deposit {deposit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to apply customer deposit")


# =============================================================================
# REVERSE (UN-APPLY) A CUSTOMER DEPOSIT APPLICATION
# FIX_P1_DEPOSIT 2026-06-16 (a)
# =============================================================================


@router.post(
    "/{deposit_id}/applications/{application_id}/reverse",
    response_model=CustomerDepositResponse,
)
async def reverse_customer_deposit_application(
    request: Request, deposit_id: UUID, application_id: UUID
):
    """Reverse (un-apply) a single customer-deposit application.

    Symmetric opposite of the original apply journal:
        original apply : Dr CUSTOMER_DEPOSIT_LIABILITY / Cr AR_TRADE
        reversal       : Dr AR_TRADE / Cr CUSTOMER_DEPOSIT_LIABILITY
    (swap the original lines — same amounts).

    Iron Law compliance:
    - reversal journal carries reversal_of_id = original application journal
      id => is_effective_journal(reversal)=false AND original=false (via
      reversed_by_id). BOTH drop from AR/AP/GL aggregation by construction,
      with NO source_type blacklist dependence (kills BL-08 class).
    - reversed_by_id on customer_deposit_applications row (Law 26 single
      reversal pointer) + status='reversed' + reversed_at.
    - IDEMPOTENT: if already reversed, returns existing reversal (HTTP 200).
    - Law 5 period-open check; Law 13 advisory lock reuses unified DEPOSIT:{deposit_id} key (B1b).
    - After un-apply the deposit available balance rises again and a
      previously-blocked void becomes possible.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()
        await _ensure_role_preconditions(pool)

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 13: reuse the SAME lock key as apply so apply and
                # un-apply on the same deposit serialize and cannot race.
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"DEPOSIT:{deposit_id}",
                )

                # Fetch the application (scoped to deposit + tenant)
                app_row = await conn.fetchrow(
                    """
                    SELECT * FROM customer_deposit_applications
                    WHERE id = $1 AND deposit_id = $2 AND tenant_id = $3
                    """,
                    application_id,
                    deposit_id,
                    ctx["tenant_id"],
                )
                if not app_row:
                    raise HTTPException(
                        status_code=404,
                        detail="Deposit application not found",
                    )

                # Idempotency guard: already reversed -> return existing reversal.
                if (app_row["status"] or "active") == "reversed" or app_row[
                    "reversed_by_id"
                ]:
                    return {
                        "success": True,
                        "message": "Application already reversed (idempotent)",
                        "data": {
                            "id": str(deposit_id),
                            "application_id": str(application_id),
                            "reversal_journal_id": (
                                str(app_row["reversed_by_id"])
                                if app_row["reversed_by_id"]
                                else None
                            ),
                            "status": "reversed",
                        },
                    }

                original_journal_id = app_row["journal_id"]
                if not original_journal_id:
                    raise HTTPException(
                        status_code=400,
                        detail="Application has no journal to reverse",
                    )

                # Law 5: period-open check (reversal posts at today).
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    date.today(),
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Defensive: original must not already be reversed (Law 26).
                orig_je = await conn.fetchrow(
                    "SELECT id, reversed_by_id, status FROM journal_entries WHERE id = $1 AND tenant_id = $2",
                    original_journal_id,
                    ctx["tenant_id"],
                )
                if orig_je and orig_je["reversed_by_id"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Original application journal already reversed",
                    )

                # Fetch original application journal lines (Dr 2-10500 / Cr AR).
                original_lines = await conn.fetch(
                    "SELECT * FROM journal_lines WHERE journal_id = $1 ORDER BY line_number",
                    original_journal_id,
                )
                if not original_lines:
                    raise HTTPException(
                        status_code=400,
                        detail="Original application journal has no lines",
                    )

                # FIX_P1_DEPOSIT 2026-06-16 (d): invariant guard #7 — the AR
                # side of the original apply (credit > 0) MUST be AR_TRADE.
                ar_side_line = next(
                    (ln for ln in original_lines if (ln["credit"] or 0) > 0),
                    None,
                )
                if ar_side_line is None:
                    raise HTTPException(
                        status_code=500,
                        detail="Invariant #7: original apply journal missing AR-side credit line",
                    )
                await _assert_ar_side_is_ar_trade(
                    conn, ctx["tenant_id"], ar_side_line["account_id"]
                )

                reversal_journal_id = uuid_module.uuid4()
                reversal_amount = app_row["amount_applied"]

                journal_number = (
                    await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'RV')", ctx["tenant_id"]
                    )
                    or f"RV-DA-{str(application_id)[:8]}"
                )

                # Reversal header — MANDATORY reversal_of_id = original apply je.
                #
                # FIX_P1_DEPOSIT 2026-06-16 OPTION B: source_id = the INVOICE id
                # (the real obligation that was settled), NOT the deposit id.
                # This is the SAME ledger-honest mechanism an invoice posting
                # uses to satisfy guard_arap_requires_obligation: the un-apply
                # DEBITS RECEIVABLE, so the guard's AR-debit branch fires and
                # checks EXISTS(SELECT 1 FROM sales_invoices WHERE id = source_id).
                # Carrying the invoice id makes that EXISTS true -> the guard
                # passes NATURALLY because the obligation genuinely exists, with
                # NO source_type whitelist (Option A whitelist removed in V177).
                #
                # Balance integrity is unaffected: this reversal carries
                # reversal_of_id, and the original apply gets reversed_by_id set
                # below, so is_effective_journal() drops BOTH from the
                # journal-derived deposit balance (net movement on 2-10500). The
                # reversal therefore need not (and does not) join customer_deposits
                # via source_id -- the restored balance comes from is_effective
                # dropping the now-reversed original apply, leaving only the POST.
                invoice_obligation_id = app_row["invoice_id"]
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, reversal_of_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'DEPOSIT_APPLICATION', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                    reversal_journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    f"Un-apply Deposit application {app_row['invoice_number'] or application_id}",
                    invoice_obligation_id,
                    original_journal_id,
                    reversal_amount,
                    ctx["user_id"],
                )

                # Reversed lines (swap debit/credit) -> Dr AR / Cr 2-10500.
                for idx, line in enumerate(original_lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        uuid_module.uuid4(),
                        reversal_journal_id,
                        idx,
                        line["account_id"],
                        line["credit"],  # swap
                        line["debit"],  # swap
                        f"Reversal - {line['memo'] or ''}",
                    )

                # Law 20: promote DRAFT -> POSTED.
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    reversal_journal_id,
                )

                # Mark original application journal reversed (drops via
                # reversed_by_id; reversal drops via reversal_of_id).
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET reversed_by_id = $2
                    WHERE id = $1
                    """,
                    original_journal_id,
                    reversal_journal_id,
                )

                # Law 26: single reversal pointer on the application row +
                # status + reversed_at. This also fires the deposit-status
                # trigger which re-derives the cache (excludes reversed rows).
                await conn.execute(
                    """
                    UPDATE customer_deposit_applications
                    SET status = 'reversed',
                        reversed_by_id = $2,
                        reversed_at = NOW()
                    WHERE id = $1
                    """,
                    application_id,
                    reversal_journal_id,
                )

                # Restore invoice cache: un-applying credits AR back, so the
                # invoice outstanding rises again. Re-derive amount_paid from
                # the journal-based remaining (Law 16) after the reversal.
                inv_id = app_row["invoice_id"]
                invoice = await conn.fetchrow(
                    "SELECT id, total_amount, status FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                    inv_id,
                    ctx["tenant_id"],
                )
                if invoice:
                    invoice_remaining = await get_invoice_remaining_from_journal(
                        conn, ctx["tenant_id"], inv_id
                    )
                    new_amount_paid = int(invoice["total_amount"]) - int(
                        invoice_remaining
                    )
                    if new_amount_paid < 0:
                        new_amount_paid = 0
                    # Revert: if no longer fully paid, demote 'paid' back to
                    # 'posted' (posted-unsettled). Other states unchanged.
                    new_status = (
                        "paid"
                        if new_amount_paid >= int(invoice["total_amount"])
                        else (
                            "posted"
                            if invoice["status"] == "paid"
                            else invoice["status"]
                        )
                    )
                    await conn.execute(
                        """
                        UPDATE sales_invoices
                        SET amount_paid = $2, status = $3, updated_at = NOW()
                        WHERE id = $1
                        """,
                        inv_id,
                        new_amount_paid,
                        new_status,
                    )
                    # Mirror accounts_receivable cache if a row exists.
                    await conn.execute(
                        """
                        UPDATE accounts_receivable
                        SET amount_paid = GREATEST(amount_paid - $2, 0),
                            status = CASE
                                WHEN GREATEST(amount_paid - $2, 0) >= amount THEN 'PAID'
                                WHEN GREATEST(amount_paid - $2, 0) > 0 THEN 'PARTIAL'
                                ELSE 'OPEN'
                            END,
                            updated_at = NOW()
                        WHERE source_id = $1 AND source_type = 'INVOICE'
                        """,
                        inv_id,
                        reversal_amount,
                    )

                logger.info(
                    f"Customer deposit application reversed: deposit={deposit_id}, "
                    f"application={application_id}, reversal_journal={reversal_journal_id}"
                )

                return {
                    "success": True,
                    "message": "Deposit application reversed (un-applied)",
                    "data": {
                        "id": str(deposit_id),
                        "application_id": str(application_id),
                        "reversal_journal_id": str(reversal_journal_id),
                        "status": "reversed",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error reversing deposit application {application_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail="Failed to reverse deposit application"
        )


# =============================================================================
# REFUND CUSTOMER DEPOSIT
# =============================================================================


@router.post("/{deposit_id}/refund", response_model=CustomerDepositResponse)
async def refund_customer_deposit(
    request: Request, deposit_id: UUID, body: RefundCustomerDepositRequest
):
    """
    Issue refund to customer from deposit.

    Creates journal entry:
    - Dr. Customer Deposit Liability
    - Cr. Cash/Bank

    Deposit must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        # Fase C1.4: precondition gate (one-time per process).
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"DEPOSIT:{deposit_id}",
                )

                # Get deposit
                dep = await conn.fetchrow(
                    """
                    SELECT * FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """,
                    deposit_id,
                    ctx["tenant_id"],
                )

                if not dep:
                    raise HTTPException(
                        status_code=404, detail="Customer deposit not found"
                    )

                if dep["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot refund deposit with status '{dep['status']}'",
                    )

                # Check remaining
                remaining = (
                    dep["amount"]
                    - (dep["amount_applied"] or 0)
                    - (dep["amount_refunded"] or 0)
                )

                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refund amount ({body.amount}) exceeds remaining balance ({remaining})",
                    )

                # Validate account
                account = await conn.fetchrow(
                    """
                    SELECT id, account_code, name, account_type FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """,
                    UUID(body.account_id),
                    ctx["tenant_id"],
                )

                if not account:
                    raise HTTPException(
                        status_code=400, detail="Payment account not found"
                    )

                if account["account_type"] != "ASSET":
                    raise HTTPException(
                        status_code=400,
                        detail="Payment account must be an asset account",
                    )

                # Fase C1.4: Resolve CUSTOMER_DEPOSIT_LIABILITY via role
                # mapping (Law 27). body.account_id is the user-picked Cr
                # Kas/Bank (validated ASSET above).
                deposit_account_id = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.CUSTOMER_DEPOSIT_LIABILITY
                )

                # Law 5: Period lock check
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    body.refund_date,
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Create refund journal
                refund_id = uuid_module.uuid4()
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                journal_number = (
                    await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'DR')", ctx["tenant_id"]
                    )
                    or f"DR-{dep['deposit_number']}"
                )

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'DEPOSIT_REFUND', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.refund_date,
                    f"Refund Deposit {dep['deposit_number']} - {dep['customer_name']}",
                    deposit_id,
                    str(trace_id),
                    body.amount,
                    ctx["user_id"],
                )

                # Dr. Customer Deposit Liability
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    deposit_account_id,
                    body.amount,
                    f"Refund Uang Muka - {dep['deposit_number']}",
                )

                # Cr. Cash/Bank
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    UUID(body.account_id),
                    body.amount,
                    f"Bayar Refund - {dep['customer_name']}",
                )

                # Law 20: Promote DRAFT -> POSTED after all lines inserted
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # Create bank transaction if bank account specified — canonical
                # BankSync helper (Rule 1). FIX: previous inline INSERT used columns
                # reference/source_type/source_id that DO NOT EXIST on bank_transactions
                # (real: reference_number/reference_type/reference_id) -> refund-to-bank 500.
                if body.bank_account_id:
                    await create_bank_transaction_for_journal(
                        conn,
                        tenant_id=ctx["tenant_id"],
                        bank_account_id=UUID(body.bank_account_id),
                        journal_id=journal_id,
                        transaction_date=body.refund_date,
                        transaction_type="withdrawal",
                        amount=-body.amount,
                        reference_type="DEPOSIT_REFUND",
                        reference_id=deposit_id,
                        reference_number=body.reference,
                        description=f"Deposit Refund - {dep['customer_name']}",
                        created_by=ctx["user_id"],
                    )

                # Create refund record
                await conn.execute(
                    """
                    INSERT INTO customer_deposit_refunds (
                        id, tenant_id, deposit_id, amount, refund_date,
                        payment_method, account_id, bank_account_id,
                        reference, notes, journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    refund_id,
                    ctx["tenant_id"],
                    deposit_id,
                    body.amount,
                    body.refund_date,
                    body.payment_method,
                    UUID(body.account_id),
                    UUID(body.bank_account_id) if body.bank_account_id else None,
                    body.reference,
                    body.notes,
                    journal_id,
                    ctx["user_id"],
                )

                # Status will be updated by trigger
                logger.info(
                    f"Customer deposit refund issued: {deposit_id}, amount={body.amount}"
                )

                return {
                    "success": True,
                    "message": "Refund issued successfully",
                    "data": {
                        "id": str(deposit_id),
                        "refund_id": str(refund_id),
                        "journal_id": str(journal_id),
                        "amount": body.amount,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error refunding customer deposit {deposit_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to refund customer deposit")


# =============================================================================
# VOID CUSTOMER DEPOSIT
# =============================================================================


@router.post("/{deposit_id}/void", response_model=CustomerDepositResponse)
async def void_customer_deposit(
    request: Request, deposit_id: UUID, body: VoidCustomerDepositRequest
):
    """
    Void a customer deposit.

    Creates reversal journal entry.
    Deposit must have no applications or refunds.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        # Fase C1.4: precondition gate (one-time per process).
        # Void reuses original journal account_ids (swap debit/credit) so
        # it does not call resolve_account_id_by_role directly, but we
        # still gate here so any process touching this module hits the
        # precondition audit once.
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"DEPOSIT:{deposit_id}",
                )

                # Get deposit
                dep = await conn.fetchrow(
                    """
                    SELECT * FROM customer_deposits
                    WHERE id = $1 AND tenant_id = $2
                """,
                    deposit_id,
                    ctx["tenant_id"],
                )

                if not dep:
                    raise HTTPException(
                        status_code=404, detail="Customer deposit not found"
                    )

                if dep["status"] == "void":
                    raise HTTPException(
                        status_code=400, detail="Deposit already voided"
                    )

                if dep["status"] == "draft":
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM customer_deposits WHERE id = $1", deposit_id
                    )
                    return {
                        "success": True,
                        "message": "Draft deposit deleted",
                        "data": {"id": str(deposit_id)},
                    }

                # Check for applications or refunds
                if (dep["amount_applied"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void deposit with applications. Reverse applications first.",
                    )

                if (dep["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void deposit with refunds. Reverse refunds first.",
                    )

                    # Law 5: Period lock check
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    date.today(),
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Create reversal journal if original was posted
                if dep["journal_id"]:
                    reversal_journal_id = uuid_module.uuid4()

                    # Get original journal lines
                    original_lines = await conn.fetch(
                        """
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """,
                        dep["journal_id"],
                    )

                    journal_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'RV')", ctx["tenant_id"]
                        )
                        or f"RV-{dep['deposit_number']}"
                    )

                    # Create reversal header
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, CURRENT_DATE, $4, 'CUSTOMER_DEPOSIT', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {dep['deposit_number']} - {dep['customer_name']}",
                        deposit_id,
                        dep["journal_id"],
                        dep["amount"],
                        ctx["user_id"],
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            reversal_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # Swap
                            line["debit"],  # Swap
                            f"Reversal - {line['memo'] or ''}",
                        )

                        # Law 20: Promote DRAFT -> POSTED after all lines inserted
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        reversal_journal_id,
                    )

                    # Mark original journal as reversed -- Law 2: keep original
                    # POSTED + reversed_by_id/reversed_at (NEVER flip to VOID, which
                    # excludes it from the POSTED ledger sum and breaks BankSync R9
                    # against the negating bank mirror). Matches sales_invoices void.
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()
                        WHERE id = $1
                    """,
                        dep["journal_id"],
                        reversal_journal_id,
                    )

                # ── Bank mirror reversal (BankSync Rule 3, canonical helper) ──
                # FIX: locate the ORIGINAL bank_transaction by journal_id (reliable),
                # NOT by reference_type='customer_deposit' (the create path writes
                # 'CUSTOMER_DEPOSIT' -- casing mismatch left the mirror un-reversed ->
                # inflated bank balance). The canonical helper inserts a NEGATING
                # mirror linked to the reversal journal; we do NOT void the original
                # row (negation nets to zero, matching the POSTED ledger).
                if dep["journal_id"]:
                    orig_bank_txn = await conn.fetchrow(
                        """
                        SELECT id FROM bank_transactions
                        WHERE journal_id = $1 AND tenant_id = $2
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        dep["journal_id"],
                        ctx["tenant_id"],
                    )
                    if orig_bank_txn:
                        await create_reversal_bank_transaction(
                            conn,
                            tenant_id=ctx["tenant_id"],
                            original_bank_transaction_id=orig_bank_txn["id"],
                            reversal_journal_id=reversal_journal_id,
                            created_by=ctx["user_id"],
                            description_prefix="[VOID]",
                        )

                # Update deposit status
                await conn.execute(
                    """
                    UPDATE customer_deposits
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    deposit_id,
                    ctx["user_id"],
                    body.reason,
                )

                logger.info(f"Customer deposit voided: {deposit_id}")

                return {
                    "success": True,
                    "message": "Customer deposit voided successfully",
                    "data": {"id": str(deposit_id), "status": "void"},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding customer deposit {deposit_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void customer deposit")


# =============================================================================
# LIST DEPOSITS FOR CUSTOMER
# =============================================================================


@router.get("/customer/{customer_id}", response_model=CustomerDepositListResponse)
async def list_customer_deposits_by_customer(
    request: Request,
    customer_id: str,
    status: Optional[Literal["all", "posted", "partial"]] = Query("all"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List available deposits for a specific customer."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Build conditions
            conditions = ["tenant_id = $1", "customer_id = $2"]
            params = [ctx["tenant_id"], customer_id]
            param_idx = 3

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1
            else:
                # Only show deposits with available balance
                conditions.append("status IN ('posted', 'partial')")

            where_clause = " AND ".join(conditions)

            # Count
            count_query = f"SELECT COUNT(*) FROM customer_deposits WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, deposit_number, customer_id, customer_name,
                       deposit_date, amount, amount_applied, amount_refunded,
                       status, payment_method, reference, created_at
                FROM customer_deposits
                WHERE {where_clause}
                ORDER BY deposit_date DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "deposit_number": row["deposit_number"],
                    "customer_id": row["customer_id"],
                    "customer_name": row["customer_name"],
                    "deposit_date": row["deposit_date"].isoformat(),
                    "amount": row["amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": row["amount"]
                    - (row["amount_applied"] or 0)
                    - (row["amount_refunded"] or 0),
                    "status": row["status"],
                    "payment_method": row["payment_method"],
                    "reference": row["reference"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error listing deposits for customer {customer_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to list customer deposits")


# =============================================================================
# GENERATE PDF — Bukti Penerimaan / Kwitansi (Uang Muka)
# =============================================================================
from io import BytesIO as _BytesIO
from fastapi.responses import StreamingResponse as _StreamingResponse
from ..services.pdf_service import get_pdf_service as _get_pdf_service
import base64 as _base64
from pathlib import Path as _Path


def _terbilang(n: int) -> str:
    """Konversi bilangan bulat rupiah ke kata Bahasa Indonesia."""
    n = int(n)
    if n == 0:
        return "Nol Rupiah"
    satuan = [
        "", "Satu", "Dua", "Tiga", "Empat", "Lima",
        "Enam", "Tujuh", "Delapan", "Sembilan", "Sepuluh", "Sebelas",
    ]

    def _to_words(x: int) -> str:
        if x < 12:
            return satuan[x]
        elif x < 20:
            return _to_words(x - 10) + " Belas"
        elif x < 100:
            return _to_words(x // 10) + " Puluh" + (
                " " + _to_words(x % 10) if x % 10 else ""
            )
        elif x < 200:
            return "Seratus" + (" " + _to_words(x - 100) if x - 100 else "")
        elif x < 1000:
            return _to_words(x // 100) + " Ratus" + (
                " " + _to_words(x % 100) if x % 100 else ""
            )
        elif x < 2000:
            return "Seribu" + (" " + _to_words(x - 1000) if x - 1000 else "")
        elif x < 1_000_000:
            return _to_words(x // 1000) + " Ribu" + (
                " " + _to_words(x % 1000) if x % 1000 else ""
            )
        elif x < 1_000_000_000:
            return _to_words(x // 1_000_000) + " Juta" + (
                " " + _to_words(x % 1_000_000) if x % 1_000_000 else ""
            )
        elif x < 1_000_000_000_000:
            return _to_words(x // 1_000_000_000) + " Miliar" + (
                " " + _to_words(x % 1_000_000_000) if x % 1_000_000_000 else ""
            )
        else:
            return _to_words(x // 1_000_000_000_000) + " Triliun" + (
                " " + _to_words(x % 1_000_000_000_000)
                if x % 1_000_000_000_000
                else ""
            )

    return _to_words(n).strip() + " Rupiah"


@router.get("/{deposit_id}/pdf")
async def get_customer_deposit_pdf(
    request: Request,
    deposit_id: str,
    format: Literal["url", "inline"] = Query(
        "inline",
        description="Response format: 'inline' returns PDF bytes",
    ),
):
    """Generate Bukti Penerimaan (Kwitansi) PDF for a customer deposit (Uang Muka)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            dep = await conn.fetchrow(
                "SELECT * FROM customer_deposits WHERE id = $1 AND tenant_id = $2",
                uuid_module.UUID(deposit_id),
                ctx["tenant_id"],
            )
            if not dep:
                raise HTTPException(status_code=404, detail="Customer deposit not found")

            # Bank account name (optional)
            bank_name = None
            if dep["bank_account_id"]:
                bank_row = await conn.fetchrow(
                    "SELECT account_name, bank_name FROM bank_accounts WHERE id = $1",
                    dep["bank_account_id"],
                )
                if bank_row:
                    bank_name = bank_row["account_name"] or bank_row["bank_name"]

            # Linked reference: sales order > quote > deposit number
            purpose_ref = dep["deposit_number"]
            if dep["sales_order_id"]:
                so_row = await conn.fetchrow(
                    "SELECT order_number FROM sales_orders WHERE id = $1",
                    dep["sales_order_id"],
                )
                if so_row and so_row["order_number"]:
                    purpose_ref = so_row["order_number"]
            elif dep["quote_id"]:
                q_row = await conn.fetchrow(
                    "SELECT quote_number FROM quotes WHERE id = $1",
                    dep["quote_id"],
                )
                if q_row and q_row["quote_number"]:
                    purpose_ref = q_row["quote_number"]

            # Remaining = amount - applied - refunded
            _amt = int(dep["amount"] or 0)
            _applied = int(dep["amount_applied"] or 0)
            _refunded = int(dep["amount_refunded"] or 0)
            remaining = _amt - _applied - _refunded

            method_label = (
                "Tunai" if (dep["payment_method"] or "").lower() == "cash"
                else "Transfer Bank"
            )

            receipt_data = {
                "receipt_number": dep["deposit_number"],
                "receipt_date": dep["deposit_date"].isoformat()
                if dep["deposit_date"] else None,
                "payer_name": dep["customer_name"],
                "amount": _amt,
                "amount_words": _terbilang(_amt),
                "method": method_label,
                "bank_name": bank_name,
                "purpose_label": "Uang Muka",
                "purpose_ref": purpose_ref,
                "remaining": remaining,
                "notes": dep["notes"],
            }

            # Tenant info for header
            tenant_row = await conn.fetchrow(
                'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
                ctx["tenant_id"],
            )
            if tenant_row:
                tenant_info = {
                    "name": tenant_row["display_name"],
                    "address": tenant_row["address"],
                    "phone": tenant_row["phone"],
                    "logo_url": tenant_row["logo_url"],
                }
            else:
                tenant_info = {
                    "name": ctx["tenant_id"],
                    "address": None,
                    "phone": None,
                    "logo_url": None,
                }

            _logo_data = None
            _logo_filename = tenant_info.get("logo_url")
            if _logo_filename:
                _logo_path = (
                    _Path(__file__).parent.parent / "static" / "logos" / _logo_filename
                )
                if _logo_path.exists():
                    with open(_logo_path, "rb") as _lf:
                        _logo_b64 = _base64.b64encode(_lf.read()).decode()
                    _logo_data = f"data:image/png;base64,{_logo_b64}"
            tenant_info["logo_data"] = _logo_data

        pdf_service = _get_pdf_service()
        pdf_bytes = pdf_service.generate_receipt_pdf(receipt_data, tenant_info)

        num = dep["deposit_number"] or str(deposit_id)[:8]
        filename = f"Kwitansi-{num}.pdf"

        return _StreamingResponse(
            _BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating deposit receipt PDF: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate receipt PDF")
