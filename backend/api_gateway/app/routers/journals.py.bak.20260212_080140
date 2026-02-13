"""
Journals Router - Manual Journal Entry Management

CRUD endpoints for manual journal entries with double-entry validation.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal

from ..schemas.journals import (
    CreateJournalRequest,
    ReverseJournalRequest,
    JournalResponse,
    JournalLineResponse,
    JournalListItem,
    JournalListResponse,
    JournalSummary,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create connection pool."""
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


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


async def get_next_journal_number(conn, tenant_id: str, prefix: str = "JV") -> str:
    """Get next journal number using sequence (uses year + month columns)."""
    today = date.today()
    year = today.year
    month = today.month
    year_month_str = today.strftime("%y%m")

    # Get or create sequence for this prefix/year/month
    seq = await conn.fetchval(
        """
        INSERT INTO journal_number_sequences (tenant_id, prefix, year, month, last_number)
        VALUES ($1, $2, $3, $4, 1)
        ON CONFLICT (tenant_id, prefix, year, month)
        DO UPDATE SET last_number = journal_number_sequences.last_number + 1, updated_at = NOW()
        RETURNING last_number
    """,
        tenant_id,
        prefix,
        year,
        month,
    )

    return f"{prefix}-{year_month_str}-{seq:04d}"


