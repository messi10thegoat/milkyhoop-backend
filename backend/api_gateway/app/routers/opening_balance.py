"""
Opening Balance Router
======================
Endpoints for managing opening balances during tenant onboarding
or fiscal year transitions.

Key Features:
- Create opening balance entries for all accounts
- Auto-balance to Opening Balance Equity (EQUITY_OPENING_BALANCE role)
- Support AR/AP/Inventory subledger opening balances
- Supersede mechanism for updates (audit trail)

Fase C1.5: hardcoded CoA codes replaced with role resolver (Law 27).
- EQUITY_OPENING_BALANCE  (was hardcoded 3-50000)
- AR_TRADE                (was hardcoded 1-10300 in READ filter --
                           WRONG name: 1-10300 is Kas Kecil; Piutang
                           Usaha is 1-10400. Subledger reconciliation
                           warning was silently dead. Fase A finding.)
- AP_TRADE                (was hardcoded 2-10100 -- correct semantic
                           code but still a literal.)
- INVENTORY_MERCHANDISE   (was hardcoded 1-10400 in READ filter --
                           ALSO WRONG: 1-10400 is Piutang Usaha, not
                           inventory. Inventory control is 1-10600. Old
                           filter dead. Fase A finding.)

REVERSE / SUPERSEDE PATH: uses original `account_id` from `journal_lines`
table -- NO role re-resolve. If a tenant remaps EQUITY_OPENING_BALANCE
between the original posting and the supersede, the reversal still
mirrors the original posting (Law 4 / Law 2 integrity).

Single-OB invariant: app-level guard at POST entry (`status='ACTIVE'`
exists check) -- DB trigger `guard_opening_balance` removed per Law 28.
"""

import json
from datetime import datetime
from typing import Optional
from uuid import UUID
from decimal import Decimal

import asyncpg
import structlog
from fastapi import APIRouter, HTTPException, Request, Query
from asyncpg import Connection

from ..schemas.opening_balance import (
    CreateOpeningBalanceRequest,
    UpdateOpeningBalanceRequest,
    OpeningBalanceResponse,
    OpeningBalanceListResponse,
    OpeningBalanceSummaryResponse,
    CreateOpeningBalanceResponse,
    ValidateOpeningBalanceResponse,
    ValidationResult,
    AccountBalanceItem,
    OpeningBalanceData,
    OpeningBalanceSummary,
)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
)
from ..services.role_precondition import assert_required_roles_for_path

logger = structlog.get_logger()

router = APIRouter(prefix="/opening-balance", tags=["opening-balance"])

# Connection pool


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


# Fase C1.5: required role mappings for opening_balance.py posting paths.
#
# EQUITY_OPENING_BALANCE -- balancing leg of the OB journal (Modal Saldo
#                           Awal). Used in both POST (create) and PUT
#                           (supersede / new active journal).
# AR_TRADE               -- READ-side subledger control match for
#                           ar_balances reconciliation warning.
# AP_TRADE               -- READ-side subledger control match for
#                           ap_balances reconciliation warning.
# INVENTORY_MERCHANDISE  -- READ-side subledger control match for
#                           inventory_balances reconciliation warning.
OPENING_BALANCE_REQUIRED_ROLES = [
    AccountRole.EQUITY_OPENING_BALANCE,
    AccountRole.AR_TRADE,
    AccountRole.AP_TRADE,
    AccountRole.INVENTORY_MERCHANDISE,
]

# One-time precondition flag (mirrors Fase C1.2/C1.3/C1.4 pattern).
_precondition_checked = False


async def _ensure_role_preconditions(pool) -> None:
    """Run role-mapping precondition once per process for opening_balance.

    Fails loud (PreconditionFailedError) if any tenant lacks any required
    role mapping. After first successful check the audit is skipped; a
    tenant added later without mapping will still fail loud at
    resolve_account_id_by_role(...) via AccountRoleUnmappedError.
    """
    global _precondition_checked
    if _precondition_checked:
        return
    await assert_required_roles_for_path(
        pool, "opening_balance", OPENING_BALANCE_REQUIRED_ROLES
    )
    _precondition_checked = True


async def _resolve_role_account(
    conn: Connection, tenant_id: str, role_key: str
) -> dict:
    """Resolve role -> full account row (id, code, name, type, normal_balance).

    Raises AccountRoleUnmappedError if role not mapped for tenant
    (Law 27 -- no silent fallback).
    """
    account_id = await resolve_account_id_by_role(conn, tenant_id, role_key)
    row = await conn.fetchrow(
        """
        SELECT id, account_code as code, name, account_type as type, normal_balance
        FROM chart_of_accounts
        WHERE tenant_id = $1 AND id = $2 AND is_active = true
        """,
        tenant_id,
        account_id,
    )
    if not row:
        # Role mapping points to non-existent/inactive account: data integrity bug.
        raise HTTPException(
            status_code=500,
            detail=(
                f"Role {role_key!r} for tenant {tenant_id!r} points to "
                f"account_id={account_id} which is missing or inactive in "
                f"chart_of_accounts. Fix account_roles mapping."
            ),
        )
    return dict(row)


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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


