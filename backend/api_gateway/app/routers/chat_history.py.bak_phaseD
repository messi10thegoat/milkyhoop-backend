"""
Chat History REST API — v2.0 Architecture

Endpoints:
  GET  /api/v3/chat/sessions                    → List sessions (cursor-paginated, ETag)
  GET  /api/v3/chat/sessions/{id}/messages      → Messages in session (cursor-paginated)
  PATCH /api/v3/chat/sessions/{id}              → Archive/update session
  GET  /api/v3/chat/history                     → Legacy flat list (backward compat)

Design decisions:
  - Cursor pagination (O(1)) instead of OFFSET (O(n))
  - No COUNT(*) — uses limit+1 trick for has_more
  - ETag for cache validation (304 Not Modified)
  - Direct asyncpg queries (same pool as v3 agent)
  - Tenant isolation via JWT context (Iron Law 24)
"""
import hashlib
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Auth Helper ───────────────────────────────────────────────────────────────

def _get_user_context(request: Request) -> dict:
    """Extract tenant_id and user_id from JWT (set by AuthMiddleware)."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user_id or ""}


# ─── DB Pool ──────────────────────────────────────────────────────────────────

async def _get_pool():
    from ..services.unified_agent.db_utils import get_session_db_pool
    return await get_session_db_pool()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _generate_title(preview: str) -> str:
    """Auto-generate session title from first message preview."""
    if not preview:
        return "Percakapan baru"
    title = preview[:60]
    if len(preview) > 60:
        title += "..."
    return title


def _serialize_messages(rows, limit: int) -> dict:
    """Convert DB rows to API response with has_more via limit+1 trick."""
    has_more = len(rows) > limit
    messages = rows[:limit]
    return {
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "message_type": m["message_type"],
                "created_at": m["created_at"].isoformat(),
                "session_id": str(m["session_id"]) if "session_id" in m.keys() else None,
            }
            for m in messages
        ],
        "has_more": has_more,
        "next_cursor": messages[-1]["created_at"].isoformat() if has_more and messages else None,
    }


# =============================================================================
# GET /sessions — List chat sessions
# =============================================================================

@router.get("/sessions")
async def list_sessions(
    request: Request,
    response: Response,
    before: Optional[str] = Query(None, description="Cursor: ISO datetime"),
    limit: int = Query(20, ge=1, le=50),
):
    """
    List chat sessions for the authenticated user.
    Cursor-paginated by updated_at DESC. ETag-cached.
    """
    ctx = _get_user_context(request)
    pool = await _get_pool()
    fetch_limit = limit + 1  # limit+1 trick for has_more

    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(400, "Invalid 'before' cursor format")
        rows = await pool.fetch(
            """
            SELECT
                cs.id, cs.summary, cs.status, cs.created_at, cs.updated_at,
                last_msg.content AS last_message_preview,
                last_msg.created_at AS last_message_at,
                msg_count.cnt AS message_count
            FROM chat_sessions cs
            LEFT JOIN LATERAL (
                SELECT content, created_at
                FROM chat_messages
                WHERE session_id = cs.id AND role = 'assistant'
                ORDER BY created_at DESC LIMIT 1
            ) last_msg ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS cnt
                FROM chat_messages
                WHERE session_id = cs.id AND role IN ('user', 'assistant')
            ) msg_count ON true
            WHERE cs.tenant_id = $1
              AND cs.user_id = $2::uuid
              AND cs.status = 'active'
              AND cs.updated_at < $3
            ORDER BY cs.updated_at DESC
            LIMIT $4
            """,
            ctx["tenant_id"], ctx["user_id"], before_dt, fetch_limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT
                cs.id, cs.summary, cs.status, cs.created_at, cs.updated_at,
                last_msg.content AS last_message_preview,
                last_msg.created_at AS last_message_at,
                msg_count.cnt AS message_count
            FROM chat_sessions cs
            LEFT JOIN LATERAL (
                SELECT content, created_at
                FROM chat_messages
                WHERE session_id = cs.id AND role = 'assistant'
                ORDER BY created_at DESC LIMIT 1
            ) last_msg ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS cnt
                FROM chat_messages
                WHERE session_id = cs.id AND role IN ('user', 'assistant')
            ) msg_count ON true
            WHERE cs.tenant_id = $1
              AND cs.user_id = $2::uuid
              AND cs.status = 'active'
            ORDER BY cs.updated_at DESC
            LIMIT $3
            """,
            ctx["tenant_id"], ctx["user_id"], fetch_limit,
        )

    has_more = len(rows) > limit
    sessions = rows[:limit]

    result_sessions = []
    for r in sessions:
        preview = r["last_message_preview"] or ""
        if len(preview) > 120:
            preview = preview[:120] + "..."
        result_sessions.append({
            "id": str(r["id"]),
            "title": r["summary"] or _generate_title(preview),
            "status": r["status"],
            "message_count": r["message_count"] or 0,
            "last_message_preview": preview,
            "last_message_at": r["last_message_at"].isoformat() if r["last_message_at"] else None,
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        })

    result = {
        "sessions": result_sessions,
        "has_more": has_more,
        "next_cursor": sessions[-1]["updated_at"].isoformat() if has_more and sessions else None,
    }

    # ETag
    etag_source = "|".join(f"{s['id']}:{s['updated_at']}" for s in result_sessions)
    etag = hashlib.md5(etag_source.encode()).hexdigest()
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"

    if_none_match = request.headers.get("if-none-match", "").strip('"')
    if if_none_match == etag:
        return Response(status_code=304)

    return {"data": result}