# =============================================================================
# LIST JOURNALS
# =============================================================================
@router.get("", response_model=JournalListResponse)
async def list_journals(
    request: Request,
    period_id: Optional[UUID] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    status: Optional[str] = Query(None, description="draft, posted, reversed"),
    source_type: Optional[str] = Query(None),
    account_id: Optional[UUID] = Query(None, description="Filter by account in lines"),
    search: Optional[str] = Query(None, description="Search description"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List journal entries with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["je.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if period_id:
                conditions.append(f"je.period_id = ${param_idx}")
                params.append(period_id)
                param_idx += 1

            if start_date:
                conditions.append(f"je.journal_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"je.journal_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            if status:
                conditions.append(f"je.status = ${param_idx}")
                params.append(status.upper())
                param_idx += 1

            if source_type:
                conditions.append(f"je.source_type = ${param_idx}")
                params.append(source_type.upper())
                param_idx += 1

            if account_id:
                conditions.append(
                    f"""
                    EXISTS (SELECT 1 FROM journal_lines jl
                            WHERE jl.journal_id = je.id AND jl.account_id = ${param_idx})
                """
                )
                params.append(account_id)
                param_idx += 1

            if search:
                conditions.append(f"je.description ILIKE ${param_idx}")
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Count totals
            count_query = f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'DRAFT') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'POSTED') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'VOID' OR reversal_of_id IS NOT NULL) as reversed_count
                FROM journal_entries je
                WHERE {where_clause}
            """
            counts = await conn.fetchrow(count_query, *params)

            # Get data with pagination
            offset = (page - 1) * limit
            params.extend([limit, offset])

            query = f"""
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.source_type, je.total_debit, je.total_credit, je.status, je.created_at
                FROM journal_entries je
                WHERE {where_clause}
                ORDER BY je.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """

            rows = await conn.fetch(query, *params)

            items = [
                JournalListItem(
                    id=str(row["id"]),
                    journal_number=row["journal_number"],
                    entry_date=row["journal_date"],
                    description=row["description"],
                    source_type=row["source_type"].lower()
                    if row["source_type"]
                    else "manual",
                    total_debit=row["total_debit"] or Decimal("0"),
                    total_credit=row["total_credit"] or Decimal("0"),
                    status=row["status"].lower() if row["status"] else "draft",
                    created_at=row["created_at"],
                )
                for row in rows
            ]

            return JournalListResponse(
                data=items,
                summary=JournalSummary(
                    total_count=counts["total"],
                    draft_count=counts["draft_count"],
                    posted_count=counts["posted_count"],
                    reversed_count=counts["reversed_count"],
                ),
                pagination={
                    "page": page,
                    "limit": limit,
                    "total": counts["total"],
                    "has_more": offset + len(items) < counts["total"],
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List journals error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list journals")


# =============================================================================
# GET JOURNAL DETAIL
# =============================================================================
@router.get("/{journal_id}", response_model=dict)
async def get_journal(request: Request, journal_id: UUID):
    """Get journal entry detail with lines."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get journal header
            je_row = await conn.fetchrow(
                """
                SELECT je.*, fp.period_name
                FROM journal_entries je
                LEFT JOIN fiscal_periods fp ON fp.id = je.period_id
                WHERE je.id = $1 AND je.tenant_id = $2
            """,
                journal_id,
                ctx["tenant_id"],
            )

            if not je_row:
                raise HTTPException(status_code=404, detail="Journal not found")

            # Get lines with account info
            lines = await conn.fetch(
                """
                SELECT jl.*, coa.account_code, coa.name as account_name
                FROM journal_lines jl
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE jl.journal_id = $1
                ORDER BY jl.line_number
            """,
                journal_id,
            )

            line_responses = [
                JournalLineResponse(
                    id=str(line["id"]),
                    line_number=line["line_number"],
                    account_id=str(line["account_id"]),
                    account_code=line["account_code"],
                    account_name=line["account_name"],
                    description=line["memo"] or line["description"],
                    debit=line["debit"] or Decimal("0"),
                    credit=line["credit"] or Decimal("0"),
                )
                for line in lines
            ]

            return {
                "success": True,
                "data": JournalResponse(
                    id=str(je_row["id"]),
                    journal_number=je_row["journal_number"],
                    entry_date=je_row["journal_date"],
                    period_id=str(je_row["period_id"]) if je_row["period_id"] else None,
                    period_name=je_row["period_name"],
                    source_type=je_row["source_type"].lower()
                    if je_row["source_type"]
                    else "manual",
                    source_id=str(je_row["source_id"]) if je_row["source_id"] else None,
                    description=je_row["description"],
                    lines=line_responses,
                    total_debit=je_row["total_debit"] or Decimal("0"),
                    total_credit=je_row["total_credit"] or Decimal("0"),
                    is_balanced=(je_row["total_debit"] or 0)
                    == (je_row["total_credit"] or 0),
                    status=je_row["status"].lower() if je_row["status"] else "draft",
                    reversal_of_id=str(je_row["reversal_of_id"])
                    if je_row["reversal_of_id"]
                    else None,
                    reversed_by_id=str(je_row["reversed_by_id"])
                    if je_row["reversed_by_id"]
                    else None,
                    created_by=str(je_row["created_by"])
                    if je_row["created_by"]
                    else None,
                    created_at=je_row["created_at"],
                    posted_at=je_row["updated_at"] if je_row["status"] == "POSTED" else None,
                    posted_by=None,
                ),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal")


# =============================================================================
# CREATE JOURNAL
# =============================================================================
@router.post("", response_model=dict, status_code=201)
async def create_journal(request: Request, body: CreateJournalRequest):
    """Create a manual journal entry."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check period is open
            period = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
                ORDER BY start_date DESC LIMIT 1
            """,
                ctx["tenant_id"],
                body.entry_date,
            )

            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post to {period['status'].lower()} period",
                )

            # Check approval requirement
            settings_row = await conn.fetchrow(
                """
                SELECT journal_approval_required FROM accounting_settings
                WHERE tenant_id = $1
            """,
                ctx["tenant_id"],
            )

            needs_approval = settings_row and settings_row["journal_approval_required"]
            initial_status = (
                "DRAFT" if body.save_as_draft or needs_approval else "POSTED"
            )

            # Calculate totals
            total_debit = sum(line.debit for line in body.lines)
            total_credit = sum(line.credit for line in body.lines)

            # Get journal number
            journal_number = await get_next_journal_number(conn, ctx["tenant_id"], "JV")

            async with conn.transaction():
                # Create journal header
                journal_id = await conn.fetchval(
                    """
                    INSERT INTO journal_entries (
                        tenant_id, journal_number, journal_date, description,
                        source_type, total_debit, total_credit, status,
                        period_id, created_by
                    )
                    VALUES ($1, $2, $3, $4, 'MANUAL', $5, $6, $7, $8, $9)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    journal_number,
                    body.entry_date,
                    body.description,
                    total_debit,
                    total_credit,
                    initial_status,
                    period["id"] if period else None,
                    ctx["user_id"],
                )

                # Create journal lines
                for i, line in enumerate(body.lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            journal_id, line_number, account_id, memo, debit, credit
                        )
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                        journal_id,
                        i,
                        UUID(line.account_id),
                        line.description,
                        line.debit,
                        line.credit,
                    )

            return await get_journal(request, journal_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create journal")


# =============================================================================
# POST DRAFT JOURNAL
# =============================================================================
@router.post("/{journal_id}/post", response_model=dict)
async def post_journal(request: Request, journal_id: UUID):
    """Post a draft journal entry."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check journal exists and is draft
            journal = await conn.fetchrow(
                """
                SELECT id, status, journal_date FROM journal_entries
                WHERE id = $1 AND tenant_id = $2
            """,
                journal_id,
                ctx["tenant_id"],
            )

            if not journal:
                raise HTTPException(status_code=404, detail="Journal not found")

            if journal["status"] != "DRAFT":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot post journal with status: {journal['status']}",
                )

            # Check period is open
            period = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
            """,
                ctx["tenant_id"],
                journal["journal_date"],
            )

            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post to {period['status'].lower()} period",
                )

            # Post journal
            await conn.execute(
                """
                UPDATE journal_entries
                SET status = 'POSTED', updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """,
                journal_id,
                ctx["tenant_id"],
            )

            return await get_journal(request, journal_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Post journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post journal")


# =============================================================================
# REVERSE JOURNAL
# =============================================================================
@router.post("/{journal_id}/reverse", response_model=dict)
async def reverse_journal(
    request: Request, journal_id: UUID, body: ReverseJournalRequest
):
    """Create a reversal entry for a posted journal."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check journal exists and is posted
            journal = await conn.fetchrow(
                """
                SELECT id, status, reversed_by_id, journal_number, description
                FROM journal_entries
                WHERE id = $1 AND tenant_id = $2
            """,
                journal_id,
                ctx["tenant_id"],
            )

            if not journal:
                raise HTTPException(status_code=404, detail="Journal not found")

            if journal["status"] == "DRAFT":
                raise HTTPException(
                    status_code=400, detail="Cannot reverse draft journal"
                )

            if journal["reversed_by_id"]:
                raise HTTPException(
                    status_code=409, detail="Journal is already reversed"
                )

            # Get original lines
            lines = await conn.fetch(
                """
                SELECT account_id, memo, debit, credit FROM journal_lines
                WHERE journal_id = $1 ORDER BY line_number
            """,
                journal_id,
            )

            # Check reversal period is open
            period = await conn.fetchrow(
                """
                SELECT id, status FROM fiscal_periods
                WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
            """,
                ctx["tenant_id"],
                body.reversal_date,
            )

            if period and period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot post reversal to {period['status'].lower()} period",
                )

            # Create reversal
            reversal_number = await get_next_journal_number(
                conn, ctx["tenant_id"], "JV"
            )
            total_debit = sum(line["credit"] for line in lines)  # Swap debit/credit
            total_credit = sum(line["debit"] for line in lines)

            async with conn.transaction():
                # Create reversal journal
                reversal_id = await conn.fetchval(
                    """
                    INSERT INTO journal_entries (
                        tenant_id, journal_number, journal_date, description,
                        source_type, total_debit, total_credit, status,
                        period_id, reversal_of_id, reversal_reason, created_by
                    )
                    VALUES ($1, $2, $3, $4, 'MANUAL', $5, $6, 'POSTED', $7, $8, $9, $10)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    reversal_number,
                    body.reversal_date,
                    f"Reversal of {journal['journal_number']}: {body.reason}",
                    total_debit,
                    total_credit,
                    period["id"] if period else None,
                    journal_id,
                    body.reason,
                    ctx["user_id"],
                )

                # Create reversed lines (swap debit/credit)
                for i, line in enumerate(lines, 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (journal_id, line_number, account_id, memo, debit, credit)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                        reversal_id,
                        i,
                        line["account_id"],
                        line["memo"],
                        line["credit"],
                        line["debit"],
                    )  # Swapped

                # Link original to reversal
                await conn.execute(
                    """
                    UPDATE journal_entries
                    SET reversed_by_id = $3, reversed_at = NOW()
                    WHERE id = $1 AND tenant_id = $2
                """,
                    journal_id,
                    ctx["tenant_id"],
                    reversal_id,
                )

            return await get_journal(request, reversal_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reverse journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reverse journal")


# =============================================================================
# DELETE DRAFT JOURNAL
# =============================================================================
@router.delete("/{journal_id}", response_model=dict)
async def delete_journal(request: Request, journal_id: UUID):
    """Delete a draft journal entry."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Check journal exists and is draft
            journal = await conn.fetchrow(
                """
                SELECT id, status FROM journal_entries
                WHERE id = $1 AND tenant_id = $2
            """,
                journal_id,
                ctx["tenant_id"],
            )

            if not journal:
                raise HTTPException(status_code=404, detail="Journal not found")

            if journal["status"] != "DRAFT":
                raise HTTPException(
                    status_code=409, detail="Only draft journals can be deleted"
                )

            # Delete lines first, then header
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM journal_lines WHERE journal_id = $1", journal_id
                )
                await conn.execute(
                    """
                    DELETE FROM journal_entries WHERE id = $1 AND tenant_id = $2
                """,
                    journal_id,
                    ctx["tenant_id"],
                )

            return {"success": True, "message": "Journal deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete journal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete journal")


# =============================================================================
# GET JOURNALS BY ACCOUNT
# =============================================================================
@router.get("/by-account/{account_id}", response_model=JournalListResponse)
async def get_journals_by_account(
    request: Request,
    account_id: UUID,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get all journals containing a specific account."""
    # Reuse list_journals with account_id filter
    return await list_journals(
        request=request,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=limit,
    )


# =============================================================================
# GET JOURNAL BY SOURCE
# =============================================================================
@router.get("/by-source/{source_type}/{source_id}", response_model=dict)
async def get_journal_by_source(
    request: Request,
    source_type: str,
    source_id: UUID,
):
    """Get journal entry for a specific source document."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            journal = await conn.fetchrow(
                """
                SELECT id FROM journal_entries
                WHERE tenant_id = $1 AND source_type = $2 AND source_id = $3
                ORDER BY created_at DESC LIMIT 1
            """,
                ctx["tenant_id"],
                source_type.upper(),
                source_id,
            )

            if not journal:
                return {"success": True, "data": None}

            return await get_journal(request, journal["id"])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get journal by source error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal")