async def get_account_by_code(
    conn: Connection, tenant_id: str, code: str
) -> Optional[dict]:
    """Get account by code."""
    query = """
        SELECT id, account_code as code, name, account_type as type, normal_balance
        FROM chart_of_accounts
        WHERE tenant_id = $1 AND account_code = $2 AND is_active = true
    """
    return await conn.fetchrow(query, tenant_id, code)


async def get_opening_balance_equity_account(conn: Connection, tenant_id: str) -> dict:
    """Get Opening Balance Equity account via role resolver (Law 27).

    Fase C1.5: was hardcoded 3-50000. Now resolves via
    EQUITY_OPENING_BALANCE role. Fails loud (AccountRoleUnmappedError ->
    422) if tenant has no mapping -- never silent fallback.
    """
    return await _resolve_role_account(
        conn, tenant_id, AccountRole.EQUITY_OPENING_BALANCE
    )


async def validate_opening_balance_request(
    conn: Connection, tenant_id: str, request: CreateOpeningBalanceRequest
) -> ValidationResult:
    """Validate opening balance request before processing."""
    errors = []
    warnings = []

    total_debit = 0
    total_credit = 0

    # Validate all account codes exist
    for line in request.accounts:
        account = await get_account_by_code(conn, tenant_id, line.account_code)
        if not account:
            errors.append(
                f"Account code '{line.account_code}' not found in Chart of Accounts"
            )
            continue

        total_debit += line.debit
        total_credit += line.credit

        # Warn if both debit and credit provided for same account
        if line.debit > 0 and line.credit > 0:
            warnings.append(
                f"Account '{line.account_code}' has both debit and credit amounts"
            )

    imbalance = total_debit - total_credit
    equity_adjustment = abs(imbalance)

    # Fase C1.5: resolve subledger control accounts via role (Law 27).
    # Original code matched control account by hardcoded `account_code`,
    # which carried two latent bugs (Fase A finding):
    #   - AR filter used 1-10300 (= Kas Kecil), never matched real AR.
    #   - Inventory filter used 1-10400 (= Piutang Usaha), never
    #     matched real inventory CoA.
    # Roles are tenant-configurable; we resolve to the tenant's actual
    # AR_TRADE / AP_TRADE / INVENTORY_MERCHANDISE account_code, then
    # match request lines by that code. Missing role mapping -> 422
    # at role resolution (no silent skip).
    ar_role_account = await _resolve_role_account(conn, tenant_id, AccountRole.AR_TRADE)
    ap_role_account = await _resolve_role_account(conn, tenant_id, AccountRole.AP_TRADE)
    inventory_role_account = await _resolve_role_account(
        conn, tenant_id, AccountRole.INVENTORY_MERCHANDISE
    )
    ar_control_code = ar_role_account["code"]
    ap_control_code = ap_role_account["code"]
    inventory_control_code = inventory_role_account["code"]

    # Check AR subledger totals if provided
    ar_control_match = None
    if request.ar_balances:
        ar_total = sum(ar.amount for ar in request.ar_balances)
        ar_control = next(
            (a for a in request.accounts if a.account_code == ar_control_code),
            None,
        )
        if ar_control:
            ar_control_amount = ar_control.debit - ar_control.credit
            ar_control_match = ar_total == ar_control_amount
            if not ar_control_match:
                warnings.append(
                    f"AR subledger total ({ar_total}) doesn't match AR control account ({ar_control_amount})"
                )
        else:
            warnings.append(
                f"AR balances provided but AR control account ({ar_control_code}) not in accounts list"
            )

    # Check AP subledger totals if provided
    ap_control_match = None
    if request.ap_balances:
        ap_total = sum(ap.amount for ap in request.ap_balances)
        ap_control = next(
            (a for a in request.accounts if a.account_code == ap_control_code),
            None,
        )
        if ap_control:
            ap_control_amount = ap_control.credit - ap_control.debit
            ap_control_match = ap_total == ap_control_amount
            if not ap_control_match:
                warnings.append(
                    f"AP subledger total ({ap_total}) doesn't match AP control account ({ap_control_amount})"
                )
        else:
            warnings.append(
                f"AP balances provided but AP control account ({ap_control_code}) not in accounts list"
            )

    # Check inventory totals if provided
    inventory_control_match = None
    if request.inventory_balances:
        inventory_total = sum(
            inv.total_value or (inv.quantity * inv.unit_cost)
            for inv in request.inventory_balances
        )
        inv_control = next(
            (a for a in request.accounts if a.account_code == inventory_control_code),
            None,
        )
        if inv_control:
            inv_control_amount = inv_control.debit - inv_control.credit
            inventory_control_match = inventory_total == inv_control_amount
            if not inventory_control_match:
                warnings.append(
                    f"Inventory total ({inventory_total}) doesn't match Inventory control ({inv_control_amount})"
                )

    return ValidationResult(
        is_valid=len(errors) == 0,
        total_debit=total_debit,
        total_credit=total_credit,
        imbalance=imbalance,
        equity_adjustment_needed=equity_adjustment,
        ar_control_match=ar_control_match,
        ap_control_match=ap_control_match,
        inventory_control_match=inventory_control_match,
        errors=errors,
        warnings=warnings,
    )


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("/summary", response_model=OpeningBalanceSummaryResponse)
async def get_opening_balance_summary(request: Request):
    """
    Get summary of current opening balance state.

    Returns whether opening balance exists, totals, and last update date.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")

        # Get active opening balance record
        query = """
            SELECT
                id, opening_date, balance_snapshot,
                gl_journal_id, ar_journal_id, ap_journal_id, inventory_journal_id,
                created_at, updated_at
            FROM opening_balance_records
            WHERE tenant_id = $1 AND status = 'ACTIVE'
            LIMIT 1
        """
        record = await conn.fetchrow(query, tenant_id)

        if not record:
            return OpeningBalanceSummaryResponse(
                data=OpeningBalanceSummary(
                    has_opening_balance=False,
                    opening_date=None,
                    total_debit=0,
                    total_credit=0,
                    equity_adjustment=0,
                    ar_total=0,
                    ap_total=0,
                    inventory_total=0,
                    last_updated=None,
                )
            )

        # Parse balance snapshot
        snapshot = record["balance_snapshot"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)

        totals = snapshot.get("totals", {})

        return OpeningBalanceSummaryResponse(
            data=OpeningBalanceSummary(
                has_opening_balance=True,
                opening_date=record["opening_date"],
                total_debit=totals.get("debit", 0),
                total_credit=totals.get("credit", 0),
                equity_adjustment=totals.get("equity_adjustment", 0),
                ar_total=totals.get("ar_total", 0),
                ap_total=totals.get("ap_total", 0),
                inventory_total=totals.get("inventory_total", 0),
                last_updated=record["updated_at"].isoformat()
                if record["updated_at"]
                else None,
            )
        )


@router.get("", response_model=OpeningBalanceListResponse)
async def list_opening_balance_history(
    request: Request,
    include_superseded: bool = Query(False, description="Include superseded records"),
):
    """
    List opening balance records.

    By default only returns ACTIVE record. Set include_superseded=true for full history.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")

        status_filter = "" if include_superseded else "AND status = 'ACTIVE'"

        query = f"""
            SELECT
                id, tenant_id, opening_date, description, status,
                gl_journal_id, ar_journal_id, ap_journal_id, inventory_journal_id,
                balance_snapshot,
                created_at, created_by, superseded_at, superseded_by
            FROM opening_balance_records
            WHERE tenant_id = $1 {status_filter}
            ORDER BY created_at DESC
        """
        records = await conn.fetch(query, tenant_id)

        data = []
        for rec in records:
            snapshot = rec["balance_snapshot"]
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot)

            accounts = snapshot.get("accounts", [])
            totals = snapshot.get("totals", {})

            data.append(
                OpeningBalanceData(
                    id=str(rec["id"]),
                    tenant_id=rec["tenant_id"],
                    opening_date=rec["opening_date"],
                    description=rec["description"],
                    status=rec["status"],
                    gl_journal_id=str(rec["gl_journal_id"])
                    if rec["gl_journal_id"]
                    else None,
                    ar_journal_id=str(rec["ar_journal_id"])
                    if rec["ar_journal_id"]
                    else None,
                    ap_journal_id=str(rec["ap_journal_id"])
                    if rec["ap_journal_id"]
                    else None,
                    inventory_journal_id=str(rec["inventory_journal_id"])
                    if rec["inventory_journal_id"]
                    else None,
                    accounts=[
                        AccountBalanceItem(
                            account_code=a["code"],
                            account_name=a["name"],
                            account_type=a["type"],
                            debit=a["debit"],
                            credit=a["credit"],
                        )
                        for a in accounts
                    ],
                    total_debit=totals.get("debit", 0),
                    total_credit=totals.get("credit", 0),
                    equity_adjustment=totals.get("equity_adjustment", 0),
                    ar_count=totals.get("ar_count"),
                    ar_total=totals.get("ar_total"),
                    ap_count=totals.get("ap_count"),
                    ap_total=totals.get("ap_total"),
                    inventory_count=totals.get("inventory_count"),
                    inventory_total=totals.get("inventory_total"),
                    created_at=rec["created_at"].isoformat()
                    if rec["created_at"]
                    else None,
                    created_by=str(rec["created_by"]),
                    superseded_at=rec["superseded_at"].isoformat()
                    if rec["superseded_at"]
                    else None,
                    superseded_by=str(rec["superseded_by"])
                    if rec["superseded_by"]
                    else None,
                )
            )

        return OpeningBalanceListResponse(data=data, total=len(data))