# =============================================================================
# GET /sessions/{session_id}/messages — Messages in a session
# =============================================================================

@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    request: Request,
    session_id: str,
    before: Optional[str] = Query(None, description="Cursor: ISO datetime"),
    limit: int = Query(30, ge=1, le=100),
):
    """
    Get messages for a specific chat session. Cursor-paginated.
    Returns 404 if session not found or doesn't belong to user (don't reveal existence).
    """
    ctx = _get_user_context(request)
    pool = await _get_pool()

    # Verify session ownership
    session = await pool.fetchrow(
        """
        SELECT id FROM chat_sessions
        WHERE id = $1::uuid AND tenant_id = $2 AND user_id = $3::uuid
        """,
        session_id, ctx["tenant_id"], ctx["user_id"],
    )
    if not session:
        raise HTTPException(404, "Session not found")

    fetch_limit = limit + 1

    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(400, "Invalid 'before' cursor format")
        rows = await pool.fetch(
            """
            SELECT id, role, content, message_type, created_at, session_id
            FROM chat_messages
            WHERE session_id = $1::uuid
              AND role IN ('user', 'assistant')
              AND created_at < $2
            ORDER BY created_at DESC
            LIMIT $3
            """,
            session_id, before_dt, fetch_limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, role, content, message_type, created_at, session_id
            FROM chat_messages
            WHERE session_id = $1::uuid
              AND role IN ('user', 'assistant')
            ORDER BY created_at DESC
            LIMIT $2
            """,
            session_id, fetch_limit,
        )

    return {"data": _serialize_messages(rows, limit)}


# =============================================================================
# PATCH /sessions/{session_id} — Archive session
# =============================================================================

@router.patch("/sessions/{session_id}")
async def update_session(request: Request, session_id: str):
    """Archive or update a chat session."""
    ctx = _get_user_context(request)
    body = await request.json()
    pool = await _get_pool()

    status = body.get("status")
    if status not in ("active", "archived"):
        raise HTTPException(400, "Invalid status. Must be 'active' or 'archived'")

    result = await pool.execute(
        """
        UPDATE chat_sessions
        SET status = $1, updated_at = NOW()
        WHERE id = $2::uuid AND tenant_id = $3 AND user_id = $4::uuid
        """,
        status, session_id, ctx["tenant_id"], ctx["user_id"],
    )

    if result == "UPDATE 0":
        raise HTTPException(404, "Session not found")

    return {"data": {"id": session_id, "status": status}}


# =============================================================================
# GET /history — Legacy flat message list (backward compat)
# =============================================================================

@router.get("/history")
async def get_history_legacy(
    request: Request,
    before: Optional[str] = Query(None, description="Cursor: ISO datetime"),
    limit: int = Query(30, ge=1, le=100),
):
    """
    DEPRECATED: Use GET /sessions + GET /sessions/:id/messages instead.
    Returns flat message list across all sessions for backward compatibility.
    """
    ctx = _get_user_context(request)
    pool = await _get_pool()
    fetch_limit = limit + 1

    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            raise HTTPException(400, "Invalid 'before' cursor format")
        rows = await pool.fetch(
            """
            SELECT cm.id, cm.role, cm.content, cm.message_type,
                   cm.created_at, cm.session_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cs.id = cm.session_id
            WHERE cs.tenant_id = $1
              AND cs.user_id = $2::uuid
              AND cm.role IN ('user', 'assistant')
              AND cm.created_at < $3
            ORDER BY cm.created_at DESC
            LIMIT $4
            """,
            ctx["tenant_id"], ctx["user_id"], before_dt, fetch_limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT cm.id, cm.role, cm.content, cm.message_type,
                   cm.created_at, cm.session_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cs.id = cm.session_id
            WHERE cs.tenant_id = $1
              AND cs.user_id = $2::uuid
              AND cm.role IN ('user', 'assistant')
            ORDER BY cm.created_at DESC
            LIMIT $3
            """,
            ctx["tenant_id"], ctx["user_id"], fetch_limit,
        )

    return {"data": _serialize_messages(rows, limit)}
