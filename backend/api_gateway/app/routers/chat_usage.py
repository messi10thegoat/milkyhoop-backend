"""
Token usage analytics endpoint.
Aggregates from chat_messages.token_count (populated by session_orchestrator).
"""
import logging
from datetime import date, timedelta
from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from ..services.unified_agent.db_utils import get_session_db_pool

logger = logging.getLogger("chat_usage")
router = APIRouter()


def _get_user_context(request: Request):
    """Extract tenant_id and user_id from auth middleware."""
    user = getattr(request.state, "user", None)
    if not user:
        return None, None
    return user.get("tenant_id"), user.get("id")


@router.get("/usage")
async def get_token_usage(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
):
    """Get aggregated token usage for the current tenant."""
    tenant_id, user_id = _get_user_context(request)
    if not tenant_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    pool = await get_session_db_pool()

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    # Daily breakdown
    daily_rows = await pool.fetch("""
        SELECT DATE(created_at) as day,
               COALESCE(SUM(token_count), 0)::int as tokens,
               COUNT(*)::int as messages
        FROM chat_messages
        WHERE tenant_id = $1
          AND created_at >= $2::date
          AND role = 'assistant'
          AND token_count IS NOT NULL
        GROUP BY DATE(created_at)
        ORDER BY day DESC
    """, tenant_id, start_date)

    # Per-session breakdown
    session_rows = await pool.fetch("""
        SELECT session_id::text,
               MIN(created_at) as started_at,
               MAX(created_at) as last_message_at,
               COALESCE(SUM(token_count), 0)::int as tokens,
               COUNT(*)::int as messages
        FROM chat_messages
        WHERE tenant_id = $1
          AND created_at >= $2::date
          AND role = 'assistant'
          AND token_count IS NOT NULL
        GROUP BY session_id
        ORDER BY MAX(created_at) DESC
    """, tenant_id, start_date)

    total_tokens = sum(r["tokens"] for r in daily_rows)
    total_messages = sum(r["messages"] for r in daily_rows)

    return {
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "days": days,
        },
        "total_tokens": total_tokens,
        "total_messages": total_messages,
        "total_sessions": len(session_rows),
        "daily": [
            {
                "date": r["day"].isoformat(),
                "tokens": r["tokens"],
                "messages": r["messages"],
            }
            for r in daily_rows
        ],
        "sessions": [
            {
                "session_id": r["session_id"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "last_message_at": r["last_message_at"].isoformat() if r["last_message_at"] else None,
                "tokens": r["tokens"],
                "messages": r["messages"],
            }
            for r in session_rows
        ],
    }