@router.get("/{record_id}", response_model=OpeningBalanceResponse)
async def get_opening_balance_detail(request: Request, record_id: UUID):
    """Get opening balance record detail by ID."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")

        query = """
            SELECT
                id, tenant_id, opening_date, description, status,
                gl_journal_id, ar_journal_id, ap_journal_id, inventory_journal_id,
                balance_snapshot,
                created_at, created_by, superseded_at, superseded_by
            FROM opening_balance_records
            WHERE tenant_id = $1 AND id = $2
        """
        rec = await conn.fetchrow(query, tenant_id, record_id)

        if not rec:
            raise HTTPException(
                status_code=404, detail="Opening balance record not found"
            )

        snapshot = rec["balance_snapshot"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)

        accounts = snapshot.get("accounts", [])
        totals = snapshot.get("totals", {})

        return OpeningBalanceResponse(
            data=OpeningBalanceData(
                id=str(rec["id"]),
                tenant_id=rec["tenant_id"],
                opening_date=rec["opening_date"],
                description=rec["description"],
                status=rec["status"],
                gl_journal_id=str(rec["gl_journal_id"])
                if rec["gl_journal_id"]
                else None,
                ar_journal_id=str(rec["ar_journal_id"])
                if rec["ar_journal_id"]
                else None,
                ap_journal_id=str(rec["ap_journal_id"])
                if rec["ap_journal_id"]
                else None,
                inventory_journal_id=str(rec["inventory_journal_id"])
                if rec["inventory_journal_id"]
                else None,
                accounts=[
                    AccountBalanceItem(
                        account_code=a["code"],
                        account_name=a["name"],
                        account_type=a["type"],
                        debit=a["debit"],
                        credit=a["credit"],
                    )
                    for a in accounts
                ],
                total_debit=totals.get("debit", 0),
                total_credit=totals.get("credit", 0),
                equity_adjustment=totals.get("equity_adjustment", 0),
                ar_count=totals.get("ar_count"),
                ar_total=totals.get("ar_total"),
                ap_count=totals.get("ap_count"),
                ap_total=totals.get("ap_total"),
                inventory_count=totals.get("inventory_count"),
                inventory_total=totals.get("inventory_total"),
                created_at=rec["created_at"].isoformat() if rec["created_at"] else None,
                created_by=str(rec["created_by"]),
                superseded_at=rec["superseded_at"].isoformat()
                if rec["superseded_at"]
                else None,
                superseded_by=str(rec["superseded_by"])
                if rec["superseded_by"]
                else None,
            )
        )


@router.post("/validate", response_model=ValidateOpeningBalanceResponse)
async def validate_opening_balance(request: Request, body: CreateOpeningBalanceRequest):
    """
    Validate opening balance request without creating.

    Use this to preview imbalances and potential issues before committing.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    await _ensure_role_preconditions(pool)

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")

        result = await validate_opening_balance_request(conn, tenant_id, body)

        return ValidateOpeningBalanceResponse(data=result)


