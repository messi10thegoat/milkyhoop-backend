"""
Accounting Periods Router - Period Management

Endpoints for managing accounting periods including close/reopen operations.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg
from datetime import datetime
import json

from ..schemas.periods import (
    UpdatePeriodRequest,
    ClosePeriodRequest,
    ReopenPeriodRequest,
    PeriodResponse,
    PeriodListItem,
    PeriodListResponse,
    ClosePeriodResponse,
    ClosePeriodWarning,
    ClosePeriodError,
    DraftJournalInfo,
    TrialBalanceSnapshotResponse,
)
from ..services.resolve_account import resolve_account

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool


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


# =============================================================================
# LIST PERIODS
# =============================================================================
@router.get("", response_model=PeriodListResponse)
async def list_periods(
    request: Request,
    fiscal_year_id: Optional[UUID] = Query(None, description="Filter by fiscal year"),
    status: Optional[str] = Query(
        None, description="Filter by status: OPEN, CLOSED, LOCKED"
    ),
    year: Optional[int] = Query(None, description="Filter by calendar year"),
):
    """List all accounting periods."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["fp.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if fiscal_year_id:
                conditions.append(f"fp.fiscal_year_id = ${param_idx}")
                params.append(fiscal_year_id)
                param_idx += 1

            if status:
                conditions.append(f"fp.status = ${param_idx}")
                params.append(status.upper())
                param_idx += 1

            if year:
                conditions.append(f"EXTRACT(YEAR FROM fp.start_date) = ${param_idx}")
                params.append(year)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT
                    fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date, fp.status,
                    fy.name as fiscal_year_name,
                    COUNT(je.id) as journal_count,
                    COUNT(je.id) FILTER (WHERE je.status = 'DRAFT') as draft_journal_count
                FROM fiscal_periods fp
                LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                LEFT JOIN journal_entries je ON je.period_id = fp.id
                WHERE {where_clause}
                GROUP BY fp.id, fy.name
                ORDER BY fp.start_date DESC
            """

            rows = await conn.fetch(query, *params)

            items = [
                PeriodListItem(
                    id=str(row["id"]),
                    period_name=row["period_name"],
                    period_number=row["period_number"],
                    fiscal_year_name=row["fiscal_year_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                    journal_count=row["journal_count"] or 0,
                    draft_journal_count=row["draft_journal_count"] or 0,
                )
                for row in rows
            ]

            return PeriodListResponse(data=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List periods error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list periods")


# =============================================================================
# GET CURRENT PERIOD
# =============================================================================
@router.get("/current", response_model=dict)
async def get_current_period(request: Request):
    """Get the current open accounting period."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow(
                """
                SELECT fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date,
                       fp.status, fp.fiscal_year_id, fy.name as fiscal_year_name
                FROM fiscal_periods fp
                LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                WHERE fp.tenant_id = $1
                  AND fp.status = 'OPEN'
                  AND CURRENT_DATE BETWEEN fp.start_date AND fp.end_date
                ORDER BY fp.start_date DESC
                LIMIT 1
            """,
                ctx["tenant_id"],
            )

            if not row:
                # Try to find any open period
                row = await conn.fetchrow(
                    """
                    SELECT fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date,
                           fp.status, fp.fiscal_year_id, fy.name as fiscal_year_name
                    FROM fiscal_periods fp
                    LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                    WHERE fp.tenant_id = $1 AND fp.status = 'OPEN'
                    ORDER BY fp.start_date DESC
                    LIMIT 1
                """,
                    ctx["tenant_id"],
                )

            if not row:
                return {
                    "success": True,
                    "data": None,
                    "message": "No open period found",
                }

            return {
                "success": True,
                "data": PeriodResponse(
                    id=str(row["id"]),
                    period_name=row["period_name"],
                    period_number=row["period_number"],
                    fiscal_year_id=str(row["fiscal_year_id"])
                    if row["fiscal_year_id"]
                    else None,
                    fiscal_year_name=row["fiscal_year_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                ),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get current period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get current period")


# =============================================================================
# GET PERIOD DETAIL
# =============================================================================
@router.get("/{period_id}", response_model=dict)
async def get_period(request: Request, period_id: UUID):
    """Get accounting period detail."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow(
                """
                SELECT fp.id, fp.period_name, fp.period_number, fp.start_date, fp.end_date,
                       fp.status, fp.closed_at, fp.closed_by, fp.lock_reason,
                       fp.fiscal_year_id, fy.name as fiscal_year_name
                FROM fiscal_periods fp
                LEFT JOIN fiscal_years fy ON fy.id = fp.fiscal_year_id
                WHERE fp.id = $1 AND fp.tenant_id = $2
            """,
                period_id,
                ctx["tenant_id"],
            )

            if not row:
                raise HTTPException(status_code=404, detail="Period not found")

            return {
                "success": True,
                "data": PeriodResponse(
                    id=str(row["id"]),
                    period_name=row["period_name"],
                    period_number=row["period_number"],
                    fiscal_year_id=str(row["fiscal_year_id"])
                    if row["fiscal_year_id"]
                    else None,
                    fiscal_year_name=row["fiscal_year_name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    status=row["status"],
                    closed_at=row["closed_at"],
                    closed_by=str(row["closed_by"]) if row["closed_by"] else None,
                    closing_notes=row["lock_reason"],
                ),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get period")


# =============================================================================
# UPDATE PERIOD
# =============================================================================
@router.put("/{period_id}", response_model=dict)
async def update_period(request: Request, period_id: UUID, body: UpdatePeriodRequest):
    """Update accounting period info."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check period exists
            existing = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_periods
                WHERE id = $1 AND tenant_id = $2
            """,
                period_id,
                ctx["tenant_id"],
            )

            if not existing:
                raise HTTPException(status_code=404, detail="Period not found")

            if existing["status"] == "LOCKED":
                raise HTTPException(
                    status_code=403, detail="Cannot modify locked period"
                )

            # Update fields
            updates = []
            params = []
            param_idx = 1

            if body.name is not None:
                updates.append(f"period_name = ${param_idx}")
                params.append(body.name)
                param_idx += 1

            if not updates:
                return await get_period(request, period_id)

            params.extend([period_id, ctx["tenant_id"]])
            update_clause = ", ".join(updates)

            await conn.execute(
                f"""
                UPDATE fiscal_periods
                SET {update_clause}
                WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            """,
                *params,
            )

            return await get_period(request, period_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update period")


# =============================================================================
# CLOSE PERIOD
# =============================================================================
@router.post("/{period_id}/close", response_model=ClosePeriodResponse)
async def close_period(request: Request, period_id: UUID, body: ClosePeriodRequest):
    """Close an accounting period."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Validate using helper function
            validation = await conn.fetchrow(
                """
                SELECT * FROM validate_period_close($1, $2)
            """,
                ctx["tenant_id"],
                period_id,
            )

            if not validation["can_close"]:
                error_code = validation["error_code"]
                if error_code == "PERIOD_NOT_FOUND":
                    raise HTTPException(
                        status_code=404, detail=validation["error_message"]
                    )
                elif error_code in ("PERIOD_ALREADY_CLOSED", "PERIOD_LOCKED"):
                    raise HTTPException(
                        status_code=409, detail=validation["error_message"]
                    )
                elif error_code == "PREVIOUS_PERIOD_OPEN":
                    return ClosePeriodResponse(
                        success=False,
                        errors=[
                            ClosePeriodError(
                                code=error_code, message=validation["error_message"]
                            )
                        ],
                    )
                elif error_code == "DRAFT_JOURNALS_EXIST":
                    # Get draft journal details
                    draft_rows = await conn.fetch(
                        """
                        SELECT id, journal_number, description, journal_date
                        FROM journal_entries
                        WHERE tenant_id = $1 AND period_id = $2 AND status = 'DRAFT'
                        LIMIT 10
                    """,
                        ctx["tenant_id"],
                        period_id,
                    )

                    draft_journals = [
                        DraftJournalInfo(
                            id=str(row["id"]),
                            journal_number=row["journal_number"],
                            description=row["description"],
                            entry_date=row["journal_date"],
                        )
                        for row in draft_rows
                    ]

                    return ClosePeriodResponse(
                        success=False,
                        errors=[
                            ClosePeriodError(
                                code=error_code, message=validation["error_message"]
                            )
                        ],
                        warnings=[
                            ClosePeriodWarning(
                                code="DRAFT_JOURNALS_EXIST",
                                message=f"{validation['draft_count']} draft journal(s) exist",
                                draft_journals=draft_journals,
                            )
                        ],
                    )

            # Handle warning case (drafts exist but not strict mode)
            warnings = []
            if validation["error_code"] == "WARNING_DRAFT_EXISTS":
                if not body.force:
                    draft_rows = await conn.fetch(
                        """
                        SELECT id, journal_number, description, journal_date
                        FROM journal_entries
                        WHERE tenant_id = $1 AND period_id = $2 AND status = 'DRAFT'
                        LIMIT 10
                    """,
                        ctx["tenant_id"],
                        period_id,
                    )

                    draft_journals = [
                        DraftJournalInfo(
                            id=str(row["id"]),
                            journal_number=row["journal_number"],
                            description=row["description"],
                            entry_date=row["journal_date"],
                        )
                        for row in draft_rows
                    ]

                    return ClosePeriodResponse(
                        success=False,
                        warnings=[
                            ClosePeriodWarning(
                                code="DRAFT_JOURNALS_EXIST",
                                message=validation["error_message"],
                                draft_journals=draft_journals,
                            )
                        ],
                    )
                else:
                    warnings.append(
                        ClosePeriodWarning(
                            code="FORCE_CLOSE",
                            message=f"Period closed with {validation['draft_count']} draft journal(s)",
                        )
                    )

            # Get period info for TB generation
            period = await conn.fetchrow(
                """
                SELECT start_date, end_date FROM fiscal_periods WHERE id = $1
            """,
                period_id,
            )

            # Generate trial balance snapshot
            tb_data = await conn.fetch(
                """
                SELECT
                    coa.id as account_id,
                    coa.account_code,
                    coa.name as account_name,
                    coa.account_type,
                    coa.normal_balance,
                    COALESCE(SUM(jl.debit), 0) as total_debit,
                    COALESCE(SUM(jl.credit), 0) as total_credit
                FROM chart_of_accounts coa
                LEFT JOIN journal_lines jl ON jl.account_id = coa.id
                LEFT JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.status = 'POSTED'
                    AND je.journal_date <= $2
                WHERE coa.tenant_id = $1 AND coa.is_active = TRUE
                GROUP BY coa.id
                HAVING COALESCE(SUM(jl.debit), 0) != 0 OR COALESCE(SUM(jl.credit), 0) != 0
                ORDER BY coa.account_code
            """,
                ctx["tenant_id"],
                period["end_date"],
            )

            lines_json = json.dumps([dict(row) for row in tb_data], default=str)
            total_debit = sum(row["total_debit"] for row in tb_data)
            total_credit = sum(row["total_credit"] for row in tb_data)
            is_balanced = total_debit == total_credit

            # Save TB snapshot
            snapshot_id = await conn.fetchval(
                """
                INSERT INTO trial_balance_snapshots
                    (tenant_id, period_id, as_of_date, snapshot_type, lines,
                     total_debit, total_credit, is_balanced, generated_by)
                VALUES ($1, $2, $3, 'closing', $4::jsonb, $5, $6, $7, $8)
                ON CONFLICT (tenant_id, period_id, snapshot_type)
                DO UPDATE SET
                    lines = EXCLUDED.lines,
                    total_debit = EXCLUDED.total_debit,
                    total_credit = EXCLUDED.total_credit,
                    is_balanced = EXCLUDED.is_balanced,
                    generated_at = NOW(),
                    generated_by = EXCLUDED.generated_by
                RETURNING id
            """,
                ctx["tenant_id"],
                period_id,
                period["end_date"],
                lines_json,
                total_debit,
                total_credit,
                is_balanced,
                ctx["user_id"],
            )

            # Law 13: Advisory lock for period close journal creation
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"PERIOD_CLOSE:{period_id}",
            )

            # ── Create closing journal entries ──────────────────────────
            # DR all REVENUE accounts (zero them out)
            # CR all EXPENSE accounts (zero them out)
            # Net difference → Retained Earnings (3-20000)
            closing_journal_id = None

            income_accounts = []
            expense_accounts = []

            for row in tb_data:
                balance = row["total_credit"] - row["total_debit"]  # net balance
                if balance == 0:
                    continue
                atype = row["account_type"]
                if atype in ("INCOME", "REVENUE"):
                    income_accounts.append(
                        (row["account_id"], row["account_code"], balance)
                    )
                elif atype == "EXPENSE":
                    expense_accounts.append(
                        (row["account_id"], row["account_code"], balance)
                    )

            if income_accounts or expense_accounts:
                # Get Retained Earnings account
                retained_earnings = await resolve_account(
                    conn, ctx["tenant_id"], "3-20000"
                )

                if retained_earnings:
                    total_income_dr = sum(abs(b) for _, _, b in income_accounts)
                    total_expense_cr = sum(abs(b) for _, _, b in expense_accounts)
                    # RE amount = DR side minus CR side so far; positive means CR RE
                    re_amount = total_income_dr - total_expense_cr

                    # Generate closing journal number
                    clo_seq = await conn.fetchval(
                        "SELECT COUNT(*) + 1 FROM journal_entries WHERE tenant_id = $1 AND source_type = 'CLOSING'",
                        ctx["tenant_id"],
                    )
                    clo_number = f"CLO-{period['end_date'].strftime('%Y%m')}-{str(clo_seq).zfill(3)}"

                    closing_total = max(total_income_dr, total_expense_cr)

                    closing_journal_id = await conn.fetchval(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, status,
                            total_debit, total_credit,
                            created_by, period_id
                        ) VALUES (gen_random_uuid(), $1, $2, $3, $4, 'CLOSING', 'DRAFT',  -- Law 20: DRAFT first
                                  $5, $5, $6, $7)
                        RETURNING id
                        """,
                        ctx["tenant_id"],
                        clo_number,
                        period["end_date"],
                        f"Closing entries for period ending {period['end_date']}",
                        closing_total,
                        ctx["user_id"],
                        period_id,
                    )

                    line_num = 1

                    # Close income accounts (DR to reduce credit balance)
                    for acct_id, code, balance in income_accounts:
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, account_id, line_number,
                                debit, credit, memo
                            ) VALUES (gen_random_uuid(), $1, $2, $3, $4, 0, $5)
                            """,
                            closing_journal_id,
                            acct_id,
                            line_num,
                            abs(balance),
                            f"Close {code} to Retained Earnings",
                        )
                        line_num += 1

                    # Close expense accounts (CR to reduce debit balance)
                    for acct_id, code, balance in expense_accounts:
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, account_id, line_number,
                                debit, credit, memo
                            ) VALUES (gen_random_uuid(), $1, $2, $3, 0, $4, $5)
                            """,
                            closing_journal_id,
                            acct_id,
                            line_num,
                            abs(balance),
                            f"Close {code} to Retained Earnings",
                        )
                        line_num += 1

                    # Retained Earnings entry (balancing entry)
                    # re_amount > 0 means more DR than CR so far → CR RE to balance
                    # re_amount < 0 means more CR than DR so far → DR RE to balance
                    if re_amount >= 0:
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, account_id, line_number,
                                debit, credit, memo
                            ) VALUES (gen_random_uuid(), $1, $2, $3, 0, $4, $5)
                            """,
                            closing_journal_id,
                            retained_earnings["id"],
                            line_num,
                            re_amount,
                            "Net income to Retained Earnings",
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, account_id, line_number,
                                debit, credit, memo
                            ) VALUES (gen_random_uuid(), $1, $2, $3, $4, 0, $5)
                            """,
                            closing_journal_id,
                            retained_earnings["id"],
                            line_num,
                            abs(re_amount),
                            "Net loss to Retained Earnings",
                        )

                    # Law 20: Finalize DRAFT -> POSTED after all lines inserted
                    await conn.execute(
                        """
                        UPDATE journal_entries SET status = 'POSTED'
                        WHERE id = $1
                        """,
                        closing_journal_id,
                    )

                    logger.info(
                        f"Created closing journal {clo_number} with {line_num} lines"
                    )

            # Close the period
            await conn.execute(
                """
                UPDATE fiscal_periods
                SET status = 'CLOSED', closed_at = NOW(), closed_by = $3,
                    lock_reason = $4, closing_journal_id = $5
                WHERE id = $1 AND tenant_id = $2
            """,
                period_id,
                ctx["tenant_id"],
                ctx["user_id"],
                body.closing_notes,
                closing_journal_id,
            )

            # Get updated period
            period_response = await get_period(request, period_id)

            return ClosePeriodResponse(
                success=True,
                data={
                    "period": period_response["data"],
                    "trial_balance_snapshot": TrialBalanceSnapshotResponse(
                        id=str(snapshot_id),
                        as_of_date=period["end_date"],
                        total_debit=total_debit,  # Law 25: numeric(18,2) — no float() conversion
                        total_credit=total_credit,  # Law 25: numeric(18,2) — no float() conversion
                        is_balanced=is_balanced,
                        generated_at=datetime.now(),
                    ),
                },
                warnings=warnings,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Close period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close period")


# =============================================================================
# REOPEN PERIOD
# =============================================================================
@router.post("/{period_id}/reopen", response_model=dict)
async def reopen_period(request: Request, period_id: UUID, body: ReopenPeriodRequest):
    """Reopen a closed accounting period."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check tenant settings
            settings_row = await conn.fetchrow(
                """
                SELECT allow_period_reopen FROM accounting_settings
                WHERE tenant_id = $1
            """,
                ctx["tenant_id"],
            )

            if settings_row and not settings_row["allow_period_reopen"]:
                raise HTTPException(
                    status_code=403,
                    detail="Period reopening is disabled for this tenant",
                )

            # Check period
            period = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_periods
                WHERE id = $1 AND tenant_id = $2
            """,
                period_id,
                ctx["tenant_id"],
            )

            if not period:
                raise HTTPException(status_code=404, detail="Period not found")

            if period["status"] == "LOCKED":
                raise HTTPException(
                    status_code=403, detail="Cannot reopen locked period"
                )

            if period["status"] == "OPEN":
                raise HTTPException(status_code=400, detail="Period is already open")

            # Reopen period
            await conn.execute(
                """
                UPDATE fiscal_periods
                SET status = 'OPEN', closed_at = NULL, closed_by = NULL
                WHERE id = $1 AND tenant_id = $2
            """,
                period_id,
                ctx["tenant_id"],
            )

            # Log audit trail
            await conn.execute(
                """
                INSERT INTO audit_logs (id, "userId", "eventType", metadata, success, "createdAt")
                VALUES (gen_random_uuid()::text, $1, 'PERIOD_REOPEN', $2, true, NOW())
            """,
                str(ctx["user_id"]) if ctx["user_id"] else None,
                json.dumps({"period_id": str(period_id), "reason": body.reason}),
            )
            return await get_period(request, period_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reopen period error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reopen period")
