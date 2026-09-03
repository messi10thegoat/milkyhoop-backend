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


async def get_next_journal_number(
    conn, tenant_id: str, prefix: str = "JV", p_date=None
) -> str:
    """Get next journal number via the canonical self-healing DB function.

    Delegates to get_next_journal_number(tenant, prefix, p_date) (V176), which
    bumps the prefix's own counter AND self-heals against the actual emitted max
    (no drift, concurrency-safe). p_date defaults to today; callers should pass
    the journal_date so the YYMM segment tracks the document date.
    """
    if p_date is None:
        p_date = date.today()
    return await conn.fetchval(
        "SELECT get_next_journal_number($1, $2, $3)",
        tenant_id,
        prefix,
        p_date,
    )


# =============================================================================
# GUARD: Block manual journal from touching derived-layer accounts (Law 31 Gate 4)
# =============================================================================
async def validate_no_derived_layer_accounts(conn, tenant_id: str, lines: list):
    """
    Block manual journal from touching accounts that have derived layers.
    These accounts MUST be accessed via their proper modules to maintain
    dual-layer sync (Law 31 Gate 4).

    - RECEIVABLE/PAYABLE -> use Payment/Settlement module
    - Persediaan/HPP -> use Stock Adjustment module
    - Bank CoA -> ALLOWED (not blocked here)
    """
    account_ids = [UUID(line.account_id) for line in lines]

    # Check RECEIVABLE and PAYABLE accounts
    ar_ap_accounts = await conn.fetch(
        """
        SELECT id, account_code, name, account_type
        FROM chart_of_accounts
        WHERE id = ANY($1) AND account_type IN ('RECEIVABLE', 'PAYABLE')
          AND tenant_id = $2
    """,
        account_ids,
        tenant_id,
    )

    if ar_ap_accounts:
        names = ", ".join(f"{a['account_code']} {a['name']}" for a in ar_ap_accounts)
        acct_type = ar_ap_accounts[0]["account_type"]
        raise HTTPException(
            status_code=400,
            detail=(
                f"Manual journal tidak boleh menyentuh akun {acct_type}. "
                f"Akun: {names}. "
                f"Gunakan modul Payment/Settlement untuk transaksi piutang/hutang."
            ),
        )

    # Check inventory/COGS accounts (default + product-level overrides)
    inventory_cogs_rows = await conn.fetch(
        """
        SELECT DISTINCT coa_id FROM (
            SELECT id AS coa_id FROM chart_of_accounts
            WHERE account_code IN ('1-10600', '5-10100') AND tenant_id = $1
            UNION
            SELECT inventory_account_id AS coa_id FROM products
            WHERE tenant_id = $1 AND inventory_account_id IS NOT NULL
            UNION
            SELECT cogs_account_id AS coa_id FROM products
            WHERE tenant_id = $1 AND cogs_account_id IS NOT NULL
        ) sub WHERE coa_id IS NOT NULL
    """,
        tenant_id,
    )

    blocked_ids = {row["coa_id"] for row in inventory_cogs_rows}
    blocked_lines = [aid for aid in account_ids if aid in blocked_ids]

    if blocked_lines:
        blocked_accounts = await conn.fetch(
            """
            SELECT account_code, name FROM chart_of_accounts
            WHERE id = ANY($1) AND tenant_id = $2
        """,
            blocked_lines,
            tenant_id,
        )
        names = ", ".join(f"{a['account_code']} {a['name']}" for a in blocked_accounts)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Manual journal tidak boleh menyentuh akun Persediaan/HPP. "
                f"Akun: {names}. "
                f"Gunakan modul Stock Adjustment untuk transaksi inventory."
            ),
        )


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
    sort_by: Optional[str] = Query(
        None,
        description="Sort field: created_at, journal_date, total_debit, description",
    ),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
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
            # Dihitung atas SELURUH himpunan terfilter, bukan halaman ini.
            # `total_amount` ditambahkan 3 Sep 2026 karena FE menghitungnya dari
            # array halaman: layar menunjukkan 299 jurnal sementara rupiahnya
            # hanya mencakup 20, dan angkanya berubah saat digulir.
            #
            # Nilai diambil dari `total_debit` di header. Diverifikasi 3 Sep
            # 2026 bahwa header = SUM(journal_lines.debit) persis
            # (193.064.615,00 keduanya), jadi ini tetap turunan jurnal, bukan
            # SUM tabel pembungkus.
            #
            # `void_count` dipisahkan supaya ketiganya SALING LEPAS dan
            # berjumlah tepat `total`. `reversed_count` kini berarti "sudah
            # DIBALIK" (`reversed_by_id`), bukan "ia adalah jurnal pembalik"
            # (`reversal_of_id`) -- lihat catatan panjang di JournalSummary.
            count_query = f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'DRAFT')  as draft_count,
                    COUNT(*) FILTER (WHERE status = 'POSTED') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'VOID')   as void_count,
                    COUNT(*) FILTER (WHERE reversed_by_id IS NOT NULL) as reversed_count,
                    COALESCE(SUM(je.total_debit), 0) as total_amount
                FROM journal_entries je
                WHERE {where_clause}
            """
            counts = await conn.fetchrow(count_query, *params)

            # Get data with pagination
            offset = (page - 1) * limit
            params.extend([limit, offset])

            # Dynamic sort
            ALLOWED_SORT_FIELDS = {
                "created_at": "je.created_at",
                "journal_date": "je.journal_date",
                "date": "je.journal_date",
                "total_debit": "je.total_debit",
                "amount": "je.total_debit",
                "description": "je.description",
            }
            sort_col = ALLOWED_SORT_FIELDS.get(sort_by or "created_at", "je.created_at")
            sort_dir = "ASC" if sort_order and sort_order.lower() == "asc" else "DESC"
            order_clause = f"{sort_col} {sort_dir}"

            query = f"""
                SELECT je.id, je.journal_number, je.journal_date, je.description,
                       je.source_type, je.total_debit, je.total_credit, je.status, je.created_at
                FROM journal_entries je
                WHERE {where_clause}
                ORDER BY {order_clause}
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
                    void_count=counts["void_count"],
                    reversed_count=counts["reversed_count"],
                    total_amount=counts["total_amount"] or Decimal("0"),
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
                    description=line.get("memo") or line.get("description", ""),
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
                    posted_at=je_row["updated_at"]
                    if je_row["status"] == "POSTED"
                    else None,
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

            async with conn.transaction():
                # Law 13: Advisory lock on manual journal creation
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"MANUAL_CREATE:{ctx['tenant_id']}",
                )

                # Law 23: Generate journal number inside transaction
                journal_number = await get_next_journal_number(
                    conn, ctx["tenant_id"], "JV", body.entry_date
                )

                # Law 31 Gate 4: Block derived-layer accounts in manual journal
                await validate_no_derived_layer_accounts(
                    conn, ctx["tenant_id"], body.lines
                )

                # Law 20: Always INSERT as DRAFT first
                journal_id = await conn.fetchval(
                    """
                    INSERT INTO journal_entries (
                        tenant_id, journal_number, journal_date, description,
                        source_type, total_debit, total_credit, status,
                        period_id, created_by
                    )
                    VALUES ($1, $2, $3, $4, 'MANUAL', $5, $6, 'DRAFT', $7, $8)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    journal_number,
                    body.entry_date,
                    body.description,
                    total_debit,
                    total_credit,
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

                # Law 20: If intended status is POSTED, update after lines (triggers hash chain)
                if initial_status == "POSTED":
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        journal_id,
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

            async with conn.transaction():
                # Law 13: Advisory lock on journal posting
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"MANUAL_POST:{str(journal_id)}",
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

            total_debit = sum(line["credit"] for line in lines)  # Swap debit/credit
            total_credit = sum(line["debit"] for line in lines)

            async with conn.transaction():
                # Law 13: Advisory lock on journal reversal
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"MANUAL_REVERSE:{str(journal_id)}",
                )

                # Law 23: Generate journal number inside transaction
                reversal_number = await get_next_journal_number(
                    conn, ctx["tenant_id"], "JV", body.reversal_date
                )

                # Law 20: Create reversal journal as DRAFT first
                reversal_id = await conn.fetchval(
                    """
                    INSERT INTO journal_entries (
                        tenant_id, journal_number, journal_date, description,
                        source_type, total_debit, total_credit, status,
                        period_id, reversal_of_id, reversal_reason, created_by
                    )
                    VALUES ($1, $2, $3, $4, 'MANUAL', $5, $6, 'DRAFT', $7, $8, $9, $10)
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
                        line.get("memo", ""),
                        line["credit"],
                        line["debit"],
                    )  # Swapped

                # Law 20: DRAFT -> POSTED after lines (triggers hash chain)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    reversal_id,
                )

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