@router.post("", response_model=CreateOpeningBalanceResponse)
async def create_opening_balance(request: Request, body: CreateOpeningBalanceRequest):
    """
    Create opening balance entries.

    This will:
    1. Validate all account codes exist
    2. Calculate any imbalance
    3. Post balancing entry to Opening Balance Equity (EQUITY_OPENING_BALANCE role)
    4. Create journal with is_opening_balance = true
    5. Create AR/AP subledger entries if provided
    6. Store snapshot for audit

    Note: If an ACTIVE opening balance exists, use PUT to supersede it.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    user_id = ctx["user_id"]
    pool = await get_pool()
    await _ensure_role_preconditions(pool)

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")

        # Check if active opening balance already exists
        existing = await conn.fetchval(
            "SELECT id FROM opening_balance_records WHERE tenant_id = $1 AND status = 'ACTIVE'",
            tenant_id,
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Active opening balance already exists. Use PUT to supersede it.",
            )

        # Validate request
        validation = await validate_opening_balance_request(conn, tenant_id, body)
        if not validation.is_valid:
            raise HTTPException(
                status_code=400,
                detail={"message": "Validation failed", "errors": validation.errors},
            )

        async with conn.transaction():
            # Law 13: Advisory lock on opening balance creation
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", f"OPENING:{tenant_id}"
            )

            # Law 5: Check period is open for the opening balance date
            period = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
                ORDER BY start_date DESC LIMIT 1
                """,
                tenant_id,
                body.opening_date,
            )
            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post opening balance to {period['status'].lower()} period",
                )

            # Get Opening Balance Equity account
            ob_equity = await get_opening_balance_equity_account(conn, tenant_id)

            # Build journal lines
            journal_lines = []
            account_snapshot = []

            for line in body.accounts:
                account = await get_account_by_code(conn, tenant_id, line.account_code)
                if not account:
                    continue

                if line.debit > 0:
                    journal_lines.append(
                        {
                            "account_id": str(account["id"]),
                            "account_code": account["code"],
                            "account_name": account["name"],
                            "debit": line.debit,
                            "credit": 0,
                        }
                    )
                if line.credit > 0:
                    journal_lines.append(
                        {
                            "account_id": str(account["id"]),
                            "account_code": account["code"],
                            "account_name": account["name"],
                            "debit": 0,
                            "credit": line.credit,
                        }
                    )

                account_snapshot.append(
                    {
                        "code": account["code"],
                        "name": account["name"],
                        "type": account["type"],
                        "debit": line.debit,
                        "credit": line.credit,
                    }
                )

            # Add equity adjustment if needed
            equity_adjustment = 0
            if validation.imbalance != 0:
                equity_adjustment = abs(validation.imbalance)
                if validation.imbalance > 0:
                    # Debit > Credit, need credit to equity
                    journal_lines.append(
                        {
                            "account_id": str(ob_equity["id"]),
                            "account_code": ob_equity["code"],
                            "account_name": ob_equity["name"],
                            "debit": 0,
                            "credit": validation.imbalance,
                        }
                    )
                else:
                    # Credit > Debit, need debit to equity
                    journal_lines.append(
                        {
                            "account_id": str(ob_equity["id"]),
                            "account_code": ob_equity["code"],
                            "account_name": ob_equity["name"],
                            "debit": abs(validation.imbalance),
                            "credit": 0,
                        }
                    )

                account_snapshot.append(
                    {
                        "code": ob_equity["code"],
                        "name": ob_equity["name"],
                        "type": ob_equity["type"],
                        "debit": abs(validation.imbalance)
                        if validation.imbalance < 0
                        else 0,
                        "credit": validation.imbalance
                        if validation.imbalance > 0
                        else 0,
                    }
                )

            # Create journal entry
            total_debit = sum(ln["debit"] for ln in journal_lines)
            total_credit = sum(ln["credit"] for ln in journal_lines)

            journal_query = """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, journal_date, description,
                    total_debit, total_credit, status, source_type, source_id,
                    is_opening_balance, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 'DRAFT', 'OPENING_BALANCE', NULL,
                    true, $7
                ) RETURNING id
            """

            # Generate entry number
            journal_number = f"OB-{body.opening_date.strftime('%Y%m%d')}-001"
            existing_count = await conn.fetchval(
                "SELECT COUNT(*) FROM journal_entries WHERE tenant_id = $1 AND journal_number LIKE $2",
                tenant_id,
                f"OB-{body.opening_date.strftime('%Y%m%d')}%",
            )
            if existing_count > 0:
                journal_number = f"OB-{body.opening_date.strftime('%Y%m%d')}-{str(existing_count + 1).zfill(3)}"

            journal_id = await conn.fetchval(
                journal_query,
                tenant_id,
                journal_number,
                body.opening_date,
                body.description or "Saldo Awal / Opening Balance",
                total_debit,
                total_credit,
                user_id,
            )

            # Insert journal lines
            for idx, line in enumerate(journal_lines, 1):
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        journal_id, account_id, memo, debit, credit, line_number
                    ) VALUES ($1, $2::uuid, $3, $4, $5, $6)
                    """,
                    journal_id,
                    line["account_id"],
                    f"Opening Balance - {line['account_name']}",
                    line["debit"],
                    line["credit"],
                    idx,
                )

            # Law 20: DRAFT -> POSTED after lines inserted (triggers hash chain)
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
            )

            # Create AR subledger entries if provided
            ar_journal_id = None
            ar_total = 0
            ar_count = 0
            if body.ar_balances:
                for ar in body.ar_balances:
                    ar_count += 1
                    ar_total += ar.amount
                    await conn.execute(
                        """
                        INSERT INTO accounts_receivable (
                            tenant_id, customer_id, source_type, source_id,
                            invoice_number, invoice_date, due_date,
                            total_amount, amount_paid, balance, status,
                            description, created_by
                        ) VALUES (
                            $1, $2::uuid, 'OPENING_BALANCE', $3,
                            $4, $5, $6,
                            $7, 0, $7, 'OPEN',
                            $8, $9
                        )
                        """,
                        tenant_id,
                        ar.customer_id,
                        journal_id,
                        ar.invoice_number or f"OB-AR-{ar_count}",
                        ar.invoice_date or body.opening_date,
                        ar.due_date or body.opening_date,
                        ar.amount,
                        ar.description or "Opening Balance AR",
                        user_id,
                    )

            # Create AP subledger entries if provided
            ap_journal_id = None
            ap_total = 0
            ap_count = 0
            if body.ap_balances:
                for ap in body.ap_balances:
                    ap_count += 1
                    ap_total += ap.amount
                    await conn.execute(
                        """
                        INSERT INTO accounts_payable (
                            tenant_id, vendor_id, source_type, source_id,
                            bill_number, bill_date, due_date,
                            total_amount, amount_paid, balance, status,
                            description, created_by
                        ) VALUES (
                            $1, $2::uuid, 'OPENING_BALANCE', $3,
                            $4, $5, $6,
                            $7, 0, $7, 'OPEN',
                            $8, $9
                        )
                        """,
                        tenant_id,
                        ap.vendor_id,
                        journal_id,
                        ap.bill_number or f"OB-AP-{ap_count}",
                        ap.bill_date or body.opening_date,
                        ap.due_date or body.opening_date,
                        ap.amount,
                        ap.description or "Opening Balance AP",
                        user_id,
                    )

            # Handle inventory opening balances if provided
            inventory_journal_id = None
            inventory_total = 0
            inventory_count = 0
            if body.inventory_balances:
                for inv in body.inventory_balances:
                    inventory_count += 1
                    item_total = inv.total_value or Decimal(
                        str(inv.quantity)
                    ) * Decimal(str(inv.unit_cost))
                    inventory_total += item_total

                    # Update product/item inventory quantity
                    await conn.execute(
                        """
                        UPDATE products SET
                            stock_quantity = COALESCE(stock_quantity, 0) + $1,
                            purchase_price = COALESCE($2, purchase_price),
                            updated_at = NOW()
                        WHERE tenant_id = $3::uuid AND id = $4::uuid
                        """,
                        inv.quantity,
                        inv.unit_cost,
                        tenant_id,
                        inv.item_id,
                    )

            # Build balance snapshot
            balance_snapshot = {
                "accounts": account_snapshot,
                "totals": {
                    "debit": total_debit,
                    "credit": total_credit,
                    "equity_adjustment": equity_adjustment,
                    "ar_count": ar_count if ar_count > 0 else None,
                    "ar_total": ar_total if ar_count > 0 else None,
                    "ap_count": ap_count if ap_count > 0 else None,
                    "ap_total": ap_total if ap_count > 0 else None,
                    "inventory_count": inventory_count if inventory_count > 0 else None,
                    "inventory_total": inventory_total if inventory_count > 0 else None,
                },
                "ar_details": [
                    {
                        "customer_id": ar.customer_id,
                        "customer_name": ar.customer_name,
                        "amount": ar.amount,
                    }
                    for ar in (body.ar_balances or [])
                ],
                "ap_details": [
                    {
                        "vendor_id": ap.vendor_id,
                        "vendor_name": ap.vendor_name,
                        "amount": ap.amount,
                    }
                    for ap in (body.ap_balances or [])
                ],
                "inventory_details": [
                    {
                        "item_id": inv.item_id,
                        "item_code": inv.item_code,
                        "quantity": inv.quantity,
                        "unit_cost": inv.unit_cost,
                    }
                    for inv in (body.inventory_balances or [])
                ],
            }

            # Create opening balance record
            record_id = await conn.fetchval(
                """
                INSERT INTO opening_balance_records (
                    tenant_id, opening_date, description,
                    gl_journal_id, ar_journal_id, ap_journal_id, inventory_journal_id,
                    balance_snapshot, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'ACTIVE', $9)
                RETURNING id
                """,
                tenant_id,
                body.opening_date,
                body.description,
                journal_id,
                ar_journal_id,
                ap_journal_id,
                inventory_journal_id,
                json.dumps(balance_snapshot),
                user_id,
            )

            logger.info(
                "opening_balance_created",
                tenant_id=tenant_id,
                record_id=str(record_id),
                journal_id=str(journal_id),
                total_debit=total_debit,
                total_credit=total_credit,
                equity_adjustment=equity_adjustment,
            )

            return CreateOpeningBalanceResponse(
                message="Opening balance created successfully",
                data={
                    "id": str(record_id),
                    "gl_journal_id": str(journal_id),
                    "journal_number": journal_number,
                    "opening_date": body.opening_date.isoformat(),
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "equity_adjustment": equity_adjustment,
                    "ar_entries": ar_count,
                    "ap_entries": ap_count,
                    "inventory_entries": inventory_count,
                },
                warnings=validation.warnings if validation.warnings else None,
            )


@router.put("", response_model=CreateOpeningBalanceResponse)
async def update_opening_balance(request: Request, body: UpdateOpeningBalanceRequest):
    """
    Update/supersede opening balance.

    This will:
    1. Mark existing ACTIVE record as SUPERSEDED
    2. Create reversal journal for old opening balance
    3. Create new opening balance with updated values
    4. Maintain full audit trail

    Use this for corrections or fiscal year transitions.
    """
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    user_id = ctx["user_id"]
    pool = await get_pool()
    await _ensure_role_preconditions(pool)

    async with pool.acquire() as conn:
        await conn.execute(f"SET app.tenant_id = '{tenant_id}'")

        # Get existing active opening balance
        existing = await conn.fetchrow(
            """
            SELECT id, gl_journal_id, balance_snapshot
            FROM opening_balance_records
            WHERE tenant_id = $1 AND status = 'ACTIVE'
            """,
            tenant_id,
        )

        if not existing:
            raise HTTPException(
                status_code=404,
                detail="No active opening balance to update. Use POST to create one.",
            )

        # Validate new request
        create_request = CreateOpeningBalanceRequest(
            opening_date=body.opening_date,
            description=body.description,
            accounts=body.accounts,
            ar_balances=body.ar_balances,
            ap_balances=body.ap_balances,
            inventory_balances=body.inventory_balances,
        )

        validation = await validate_opening_balance_request(
            conn, tenant_id, create_request
        )
        if not validation.is_valid:
            raise HTTPException(
                status_code=400,
                detail={"message": "Validation failed", "errors": validation.errors},
            )

        async with conn.transaction():
            # Law 13: Advisory lock on opening balance update
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"OPENING_UPDATE:{tenant_id}",
            )

            # Law 5: Check period is open for the opening balance date
            period = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
                ORDER BY start_date DESC LIMIT 1
                """,
                tenant_id,
                body.opening_date,
            )
            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post opening balance to {period['status'].lower()} period",
                )

            # Mark existing as superseded
            await conn.execute(
                """
                UPDATE opening_balance_records
                SET status = 'SUPERSEDED', superseded_at = NOW(), superseded_by = $1
                WHERE id = $2
                """,
                user_id,
                existing["id"],
            )

            # Create reversal journal for old opening balance.
            # Fase C1.5: reversal lines MIRROR the original posting by
            # reusing the original `account_id` values from journal_lines.
            # We DO NOT re-resolve via role_resolver here: if a tenant
            # remaps EQUITY_OPENING_BALANCE (or any role) between the
            # original post and this supersede, re-resolving would land
            # the reversal on a different account than the original
            # posting, breaking Law 2 (mirror) and Law 4 (drift). The
            # role mapping is a forward-looking convention for NEW
            # postings, never a rewrite of history.
            old_journal_id = existing["gl_journal_id"]
            if old_journal_id:
                # Get old journal lines (authoritative source for reversal)
                old_lines = await conn.fetch(
                    """
                    SELECT account_id, debit, credit, memo
                    FROM journal_lines
                    WHERE journal_id = $1
                    """,
                    old_journal_id,
                )

                # Create reversal journal
                reversal_number = f"OB-REV-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                total_debit = sum(ln["credit"] for ln in old_lines)  # Swap debit/credit
                total_credit = sum(ln["debit"] for ln in old_lines)

                reversal_id = await conn.fetchval(
                    """
                    INSERT INTO journal_entries (
                        tenant_id, journal_number, journal_date, description,
                        total_debit, total_credit, status, source_type,
                        is_opening_balance, reversal_of_id, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, 'DRAFT', 'OPENING_BALANCE_REVERSAL',
                        true, $7, $8
                    ) RETURNING id
                    """,
                    tenant_id,
                    reversal_number,
                    body.opening_date,
                    f"Reversal: {body.reason}",
                    total_debit,
                    total_credit,
                    old_journal_id,
                    user_id,
                )

                for idx, line in enumerate(old_lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            journal_id, account_id, memo, debit, credit, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        reversal_id,
                        line["account_id"],
                        f"Reversal - {line['memo']}",
                        line["credit"],
                        line["debit"],
                        idx,  # Swap debit/credit
                    )

                # Law 20: DRAFT -> POSTED after lines (triggers hash chain)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    reversal_id,
                )

                # Law 26: Link original journal to reversal
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET reversed_by_id = $1
                    WHERE id = $2
                    """,
                    reversal_id,
                    old_journal_id,
                )

            # Now create new opening balance (reuse create logic)
            # For simplicity, we'll duplicate the core creation logic here

            ob_equity = await get_opening_balance_equity_account(conn, tenant_id)

            journal_lines = []
            account_snapshot = []

            for line in body.accounts:
                account = await get_account_by_code(conn, tenant_id, line.account_code)
                if not account:
                    continue

                if line.debit > 0:
                    journal_lines.append(
                        {
                            "account_id": str(account["id"]),
                            "account_code": account["code"],
                            "account_name": account["name"],
                            "debit": line.debit,
                            "credit": 0,
                        }
                    )
                if line.credit > 0:
                    journal_lines.append(
                        {
                            "account_id": str(account["id"]),
                            "account_code": account["code"],
                            "account_name": account["name"],
                            "debit": 0,
                            "credit": line.credit,
                        }
                    )

                account_snapshot.append(
                    {
                        "code": account["code"],
                        "name": account["name"],
                        "type": account["type"],
                        "debit": line.debit,
                        "credit": line.credit,
                    }
                )

            # Add equity adjustment
            equity_adjustment = 0
            if validation.imbalance != 0:
                equity_adjustment = abs(validation.imbalance)
                if validation.imbalance > 0:
                    journal_lines.append(
                        {
                            "account_id": str(ob_equity["id"]),
                            "account_code": ob_equity["code"],
                            "account_name": ob_equity["name"],
                            "debit": 0,
                            "credit": validation.imbalance,
                        }
                    )
                else:
                    journal_lines.append(
                        {
                            "account_id": str(ob_equity["id"]),
                            "account_code": ob_equity["code"],
                            "account_name": ob_equity["name"],
                            "debit": abs(validation.imbalance),
                            "credit": 0,
                        }
                    )

                account_snapshot.append(
                    {
                        "code": ob_equity["code"],
                        "name": ob_equity["name"],
                        "type": ob_equity["type"],
                        "debit": abs(validation.imbalance)
                        if validation.imbalance < 0
                        else 0,
                        "credit": validation.imbalance
                        if validation.imbalance > 0
                        else 0,
                    }
                )

            total_debit = sum(ln["debit"] for ln in journal_lines)
            total_credit = sum(ln["credit"] for ln in journal_lines)

            journal_number = f"OB-{body.opening_date.strftime('%Y%m%d')}-001"
            existing_count = await conn.fetchval(
                "SELECT COUNT(*) FROM journal_entries WHERE tenant_id = $1 AND journal_number LIKE $2",
                tenant_id,
                f"OB-{body.opening_date.strftime('%Y%m%d')}%",
            )
            if existing_count > 0:
                journal_number = f"OB-{body.opening_date.strftime('%Y%m%d')}-{str(existing_count + 1).zfill(3)}"

            journal_id = await conn.fetchval(
                """
                INSERT INTO journal_entries (
                    tenant_id, journal_number, journal_date, description,
                    total_debit, total_credit, status, source_type,
                    is_opening_balance, created_by
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 'DRAFT', 'OPENING_BALANCE',
                    true, $7
                ) RETURNING id
                """,
                tenant_id,
                journal_number,
                body.opening_date,
                body.description or f"Updated Opening Balance: {body.reason}",
                total_debit,
                total_credit,
                user_id,
            )

            for idx, line in enumerate(journal_lines, 1):
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        journal_id, account_id, memo, debit, credit, line_number
                    ) VALUES ($1, $2::uuid, $3, $4, $5, $6)
                    """,
                    journal_id,
                    line["account_id"],
                    f"Opening Balance - {line['account_name']}",
                    line["debit"],
                    line["credit"],
                    idx,
                )

            # Law 20: DRAFT -> POSTED after lines (triggers hash chain)
            await conn.execute(
                "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
            )

            # Handle AR/AP/Inventory (simplified - in production would need more careful handling)
            ar_count = len(body.ar_balances) if body.ar_balances else 0
            ar_total = (
                sum(ar.amount for ar in body.ar_balances) if body.ar_balances else 0
            )
            ap_count = len(body.ap_balances) if body.ap_balances else 0
            ap_total = (
                sum(ap.amount for ap in body.ap_balances) if body.ap_balances else 0
            )
            inventory_count = (
                len(body.inventory_balances) if body.inventory_balances else 0
            )
            inventory_total = (
                sum(
                    inv.total_value
                    or Decimal(str(inv.quantity)) * Decimal(str(inv.unit_cost))
                    for inv in body.inventory_balances
                )
                if body.inventory_balances
                else 0
            )

            balance_snapshot = {
                "accounts": account_snapshot,
                "totals": {
                    "debit": total_debit,
                    "credit": total_credit,
                    "equity_adjustment": equity_adjustment,
                    "ar_count": ar_count if ar_count > 0 else None,
                    "ar_total": ar_total if ar_count > 0 else None,
                    "ap_count": ap_count if ap_count > 0 else None,
                    "ap_total": ap_total if ap_count > 0 else None,
                    "inventory_count": inventory_count if inventory_count > 0 else None,
                    "inventory_total": inventory_total if inventory_count > 0 else None,
                },
                "update_reason": body.reason,
                "supersedes": str(existing["id"]),
            }

            record_id = await conn.fetchval(
                """
                INSERT INTO opening_balance_records (
                    tenant_id, opening_date, description,
                    gl_journal_id, balance_snapshot, status, created_by
                ) VALUES ($1, $2, $3, $4, $5, 'ACTIVE', $6)
                RETURNING id
                """,
                tenant_id,
                body.opening_date,
                body.description,
                journal_id,
                json.dumps(balance_snapshot),
                user_id,
            )

            logger.info(
                "opening_balance_updated",
                tenant_id=tenant_id,
                old_record_id=str(existing["id"]),
                new_record_id=str(record_id),
                reason=body.reason,
            )

            return CreateOpeningBalanceResponse(
                message="Opening balance updated successfully",
                data={
                    "id": str(record_id),
                    "superseded_id": str(existing["id"]),
                    "gl_journal_id": str(journal_id),
                    "journal_number": journal_number,
                    "opening_date": body.opening_date.isoformat(),
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "equity_adjustment": equity_adjustment,
                },
                warnings=validation.warnings if validation.warnings else None,
            )
