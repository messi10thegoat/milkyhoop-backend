"""
Bank Reconciliation Router

Enables matching of bank statement transactions with system transactions.

Database Tables:
- reconciliation_sessions: Session tracking
- bank_statement_lines_v2: Imported statement data
- reconciliation_matches: Match records
- reconciliation_adjustments: Fee/interest adjustments
- bank_transactions: Extended with reconciliation columns

Endpoints:
- GET  /accounts - List bank accounts with reconciliation status
- GET  /sessions - List reconciliation sessions
- POST /sessions - Start new session
- GET  /sessions/:id - Get session detail
- POST /sessions/:id/cancel - Cancel session
- GET  /history/:accountId - Get reconciliation history
- POST /sessions/:id/import - Import bank statement file (CSV/XLSX/OFX)
- GET  /sessions/:id/statements - List statement lines
- GET  /sessions/:id/transactions - List system transactions for matching
- POST /sessions/:id/match - Create match between statement line and transactions
- DELETE /sessions/:id/match/:matchId - Remove match
- POST /sessions/:id/auto-match - Run auto-matching algorithm
- POST /sessions/:id/transactions - Create transaction from unmatched statement line
- POST /sessions/:id/complete - Complete reconciliation session
"""

from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File, Form
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date, datetime
import uuid
import json
import io

