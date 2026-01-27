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
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date, datetime
import uuid

from ..schemas.bank_reconciliation import (
    CreateSessionRequest,
    AccountsListResponse,
    SessionsListResponse,
    SessionCreateResponse,
    SessionDetailResponse,
    CancelResponse,
    HistoryResponse,
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

    return {
        "tenant_id": tenant_id,
        "user_id": UUID(user_id) if user_id else None
    }


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
                ctx["tenant_id"]
            )

            accounts = []
            today = date.today()

            for row in rows:
                last_recon = row["last_reconciled_date"]
                days_since = (today - last_recon).days if last_recon else None
                needs_recon = days_since is None or days_since > 7

                if needs_reconciliation is not None and needs_recon != needs_reconciliation:
                    continue

                accounts.append({
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
                    "active_session_id": str(row["active_session_id"]) if row["active_session_id"] else None,
                    "active_session_status": row["active_session_status"],
                })

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
    status: Optional[Literal["not_started", "in_progress", "completed", "cancelled"]] = Query(None),
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
                *params
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
                *params
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
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
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
                "hasMore": (offset + limit) < total
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
                UUID(body.account_id), ctx["tenant_id"]
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
                UUID(body.account_id), ctx["tenant_id"]
            )

            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="An in-progress reconciliation session already exists for this account"
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
                session_id, ctx["tenant_id"], UUID(body.account_id),
                body.statement_date, body.statement_start_date, body.statement_end_date,
                body.statement_beginning_balance, body.statement_ending_balance,
                "in_progress", ctx["user_id"], now
            )

            return {
                "id": str(session_id),
                "status": "in_progress",
                "created_at": now.isoformat()
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
                session_id, ctx["tenant_id"]
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
                session_id
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
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                }
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
                session_id, ctx["tenant_id"]
            )

            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

            if session["status"] != "in_progress":
                raise HTTPException(status_code=400, detail="Can only cancel in-progress sessions")

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
                ctx["tenant_id"], session_id
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
                session_id
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
                account_id, ctx["tenant_id"]
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
                account_id, ctx["tenant_id"], limit, offset
            )

            history = [
                {
                    "id": str(row["id"]),
                    "statement_date": str(row["statement_date"]),
                    "statement_ending_balance": row["statement_ending_balance"],
                    "matched_count": row["matched_count"] or 0,
                    "adjustments_count": row["adjustments_count"] or 0,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                    "completed_by": row["completed_by"] or "Unknown",
                }
                for row in rows
            ]

            return {
                "data": history,
                "total": total,
                "hasMore": (offset + limit) < total
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get history")