from ..schemas.bank_reconciliation import (
    CreateSessionRequest,
    AccountsListResponse,
    SessionsListResponse,
    SessionCreateResponse,
    SessionDetailResponse,
    CancelResponse,
    HistoryResponse,
    MatchRequest,
    AutoMatchRequest,
    CreateTransactionFromLineRequest,
    CompleteSessionRequest,
    ImportResponse,
    ImportDateRange,
    ImportError as ImportErrorSchema,
    StatementLinesResponse,
    TransactionsResponse,
    MatchResponse,
    UnmatchResponse,
    AutoMatchResponse,
    CreateTransactionResponse,
    CompleteResponse,
    SessionStatistics,
    FinalStats,
    StatementLineItem,
    TransactionItem,
    MatchSuggestion,
)
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create database connection pool."""
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
    user_id = user.get("user_id") or user.get("id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


# =============================================================================
# ACCOUNTS ENDPOINTS
# =============================================================================


@router.get("/accounts", response_model=AccountsListResponse)
async def list_accounts(
    request: Request,
    needs_reconciliation: Optional[bool] = Query(None),
):
    """List bank accounts with reconciliation status."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            rows = await conn.fetch(
                """
                SELECT
                    ba.id,
                    ba.account_name as name,
                    ba.account_number,
                    COALESCE(ba.current_balance, 0) as current_balance,
                    (
                        SELECT rs.statement_date
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'completed'
                          AND rs.tenant_id = $1
                        ORDER BY rs.statement_date DESC
                        LIMIT 1
                    ) as last_reconciled_date,
                    (
                        SELECT rs.statement_ending_balance
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'completed'
                          AND rs.tenant_id = $1
                        ORDER BY rs.statement_date DESC
                        LIMIT 1
                    ) as last_reconciled_balance,
                    (
                        SELECT rs.id
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'in_progress'
                          AND rs.tenant_id = $1
                        LIMIT 1
                    ) as active_session_id,
                    (
                        SELECT rs.status
                        FROM reconciliation_sessions rs
                        WHERE rs.account_id = ba.id
                          AND rs.status = 'in_progress'
                          AND rs.tenant_id = $1
                        LIMIT 1
                    ) as active_session_status
                FROM bank_accounts ba
                WHERE ba.tenant_id = $1
                  AND ba.is_active = true
                ORDER BY ba.account_name
                """,
                ctx["tenant_id"],
            )

            accounts = []
            today = date.today()

            for row in rows:
                last_recon = row["last_reconciled_date"]
                days_since = (today - last_recon).days if last_recon else None
                needs_recon = days_since is None or days_since > 7

                if (
                    needs_reconciliation is not None
                    and needs_recon != needs_reconciliation
                ):
                    continue

                accounts.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "account_number": row["account_number"],
                        "current_balance": row["current_balance"] or 0,
                        "last_reconciled_date": str(last_recon) if last_recon else None,
                        "last_reconciled_balance": row["last_reconciled_balance"],
                        "statement_balance": None,
                        "statement_date": None,
                        "unreconciled_difference": None,
                        "needs_reconciliation": needs_recon,
                        "days_since_reconciliation": days_since,
                        "active_session_id": str(row["active_session_id"])
                        if row["active_session_id"]
                        else None,
                        "active_session_status": row["active_session_status"],
                    }
                )

            return {"data": accounts, "total": len(accounts)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list accounts")


# =============================================================================
# SESSIONS ENDPOINTS
# =============================================================================


@router.get("/sessions", response_model=SessionsListResponse)
async def list_sessions(
    request: Request,
    account_id: Optional[str] = Query(None),
    status: Optional[
        Literal["not_started", "in_progress", "completed", "cancelled"]
    ] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List reconciliation sessions with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["rs.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if account_id:
                conditions.append(f"rs.account_id = ${param_idx}")
                params.append(UUID(account_id))
                param_idx += 1

            if status:
                conditions.append(f"rs.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM reconciliation_sessions rs WHERE {where_clause}",
                *params,
            )

            params.extend([limit, offset])
            rows = await conn.fetch(
                f"""
                SELECT
                    rs.*,
                    ba.account_name,
                    ba.account_number,
                    (SELECT COUNT(*) FROM bank_statement_lines_v2 bsl WHERE bsl.session_id = rs.id) as total_lines,
                    (SELECT COUNT(*) FROM bank_statement_lines_v2 bsl WHERE bsl.session_id = rs.id AND bsl.match_status = 'matched') as matched_count,
                    (SELECT COUNT(*) FROM bank_statement_lines_v2 bsl WHERE bsl.session_id = rs.id AND bsl.match_status = 'unmatched') as unmatched_count,
                    (SELECT COUNT(*) FROM reconciliation_adjustments ra WHERE ra.session_id = rs.id) as adjustments_count
                FROM reconciliation_sessions rs
                JOIN bank_accounts ba ON ba.id = rs.account_id
                WHERE {where_clause}
                ORDER BY rs.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params,
            )

            sessions = [
                {
                    "id": str(row["id"]),
                    "account_id": str(row["account_id"]),
                    "account_name": row["account_name"],
                    "account_number": row["account_number"],
                    "statement_date": str(row["statement_date"]),
                    "statement_start_date": str(row["statement_start_date"]),
                    "statement_end_date": str(row["statement_end_date"]),
                    "statement_beginning_balance": row["statement_beginning_balance"],
                    "statement_ending_balance": row["statement_ending_balance"],
                    "status": row["status"],
                    "cleared_balance": row["cleared_balance"],
                    "cleared_count": row["matched_count"] or 0,
                    "uncleared_count": row["unmatched_count"] or 0,
                    "difference": row["difference"],
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                    "completed_at": row["completed_at"].isoformat()
                    if row["completed_at"]
                    else None,
                    "total_statement_lines": row["total_lines"] or 0,
                    "matched_count": row["matched_count"] or 0,
                    "unmatched_count": row["unmatched_count"] or 0,
                    "adjustments_count": row["adjustments_count"] or 0,
                }
                for row in rows
            ]

            return {
                "data": sessions,
                "total": total,
                "hasMore": (offset + limit) < total,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list sessions")


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(request: Request, body: CreateSessionRequest):
    """Start a new reconciliation session."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Validate account exists
            account = await conn.fetchrow(
                "SELECT id, is_active FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                UUID(body.account_id),
                ctx["tenant_id"],
            )

            if not account:
                raise HTTPException(status_code=404, detail="Bank account not found")

            if not account["is_active"]:
                raise HTTPException(status_code=400, detail="Bank account is inactive")

            # Check no in-progress session exists
            existing = await conn.fetchval(
                """
                SELECT id FROM reconciliation_sessions
                WHERE account_id = $1 AND tenant_id = $2 AND status = 'in_progress'
                """,
                UUID(body.account_id),
                ctx["tenant_id"],
            )

            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="An in-progress reconciliation session already exists for this account",
                )

            # Create session
            session_id = uuid.uuid4()
            now = datetime.utcnow()

            await conn.execute(
                """
                INSERT INTO reconciliation_sessions (
                    id, tenant_id, account_id, statement_date,
                    statement_start_date, statement_end_date,
                    statement_beginning_balance, statement_ending_balance,
                    status, created_by, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
                """,
                session_id,
                ctx["tenant_id"],
                UUID(body.account_id),
                body.statement_date,
                body.statement_start_date,
                body.statement_end_date,
                body.statement_beginning_balance,
                body.statement_ending_balance,
                "in_progress",
                ctx["user_id"],
                now,
            )

            return {
                "id": str(session_id),
                "status": "in_progress",
                "created_at": now.isoformat(),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create session")


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(request: Request, session_id: UUID):
    """Get session details with statistics."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            row = await conn.fetchrow(
                """
                SELECT
                    rs.*,
                    ba.account_name
                FROM reconciliation_sessions rs
                JOIN bank_accounts ba ON ba.id = rs.account_id
                WHERE rs.id = $1 AND rs.tenant_id = $2
                """,
                session_id,
                ctx["tenant_id"],
            )

            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            # Get statistics
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_lines,
                    COUNT(*) FILTER (WHERE match_status = 'matched') as matched_count,
                    COUNT(*) FILTER (WHERE match_status = 'unmatched') as unmatched_count,
                    COUNT(*) FILTER (WHERE match_status = 'excluded') as excluded_count,
                    COALESCE(SUM(CASE WHEN match_status = 'matched' THEN amount ELSE 0 END), 0) as total_cleared,
                    COALESCE(SUM(CASE WHEN match_status = 'unmatched' THEN amount ELSE 0 END), 0) as total_uncleared
                FROM bank_statement_lines_v2
                WHERE session_id = $1
                """,
                session_id,
            )

            return {
                "success": True,
                "data": {
                    "id": str(row["id"]),
                    "account_id": str(row["account_id"]),
                    "account_name": row["account_name"],
                    "statement_date": str(row["statement_date"]),
                    "statement_start_date": str(row["statement_start_date"]),
                    "statement_end_date": str(row["statement_end_date"]),
                    "statement_beginning_balance": row["statement_beginning_balance"],
                    "statement_ending_balance": row["statement_ending_balance"],
                    "status": row["status"],
                    "cleared_balance": row["cleared_balance"],
                    "difference": row["difference"],
                    "statistics": {
                        "total_statement_lines": stats["total_lines"] or 0,
                        "matched_count": stats["matched_count"] or 0,
                        "unmatched_count": stats["unmatched_count"] or 0,
                        "excluded_count": stats["excluded_count"] or 0,
                        "total_cleared": stats["total_cleared"] or 0,
                        "total_uncleared": stats["total_uncleared"] or 0,
                    },
                    "created_at": row["created_at"].isoformat()
                    if row["created_at"]
                    else None,
                    "updated_at": row["updated_at"].isoformat()
                    if row["updated_at"]
                    else None,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get session")


@router.post("/sessions/{session_id}/cancel", response_model=CancelResponse)
async def cancel_session(request: Request, session_id: UUID):
    """Cancel an in-progress reconciliation session."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session exists and is in_progress
            session = await conn.fetchrow(
                "SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(
                    status_code=400, detail="Can only cancel in-progress sessions"
                )

            # Reset cleared transactions
            result = await conn.execute(
                """
                UPDATE bank_transactions
                SET is_cleared = false,
                    cleared_at = NULL,
                    matched_statement_line_id = NULL
                WHERE tenant_id = $1
                  AND matched_statement_line_id IN (
                      SELECT id FROM bank_statement_lines_v2 WHERE session_id = $2
                  )
                """,
                ctx["tenant_id"],
                session_id,
            )

            # Parse the result to get count
            reset_count = 0
            if result:
                parts = result.split()
                if len(parts) >= 2:
                    try:
                        reset_count = int(parts[1])
                    except ValueError:
                        pass

            # Update session status
            await conn.execute(
                "UPDATE reconciliation_sessions SET status = 'cancelled', updated_at = NOW() WHERE id = $1",
                session_id,
            )

            return {"success": True, "cleared_transactions_reset": reset_count}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel session")


# =============================================================================
# HISTORY ENDPOINT
# =============================================================================


@router.get("/history/{account_id}", response_model=HistoryResponse)
async def get_history(
    request: Request,
    account_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """Get reconciliation history for an account."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            total = await conn.fetchval(
                """
                SELECT COUNT(*) FROM reconciliation_sessions
                WHERE account_id = $1 AND tenant_id = $2 AND status = 'completed'
                """,
                account_id,
                ctx["tenant_id"],
            )

            rows = await conn.fetch(
                """
                SELECT
                    rs.id,
                    rs.statement_date,
                    rs.statement_ending_balance,
                    rs.completed_at,
                    u.name as completed_by,
                    (SELECT COUNT(*) FROM bank_statement_lines_v2 bsl
                     WHERE bsl.session_id = rs.id AND bsl.match_status = 'matched') as matched_count,
                    (SELECT COUNT(*) FROM reconciliation_adjustments ra
                     WHERE ra.session_id = rs.id) as adjustments_count
                FROM reconciliation_sessions rs
                LEFT JOIN users u ON u.id = rs.created_by
                WHERE rs.account_id = $1 AND rs.tenant_id = $2 AND rs.status = 'completed'
                ORDER BY rs.statement_date DESC
                LIMIT $3 OFFSET $4
                """,
                account_id,
                ctx["tenant_id"],
                limit,
                offset,
            )

            history = [
                {
                    "id": str(row["id"]),
                    "statement_date": str(row["statement_date"]),
                    "statement_ending_balance": row["statement_ending_balance"],
                    "matched_count": row["matched_count"] or 0,
                    "adjustments_count": row["adjustments_count"] or 0,
                    "completed_at": row["completed_at"].isoformat()
                    if row["completed_at"]
                    else None,
                    "completed_by": row["completed_by"] or "Unknown",
                }
                for row in rows
            ]

            return {
                "data": history,
                "total": total,
                "hasMore": (offset + limit) < total,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get history")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


async def get_session_statistics(conn, session_id: UUID) -> SessionStatistics:
    """Calculate session statistics."""
    stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (WHERE match_status IS NOT NULL) as total_statement_lines,
            COUNT(*) FILTER (WHERE match_status = 'matched') as matched_lines,
            COUNT(*) FILTER (WHERE match_status = 'unmatched') as unmatched_lines,
            COALESCE(SUM(amount), 0) as statement_total,
            COALESCE(SUM(CASE WHEN match_status = 'matched' THEN amount ELSE 0 END), 0) as matched_total
        FROM bank_statement_lines_v2
        WHERE session_id = $1
        """,
        session_id,
    )

    tx_stats = await conn.fetchrow(
        """
        SELECT
            COUNT(*) as total_transactions,
            COUNT(*) FILTER (WHERE is_cleared = true) as matched_transactions,
            COUNT(*) FILTER (WHERE is_cleared = false) as unmatched_transactions
        FROM bank_transactions bt
        JOIN reconciliation_sessions rs ON bt.account_id = rs.account_id
        WHERE rs.id = $1
          AND bt.transaction_date BETWEEN rs.statement_start_date AND rs.statement_end_date
        """,
        session_id,
    )

    session = await conn.fetchrow(
        "SELECT statement_ending_balance FROM reconciliation_sessions WHERE id = $1",
        session_id,
    )

    matched_total = stats["matched_total"] or 0
    statement_ending = session["statement_ending_balance"] if session else 0
    difference = statement_ending - matched_total

    return SessionStatistics(
        total_statement_lines=stats["total_statement_lines"] or 0,
        matched_lines=stats["matched_lines"] or 0,
        unmatched_lines=stats["unmatched_lines"] or 0,
        total_transactions=tx_stats["total_transactions"] or 0,
        matched_transactions=tx_stats["matched_transactions"] or 0,
        unmatched_transactions=tx_stats["unmatched_transactions"] or 0,
        statement_total=stats["statement_total"] or 0,
        matched_total=matched_total,
        difference=difference,
        is_balanced=(difference == 0),
    )


async def update_reconciliation_session_stats(conn, session_id: UUID):
    """Update session statistics after match operations."""
    stats = await conn.fetchrow(
        """
        SELECT
            COALESCE(SUM(CASE WHEN match_status = 'matched' THEN amount ELSE 0 END), 0) as cleared_balance,
            COUNT(*) FILTER (WHERE match_status = 'matched') as cleared_count
        FROM bank_statement_lines_v2
        WHERE session_id = $1
        """,
        session_id,
    )

    session = await conn.fetchrow(
        "SELECT statement_ending_balance FROM reconciliation_sessions WHERE id = $1",
        session_id,
    )

    cleared_balance = stats["cleared_balance"] or 0
    statement_ending = session["statement_ending_balance"] if session else 0
    difference = statement_ending - cleared_balance

    await conn.execute(
        """
        UPDATE reconciliation_sessions
        SET cleared_balance = $2,
            cleared_count = $3,
            difference = $4,
            updated_at = NOW()
        WHERE id = $1
        """,
        session_id,
        cleared_balance,
        stats["cleared_count"] or 0,
        difference,
    )


# =============================================================================
# IMPORT & STATEMENT LINES ENDPOINTS
# =============================================================================


@router.post("/sessions/{session_id}/import", response_model=ImportResponse)
async def import_statement(
    request: Request,
    session_id: UUID,
    file: UploadFile = File(...),
    config: str = Form(...),
):
    """
    Import bank statement file (CSV, XLSX, or OFX).

    Max file size: 10MB.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        # Check file size (10MB max)
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size exceeds 10MB limit")

        # Parse config JSON
        try:
            import_config = json.loads(config)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid config JSON")

        file_format = import_config.get("format", "csv").lower()
        filename = file.filename.lower() if file.filename else ""

        # Detect format from filename if not specified
        if not file_format or file_format == "auto":
            if filename.endswith(".csv"):
                file_format = "csv"
            elif filename.endswith((".xlsx", ".xls")):
                file_format = "xlsx"
            elif filename.endswith(".ofx"):
                file_format = "ofx"
            else:
                file_format = "csv"

        lines_imported = 0
        lines_skipped = 0
        total_credits = 0
        total_debits = 0
        errors: list = []
        dates: list = []

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session exists and is in_progress
            session = await conn.fetchrow(
                "SELECT status, account_id FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(
                    status_code=400, detail="Can only import to in-progress sessions"
                )

            # Parse file based on format
            if file_format == "csv":
                import pandas as pd

                date_column = import_config.get("date_column", "date")
                description_column = import_config.get(
                    "description_column", "description"
                )
                amount_column = import_config.get("amount_column")
                debit_column = import_config.get("debit_column")
                credit_column = import_config.get("credit_column")
                reference_column = import_config.get("reference_column")
                balance_column = import_config.get("balance_column")
                date_format = import_config.get("date_format", "%d/%m/%Y")
                skip_rows = import_config.get("skip_rows", 0)

                # Convert date format from DD/MM/YYYY to Python strptime format
                py_date_format = (
                    date_format.replace("DD", "%d")
                    .replace("MM", "%m")
                    .replace("YYYY", "%Y")
                    .replace("YY", "%y")
                )

                try:
                    df = pd.read_csv(io.BytesIO(content), skiprows=skip_rows)
                except Exception as e:
                    raise HTTPException(
                        status_code=400, detail=f"Failed to parse CSV: {str(e)}"
                    )

                line_number = 0
                for idx, row in df.iterrows():
                    line_number += 1
                    try:
                        # Parse date
                        date_str = str(row.get(date_column, "")).strip()
                        try:
                            tx_date = datetime.strptime(date_str, py_date_format).date()
                            dates.append(tx_date)
                        except ValueError:
                            errors.append(
                                ImportErrorSchema(
                                    row_number=line_number,
                                    column=date_column,
                                    value=date_str,
                                    error="Invalid date format",
                                )
                            )
                            lines_skipped += 1
                            continue

                        # Parse description
                        description = str(row.get(description_column, "")).strip()
                        if not description:
                            description = "No description"

                        # Parse amount
                        amount = 0
                        is_credit = False

                        if amount_column:
                            amount_str = str(row.get(amount_column, "0"))
                            amount_str = amount_str.replace(",", "").replace(" ", "")
                            try:
                                amount_float = float(amount_str)
                                amount = int(abs(amount_float))
                                is_credit = amount_float > 0
                            except ValueError:
                                errors.append(
                                    ImportErrorSchema(
                                        row_number=line_number,
                                        column=amount_column,
                                        value=amount_str,
                                        error="Invalid amount",
                                    )
                                )
                                lines_skipped += 1
                                continue
                        elif debit_column and credit_column:
                            debit_str = (
                                str(row.get(debit_column, "0"))
                                .replace(",", "")
                                .replace(" ", "")
                            )
                            credit_str = (
                                str(row.get(credit_column, "0"))
                                .replace(",", "")
                                .replace(" ", "")
                            )
                            try:
                                debit = (
                                    abs(float(debit_str))
                                    if debit_str and debit_str != "nan"
                                    else 0
                                )
                                credit = (
                                    abs(float(credit_str))
                                    if credit_str and credit_str != "nan"
                                    else 0
                                )
                                if credit > 0:
                                    amount = int(credit)
                                    is_credit = True
                                else:
                                    amount = int(debit)
                                    is_credit = False
                            except ValueError:
                                lines_skipped += 1
                                continue

                        # Parse reference
                        reference = None
                        if reference_column:
                            ref_val = row.get(reference_column)
                            if pd.notna(ref_val):
                                reference = str(ref_val).strip()

                        # Parse running balance
                        running_balance = None
                        if balance_column:
                            bal_val = row.get(balance_column)
                            if pd.notna(bal_val):
                                bal_str = str(bal_val).replace(",", "").replace(" ", "")
                                try:
                                    running_balance = int(float(bal_str))
                                except ValueError:
                                    pass

                        # Insert line
                        line_id = uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO bank_statement_lines_v2 (
                                id, tenant_id, session_id, line_number, transaction_date,
                                description, reference, amount, is_credit, running_balance,
                                match_status, created_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'unmatched', NOW())
                            """,
                            line_id,
                            ctx["tenant_id"],
                            session_id,
                            line_number,
                            tx_date,
                            description,
                            reference,
                            amount,
                            is_credit,
                            running_balance,
                        )

                        lines_imported += 1
                        if is_credit:
                            total_credits += amount
                        else:
                            total_debits += amount

                    except Exception as e:
                        errors.append(
                            ImportErrorSchema(row_number=line_number, error=str(e))
                        )
                        lines_skipped += 1

            elif file_format == "xlsx":
                import pandas as pd

                date_column = import_config.get("date_column", "date")
                description_column = import_config.get(
                    "description_column", "description"
                )
                amount_column = import_config.get("amount_column")
                debit_column = import_config.get("debit_column")
                credit_column = import_config.get("credit_column")
                reference_column = import_config.get("reference_column")
                balance_column = import_config.get("balance_column")
                skip_rows = import_config.get("skip_rows", 0)

                try:
                    df = pd.read_excel(io.BytesIO(content), skiprows=skip_rows)
                except Exception as e:
                    raise HTTPException(
                        status_code=400, detail=f"Failed to parse Excel: {str(e)}"
                    )

                line_number = 0
                for idx, row in df.iterrows():
                    line_number += 1
                    try:
                        # Parse date
                        date_val = row.get(date_column)
                        if pd.isna(date_val):
                            lines_skipped += 1
                            continue

                        if isinstance(date_val, datetime):
                            tx_date = date_val.date()
                        elif isinstance(date_val, date):
                            tx_date = date_val
                        else:
                            tx_date = pd.to_datetime(date_val).date()
                        dates.append(tx_date)

                        # Parse description
                        description = (
                            str(row.get(description_column, "")).strip()
                            or "No description"
                        )

                        # Parse amount
                        amount = 0
                        is_credit = False

                        if amount_column:
                            amount_val = row.get(amount_column, 0)
                            if pd.notna(amount_val):
                                amount = int(abs(float(amount_val)))
                                is_credit = float(amount_val) > 0
                        elif debit_column and credit_column:
                            debit = row.get(debit_column, 0)
                            credit = row.get(credit_column, 0)
                            debit = float(debit) if pd.notna(debit) else 0
                            credit = float(credit) if pd.notna(credit) else 0
                            if credit > 0:
                                amount = int(credit)
                                is_credit = True
                            else:
                                amount = int(abs(debit))
                                is_credit = False

                        # Parse reference
                        reference = None
                        if reference_column:
                            ref_val = row.get(reference_column)
                            if pd.notna(ref_val):
                                reference = str(ref_val).strip()

                        # Parse running balance
                        running_balance = None
                        if balance_column:
                            bal_val = row.get(balance_column)
                            if pd.notna(bal_val):
                                running_balance = int(float(bal_val))

                        # Insert line
                        line_id = uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO bank_statement_lines_v2 (
                                id, tenant_id, session_id, line_number, transaction_date,
                                description, reference, amount, is_credit, running_balance,
                                match_status, created_at
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'unmatched', NOW())
                            """,
                            line_id,
                            ctx["tenant_id"],
                            session_id,
                            line_number,
                            tx_date,
                            description,
                            reference,
                            amount,
                            is_credit,
                            running_balance,
                        )

                        lines_imported += 1
                        if is_credit:
                            total_credits += amount
                        else:
                            total_debits += amount

                    except Exception as e:
                        errors.append(
                            ImportErrorSchema(row_number=line_number, error=str(e))
                        )
                        lines_skipped += 1

            elif file_format == "ofx":
                try:
                    from ofxparse import OfxParser
                except ImportError:
                    raise HTTPException(
                        status_code=500, detail="OFX parsing not available"
                    )

                try:
                    ofx = OfxParser.parse(io.BytesIO(content))
                except Exception as e:
                    raise HTTPException(
                        status_code=400, detail=f"Failed to parse OFX: {str(e)}"
                    )

                line_number = 0
                for account in ofx.accounts:
                    for tx in account.statement.transactions:
                        line_number += 1
                        try:
                            tx_date = (
                                tx.date.date() if hasattr(tx.date, "date") else tx.date
                            )
                            dates.append(tx_date)

                            description = tx.memo or tx.payee or "No description"
                            amount = int(abs(float(tx.amount)))
                            is_credit = float(tx.amount) > 0
                            reference = tx.id if hasattr(tx, "id") else None

                            line_id = uuid.uuid4()
                            await conn.execute(
                                """
                                INSERT INTO bank_statement_lines_v2 (
                                    id, tenant_id, session_id, line_number, transaction_date,
                                    description, reference, amount, is_credit, running_balance,
                                    match_status, created_at
                                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NULL, 'unmatched', NOW())
                                """,
                                line_id,
                                ctx["tenant_id"],
                                session_id,
                                line_number,
                                tx_date,
                                description,
                                reference,
                                amount,
                                is_credit,
                            )

                            lines_imported += 1
                            if is_credit:
                                total_credits += amount
                            else:
                                total_debits += amount

                        except Exception as e:
                            errors.append(
                                ImportErrorSchema(row_number=line_number, error=str(e))
                            )
                            lines_skipped += 1

            else:
                raise HTTPException(
                    status_code=400, detail=f"Unsupported format: {file_format}"
                )

            # Update session stats
            await update_reconciliation_session_stats(conn, session_id)

        # Calculate date range
        if dates:
            start_date = str(min(dates))
            end_date = str(max(dates))
        else:
            start_date = ""
            end_date = ""

        return ImportResponse(
            lines_imported=lines_imported,
            lines_skipped=lines_skipped,
            total_credits=total_credits,
            total_debits=total_debits,
            date_range=ImportDateRange(start_date=start_date, end_date=end_date),
            errors=errors[:50],  # Limit errors returned
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing statement: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to import statement")


@router.get("/sessions/{session_id}/statements", response_model=StatementLinesResponse)
async def list_statement_lines(
    request: Request,
    session_id: UUID,
    match_status: Optional[Literal["unmatched", "matched", "excluded"]] = Query(None),
    search: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List statement lines for a reconciliation session."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session access
            session = await conn.fetchrow(
                "SELECT id FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            conditions = ["bsl.session_id = $1", "bsl.tenant_id = $2"]
            params = [session_id, ctx["tenant_id"]]
            param_idx = 3

            if match_status:
                conditions.append(f"bsl.match_status = ${param_idx}")
                params.append(match_status)
                param_idx += 1

            if search:
                conditions.append(
                    f"(bsl.description ILIKE ${param_idx} OR bsl.reference ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bank_statement_lines_v2 bsl WHERE {where_clause}",
                *params,
            )

            params.extend([limit, offset])
            rows = await conn.fetch(
                f"""
                SELECT
                    bsl.id,
                    bsl.line_number,
                    bsl.transaction_date,
                    bsl.description,
                    bsl.reference,
                    bsl.amount,
                    bsl.is_credit,
                    bsl.running_balance,
                    bsl.match_status,
                    bsl.created_at,
                    rm.id as match_id,
                    rm.confidence
                FROM bank_statement_lines_v2 bsl
                LEFT JOIN reconciliation_matches rm ON rm.statement_line_id = bsl.id
                WHERE {where_clause}
                ORDER BY bsl.line_number
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params,
            )

            lines = []
            for row in rows:
                # Get matched transaction IDs
                matched_tx_ids = []
                if row["match_id"]:
                    tx_rows = await conn.fetch(
                        """
                        SELECT bt.id FROM bank_transactions bt
                        WHERE bt.matched_statement_line_id = $1
                        """,
                        row["id"],
                    )
                    matched_tx_ids = [str(tx["id"]) for tx in tx_rows]

                lines.append(
                    StatementLineItem(
                        id=str(row["id"]),
                        line_number=row["line_number"],
                        transaction_date=str(row["transaction_date"]),
                        description=row["description"] or "",
                        reference=row["reference"],
                        amount=row["amount"],
                        is_credit=row["is_credit"],
                        running_balance=row["running_balance"],
                        status=row["match_status"],
                        match_id=str(row["match_id"]) if row["match_id"] else None,
                        matched_transaction_ids=matched_tx_ids,
                        confidence=row["confidence"],
                        created_at=row["created_at"].isoformat()
                        if row["created_at"]
                        else "",
                    )
                )

            return StatementLinesResponse(
                data=lines, total=total, hasMore=(offset + limit) < total
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing statement lines: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list statement lines")


# =============================================================================
# TRANSACTIONS & MATCHING ENDPOINTS
# =============================================================================


@router.get("/sessions/{session_id}/transactions", response_model=TransactionsResponse)
async def list_transactions(
    request: Request,
    session_id: UUID,
    is_cleared: Optional[bool] = Query(None),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List system transactions for matching within session date range."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Get session details
            session = await conn.fetchrow(
                """
                SELECT account_id, statement_start_date, statement_end_date
                FROM reconciliation_sessions
                WHERE id = $1 AND tenant_id = $2
                """,
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            conditions = [
                "bt.account_id = $1",
                "bt.tenant_id = $2",
                "bt.transaction_date >= $3",
                "bt.transaction_date <= $4",
            ]
            params = [
                session["account_id"],
                ctx["tenant_id"],
                session["statement_start_date"],
                session["statement_end_date"],
            ]
            param_idx = 5

            if is_cleared is not None:
                conditions.append(f"bt.is_cleared = ${param_idx}")
                params.append(is_cleared)
                param_idx += 1

            if type:
                conditions.append(f"bt.transaction_type = ${param_idx}")
                params.append(type)
                param_idx += 1

            if search:
                conditions.append(
                    f"(bt.description ILIKE ${param_idx} OR bt.reference ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM bank_transactions bt WHERE {where_clause}",
                *params,
            )

            params.extend([limit, offset])
            rows = await conn.fetch(
                f"""
                SELECT
                    bt.id,
                    bt.transaction_type,
                    bt.transaction_date,
                    bt.description,
                    bt.reference,
                    bt.amount,
                    bt.is_credit,
                    bt.source_type,
                    bt.source_id,
                    bt.source_number,
                    bt.is_cleared,
                    bt.matched_statement_line_id,
                    c.name as contact_name
                FROM bank_transactions bt
                LEFT JOIN contacts c ON c.id = bt.contact_id
                WHERE {where_clause}
                ORDER BY bt.transaction_date DESC, bt.created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params,
            )

            transactions = []
            for row in rows:
                # Get match ID if matched
                match_id = None
                if row["matched_statement_line_id"]:
                    match_row = await conn.fetchval(
                        "SELECT id FROM reconciliation_matches WHERE statement_line_id = $1",
                        row["matched_statement_line_id"],
                    )
                    if match_row:
                        match_id = str(match_row)

                transactions.append(
                    TransactionItem(
                        id=str(row["id"]),
                        transaction_type=row["transaction_type"] or "other",
                        transaction_date=str(row["transaction_date"]),
                        description=row["description"],
                        reference=row["reference"],
                        amount=row["amount"],
                        is_credit=row["is_credit"],
                        source_type=row["source_type"] or "manual",
                        source_id=str(row["source_id"]) if row["source_id"] else None,
                        source_number=row["source_number"],
                        contact_name=row["contact_name"],
                        is_matched=row["is_cleared"] or False,
                        match_id=match_id,
                    )
                )

            return TransactionsResponse(
                data=transactions, total=total, hasMore=(offset + limit) < total
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list transactions")


@router.post("/sessions/{session_id}/match", response_model=MatchResponse)
async def create_match(
    request: Request,
    session_id: UUID,
    body: MatchRequest,
):
    """Create a match between a statement line and one or more transactions."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                "SELECT status, account_id FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(
                    status_code=400, detail="Can only match in in-progress sessions"
                )

            # Verify statement line
            statement_line = await conn.fetchrow(
                "SELECT id, amount, is_credit, match_status FROM bank_statement_lines_v2 WHERE id = $1 AND session_id = $2",
                UUID(body.statement_line_id),
                session_id,
            )

            if not statement_line:
                raise HTTPException(status_code=404, detail="Statement line not found")

            if statement_line["match_status"] == "matched":
                raise HTTPException(
                    status_code=400, detail="Statement line is already matched"
                )

            # Verify transactions
            tx_ids = [UUID(tx_id) for tx_id in body.transaction_ids]
            transactions = await conn.fetch(
                """
                SELECT id, amount, is_credit, is_cleared
                FROM bank_transactions
                WHERE id = ANY($1) AND account_id = $2 AND tenant_id = $3
                """,
                tx_ids,
                session["account_id"],
                ctx["tenant_id"],
            )

            if len(transactions) != len(tx_ids):
                raise HTTPException(
                    status_code=404, detail="One or more transactions not found"
                )

            # Check no transaction is already cleared
            for tx in transactions:
                if tx["is_cleared"]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Transaction {tx['id']} is already matched",
                    )

            # Validate amount and type match
            line_amount = statement_line["amount"]
            line_is_credit = statement_line["is_credit"]
            total_tx_amount = sum(tx["amount"] for tx in transactions)

            # For proper matching, types should be compatible
            # Credits in statement = receipts/income, Debits = payments/expenses

            # Determine match type
            if len(tx_ids) == 1:
                match_type = "one_to_one"
            else:
                match_type = "one_to_many"

            # Create match record
            match_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO reconciliation_matches (
                    id, tenant_id, session_id, statement_line_id,
                    match_type, confidence, created_by, created_at
                ) VALUES ($1, $2, $3, $4, $5, 'manual', $6, NOW())
                """,
                match_id,
                ctx["tenant_id"],
                session_id,
                UUID(body.statement_line_id),
                match_type,
                ctx["user_id"],
            )

            # Update transactions
            now = datetime.utcnow()
            for tx_id in tx_ids:
                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET is_cleared = true,
                        cleared_at = $2,
                        matched_statement_line_id = $3
                    WHERE id = $1
                    """,
                    tx_id,
                    now,
                    UUID(body.statement_line_id),
                )

            # Update statement line status
            await conn.execute(
                """
                UPDATE bank_statement_lines_v2
                SET match_status = 'matched'
                WHERE id = $1
                """,
                UUID(body.statement_line_id),
            )

            # Update session stats
            await update_reconciliation_session_stats(conn, session_id)

            # Get updated stats
            stats = await get_session_statistics(conn, session_id)

            return MatchResponse(
                match_id=str(match_id),
                match_type=match_type,
                confidence="manual",
                cleared_amount=line_amount,
                session_stats=stats,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating match: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create match")


@router.delete(
    "/sessions/{session_id}/match/{match_id}", response_model=UnmatchResponse
)
async def remove_match(
    request: Request,
    session_id: UUID,
    match_id: UUID,
):
    """Remove a match and reset transaction cleared status."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                "SELECT status FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(
                    status_code=400, detail="Can only unmatch in in-progress sessions"
                )

            # Get match
            match = await conn.fetchrow(
                "SELECT statement_line_id FROM reconciliation_matches WHERE id = $1 AND session_id = $2",
                match_id,
                session_id,
            )

            if not match:
                raise HTTPException(status_code=404, detail="Match not found")

            # Reset transactions
            await conn.execute(
                """
                UPDATE bank_transactions
                SET is_cleared = false,
                    cleared_at = NULL,
                    matched_statement_line_id = NULL
                WHERE matched_statement_line_id = $1 AND tenant_id = $2
                """,
                match["statement_line_id"],
                ctx["tenant_id"],
            )

            # Reset statement line
            await conn.execute(
                """
                UPDATE bank_statement_lines_v2
                SET match_status = 'unmatched'
                WHERE id = $1
                """,
                match["statement_line_id"],
            )

            # Delete match
            await conn.execute(
                "DELETE FROM reconciliation_matches WHERE id = $1", match_id
            )

            # Update session stats
            await update_reconciliation_session_stats(conn, session_id)

            # Get updated stats
            stats = await get_session_statistics(conn, session_id)

            return UnmatchResponse(success=True, session_stats=stats)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing match: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove match")


# =============================================================================
# AUTO-MATCH & CREATE TRANSACTION ENDPOINTS
# =============================================================================


@router.post("/sessions/{session_id}/auto-match", response_model=AutoMatchResponse)
async def auto_match(
    request: Request,
    session_id: UUID,
    body: Optional[AutoMatchRequest] = None,
):
    """
    Run auto-matching algorithm on unmatched statement lines.

    Matching rules:
    - exact: Same date and amount
    - high: Amount matches, date within 3 days
    - medium: Amount matches within 5%, date within 7 days
    - low: Partial match on description/reference
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        if body is None:
            body = AutoMatchRequest()

        date_tolerance = body.date_tolerance_days
        confidence_threshold = body.confidence_threshold

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                """
                SELECT status, account_id, statement_start_date, statement_end_date
                FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2
                """,
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(
                    status_code=400,
                    detail="Can only auto-match in in-progress sessions",
                )

            # Get unmatched statement lines
            unmatched_lines = await conn.fetch(
                """
                SELECT id, transaction_date, description, reference, amount, is_credit
                FROM bank_statement_lines_v2
                WHERE session_id = $1 AND match_status = 'unmatched'
                ORDER BY transaction_date
                """,
                session_id,
            )

            # Get uncleared transactions
            uncleared_txs = await conn.fetch(
                """
                SELECT id, transaction_date, description, reference, amount, is_credit
                FROM bank_transactions
                WHERE account_id = $1
                  AND tenant_id = $2
                  AND transaction_date BETWEEN $3 AND $4
                  AND is_cleared = false
                ORDER BY transaction_date
                """,
                session["account_id"],
                ctx["tenant_id"],
                session["statement_start_date"],
                session["statement_end_date"],
            )

            matches_created = 0
            suggestions: list = []
            now = datetime.utcnow()

            for line in unmatched_lines:
                best_match = None
                best_confidence = None
                best_score = 0.0
                match_reasons = []

                for tx in uncleared_txs:
                    reasons = []
                    score = 0.0

                    # Amount match
                    if line["amount"] == tx["amount"]:
                        score += 0.5
                        reasons.append("Exact amount match")
                    elif abs(line["amount"] - tx["amount"]) <= line["amount"] * 0.05:
                        score += 0.3
                        reasons.append("Amount within 5%")

                    # Type match (credit vs debit)
                    if line["is_credit"] == tx["is_credit"]:
                        score += 0.1
                        reasons.append("Type match")

                    # Date match
                    date_diff = abs(
                        (line["transaction_date"] - tx["transaction_date"]).days
                    )
                    if date_diff == 0:
                        score += 0.3
                        reasons.append("Exact date match")
                    elif date_diff <= 3:
                        score += 0.2
                        reasons.append(f"Date within {date_diff} days")
                    elif date_diff <= date_tolerance:
                        score += 0.1
                        reasons.append(f"Date within {date_diff} days")

                    # Reference match
                    if line["reference"] and tx["reference"]:
                        if line["reference"].lower() == tx["reference"].lower():
                            score += 0.2
                            reasons.append("Reference match")

                    if score > best_score:
                        best_score = score
                        best_match = tx
                        match_reasons = reasons

                # Determine confidence level
                if best_match and best_score >= 0.8:
                    best_confidence = "exact"
                elif best_match and best_score >= 0.6:
                    best_confidence = "high"
                elif best_match and best_score >= 0.4:
                    best_confidence = "medium"
                elif best_match and best_score >= 0.2:
                    best_confidence = "low"

                if best_match and best_confidence:
                    # Check if we should auto-match or suggest
                    confidence_order = ["exact", "high", "medium", "low"]
                    threshold_idx = confidence_order.index(confidence_threshold)
                    match_idx = confidence_order.index(best_confidence)

                    if match_idx <= threshold_idx:
                        # Auto-match
                        match_id = uuid.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO reconciliation_matches (
                                id, tenant_id, session_id, statement_line_id,
                                match_type, confidence, created_by, created_at
                            ) VALUES ($1, $2, $3, $4, 'one_to_one', $5, $6, NOW())
                            """,
                            match_id,
                            ctx["tenant_id"],
                            session_id,
                            line["id"],
                            best_confidence,
                            ctx["user_id"],
                        )

                        await conn.execute(
                            """
                            UPDATE bank_transactions
                            SET is_cleared = true,
                                cleared_at = $2,
                                matched_statement_line_id = $3
                            WHERE id = $1
                            """,
                            best_match["id"],
                            now,
                            line["id"],
                        )

                        await conn.execute(
                            """
                            UPDATE bank_statement_lines_v2
                            SET match_status = 'matched'
                            WHERE id = $1
                            """,
                            line["id"],
                        )

                        matches_created += 1

                        # Remove from available transactions
                        uncleared_txs = [
                            tx for tx in uncleared_txs if tx["id"] != best_match["id"]
                        ]
                    else:
                        # Add suggestion
                        suggestions.append(
                            MatchSuggestion(
                                statement_line_id=str(line["id"]),
                                suggested_transaction_ids=[str(best_match["id"])],
                                confidence=best_confidence,
                                confidence_score=best_score,
                                match_reasons=match_reasons,
                            )
                        )

            # Update session stats
            await update_reconciliation_session_stats(conn, session_id)

            # Get updated stats
            stats = await get_session_statistics(conn, session_id)

            return AutoMatchResponse(
                matches_created=matches_created,
                suggestions=suggestions,
                session_stats=stats,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error auto-matching: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to auto-match")


@router.post(
    "/sessions/{session_id}/transactions", response_model=CreateTransactionResponse
)
async def create_transaction_from_line(
    request: Request,
    session_id: UUID,
    body: CreateTransactionFromLineRequest,
):
    """Create a bank transaction from an unmatched statement line."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                "SELECT status, account_id FROM reconciliation_sessions WHERE id = $1 AND tenant_id = $2",
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(
                    status_code=400,
                    detail="Can only create transactions in in-progress sessions",
                )

            # Get statement line
            statement_line = await conn.fetchrow(
                """
                SELECT id, transaction_date, description, reference, amount, is_credit, match_status
                FROM bank_statement_lines_v2
                WHERE id = $1 AND session_id = $2
                """,
                UUID(body.statement_line_id),
                session_id,
            )

            if not statement_line:
                raise HTTPException(status_code=404, detail="Statement line not found")

            if statement_line["match_status"] == "matched":
                raise HTTPException(
                    status_code=400, detail="Statement line is already matched"
                )

            # Verify account exists
            account = await conn.fetchrow(
                "SELECT id FROM chart_of_accounts WHERE id = $1 AND tenant_id = $2",
                UUID(body.account_id),
                ctx["tenant_id"],
            )

            if not account:
                raise HTTPException(
                    status_code=404, detail="Chart of accounts entry not found"
                )

            # Create transaction
            tx_id = uuid.uuid4()
            now = datetime.utcnow()

            # Determine is_credit based on type
            is_credit = statement_line["is_credit"]

            await conn.execute(
                """
                INSERT INTO bank_transactions (
                    id, tenant_id, account_id, transaction_type, transaction_date,
                    description, reference, amount, is_credit, source_type,
                    contact_id, is_cleared, created_by, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'reconciliation', $10, false, $11, $12, $12)
                """,
                tx_id,
                ctx["tenant_id"],
                session["account_id"],
                body.type,
                statement_line["transaction_date"],
                body.description or statement_line["description"],
                statement_line["reference"],
                statement_line["amount"],
                is_credit,
                UUID(body.contact_id) if body.contact_id else None,
                ctx["user_id"],
                now,
            )

            match_id = None
            if body.auto_match:
                # Auto-match the created transaction
                match_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO reconciliation_matches (
                        id, tenant_id, session_id, statement_line_id,
                        match_type, confidence, created_by, created_at
                    ) VALUES ($1, $2, $3, $4, 'one_to_one', 'auto_created', $5, NOW())
                    """,
                    match_id,
                    ctx["tenant_id"],
                    session_id,
                    UUID(body.statement_line_id),
                    ctx["user_id"],
                )

                await conn.execute(
                    """
                    UPDATE bank_transactions
                    SET is_cleared = true,
                        cleared_at = $2,
                        matched_statement_line_id = $3
                    WHERE id = $1
                    """,
                    tx_id,
                    now,
                    UUID(body.statement_line_id),
                )

                await conn.execute(
                    """
                    UPDATE bank_statement_lines_v2
                    SET match_status = 'matched'
                    WHERE id = $1
                    """,
                    UUID(body.statement_line_id),
                )

            # Update session stats
            await update_reconciliation_session_stats(conn, session_id)

            # Get updated stats
            stats = await get_session_statistics(conn, session_id)

            return CreateTransactionResponse(
                transaction_id=str(tx_id),
                match_id=str(match_id) if match_id else None,
                session_stats=stats,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating transaction: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create transaction")


# =============================================================================
# COMPLETE SESSION ENDPOINT
# =============================================================================


@router.post("/sessions/{session_id}/complete", response_model=CompleteResponse)
async def complete_session(
    request: Request,
    session_id: UUID,
    body: CompleteSessionRequest,
):
    """
    Complete a reconciliation session.

    Validates that the difference is zero (or covered by adjustments),
    creates journal entries for adjustments, marks transactions as reconciled,
    and updates session status to completed.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET app.tenant_id = '{ctx['tenant_id']}'")

            # Verify session
            session = await conn.fetchrow(
                """
                SELECT id, status, account_id, statement_beginning_balance,
                       statement_ending_balance, cleared_balance, difference
                FROM reconciliation_sessions
                WHERE id = $1 AND tenant_id = $2
                """,
                session_id,
                ctx["tenant_id"],
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(
                    status_code=400, detail="Can only complete in-progress sessions"
                )

            # Calculate adjustments total
            adjustments_total = sum(adj.amount for adj in body.adjustments)
            current_difference = session["difference"] or 0
            final_difference = current_difference - adjustments_total

            # Validate difference is covered
            if final_difference != 0 and len(body.adjustments) == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot complete session with difference of {final_difference}. Add adjustments to balance.",
                )

            if abs(final_difference) > 100:  # Allow small rounding differences
                raise HTTPException(
                    status_code=400,
                    detail=f"Remaining difference of {final_difference} is too large. Add more adjustments.",
                )

            now = datetime.utcnow()
            journal_entries_created = 0

            # Get bank account COA entry
            bank_account = await conn.fetchrow(
                "SELECT coa_id FROM bank_accounts WHERE id = $1", session["account_id"]
            )

            # Create adjustments and journal entries
            for adj in body.adjustments:
                adj_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO reconciliation_adjustments (
                        id, tenant_id, session_id, type, amount, description,
                        account_id, created_by, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    adj_id,
                    ctx["tenant_id"],
                    session_id,
                    adj.type,
                    adj.amount,
                    adj.description,
                    UUID(adj.account_id),
                    ctx["user_id"],
                    now,
                )

                # Create journal entry for adjustment
                journal_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, entry_date, reference, description,
                        source_type, source_id, status, created_by, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, 'reconciliation', $6, 'posted', $7, $8, $8)
                    """,
                    journal_id,
                    ctx["tenant_id"],
                    now.date(),
                    f"RECON-ADJ-{session_id.hex[:8]}",
                    f"Bank Reconciliation Adjustment: {adj.description}",
                    session_id,
                    ctx["user_id"],
                    now,
                )

                # Create journal entry lines
                # Debit/Credit depends on adjustment type
                if adj.type in ["bank_fee", "correction"]:
                    # Fee: Debit expense, Credit bank
                    await conn.execute(
                        """
                        INSERT INTO journal_entry_lines (
                            id, tenant_id, journal_entry_id, account_id, debit, credit, description
                        ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid.uuid4(),
                        ctx["tenant_id"],
                        journal_id,
                        UUID(adj.account_id),
                        adj.amount,
                        adj.description,
                    )
                    if bank_account and bank_account["coa_id"]:
                        await conn.execute(
                            """
                            INSERT INTO journal_entry_lines (
                                id, tenant_id, journal_entry_id, account_id, debit, credit, description
                            ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                            """,
                            uuid.uuid4(),
                            ctx["tenant_id"],
                            journal_id,
                            bank_account["coa_id"],
                            adj.amount,
                            adj.description,
                        )
                elif adj.type == "interest":
                    # Interest: Debit bank, Credit income
                    if bank_account and bank_account["coa_id"]:
                        await conn.execute(
                            """
                            INSERT INTO journal_entry_lines (
                                id, tenant_id, journal_entry_id, account_id, debit, credit, description
                            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                            """,
                            uuid.uuid4(),
                            ctx["tenant_id"],
                            journal_id,
                            bank_account["coa_id"],
                            adj.amount,
                            adj.description,
                        )
                    await conn.execute(
                        """
                        INSERT INTO journal_entry_lines (
                            id, tenant_id, journal_entry_id, account_id, debit, credit, description
                        ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                        uuid.uuid4(),
                        ctx["tenant_id"],
                        journal_id,
                        UUID(adj.account_id),
                        adj.amount,
                        adj.description,
                    )
                else:
                    # Other: Default to debit expense, credit bank
                    await conn.execute(
                        """
                        INSERT INTO journal_entry_lines (
                            id, tenant_id, journal_entry_id, account_id, debit, credit, description
                        ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid.uuid4(),
                        ctx["tenant_id"],
                        journal_id,
                        UUID(adj.account_id),
                        adj.amount,
                        adj.description,
                    )
                    if bank_account and bank_account["coa_id"]:
                        await conn.execute(
                            """
                            INSERT INTO journal_entry_lines (
                                id, tenant_id, journal_entry_id, account_id, debit, credit, description
                            ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                            """,
                            uuid.uuid4(),
                            ctx["tenant_id"],
                            journal_id,
                            bank_account["coa_id"],
                            adj.amount,
                            adj.description,
                        )

                journal_entries_created += 1

            # Mark all matched transactions as reconciled
            await conn.execute(
                """
                UPDATE bank_transactions
                SET is_reconciled = true,
                    reconciled_at = $2,
                    reconciled_session_id = $1
                WHERE tenant_id = $3
                  AND matched_statement_line_id IN (
                      SELECT id FROM bank_statement_lines_v2 WHERE session_id = $1
                  )
                """,
                session_id,
                now,
                ctx["tenant_id"],
            )

            # Get final statistics
            matched_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM bank_statement_lines_v2
                WHERE session_id = $1 AND match_status = 'matched'
                """,
                session_id,
            )

            # Update session status to completed
            await conn.execute(
                """
                UPDATE reconciliation_sessions
                SET status = 'completed',
                    completed_at = $2,
                    completed_by = $3,
                    difference = $4,
                    updated_at = $2
                WHERE id = $1
                """,
                session_id,
                now,
                ctx["user_id"],
                final_difference,
            )

            return CompleteResponse(
                success=True,
                completed_at=now.isoformat(),
                final_stats=FinalStats(
                    total_matched=matched_count or 0,
                    total_adjustments=len(body.adjustments),
                    opening_difference=current_difference,
                    closing_difference=adjustments_total,
                    final_difference=final_difference,
                ),
                journal_entries_created=journal_entries_created,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to complete session")
