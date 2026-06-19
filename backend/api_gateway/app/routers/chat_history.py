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
import json
import logging
import uuid
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


def _serialize_messages(rows, limit: int, att_map: dict = None) -> dict:
    """Convert DB rows to API response with has_more via limit+1 trick."""
    has_more = len(rows) > limit
    messages = rows[:limit]
    att_map = att_map or {}
    serialized = []
    for m in messages:
        msg_id = str(m["id"])
        raw_meta = m["metadata"] if "metadata" in m.keys() else None
        if raw_meta is not None:
            if isinstance(raw_meta, str):
                try:
                    meta = json.loads(raw_meta)
                except Exception:
                    meta = None
            else:
                meta = dict(raw_meta)
        else:
            meta = None
        serialized.append({
            "id": msg_id,
            "role": m["role"],
            "content": m["content"],
            "message_type": m["message_type"],
            "created_at": m["created_at"].isoformat(),
            "session_id": str(m["session_id"]) if "session_id" in m.keys() else None,
            "metadata": meta,
            "attachments": att_map.get(msg_id, []),
        })
    return {
        "messages": serialized,
        "has_more": has_more,
        "next_cursor": messages[-1]["created_at"].isoformat() if has_more and messages else None,
    }



async def _enrich_messages(pool, rows, limit: int) -> dict:
    """Fetch attachments + lazy-sync PENDING status, then serialize."""
    import uuid as _uuid_mod
    trimmed = rows[:limit]
    msg_ids = [m["id"] for m in trimmed]

    # Fetch attachments for all messages in one query
    att_map = {}
    if msg_ids:
        att_rows = await pool.fetch(
            """
            SELECT message_id, file_name, content_type, file_size, storage_key, thumbnail_url
            FROM chat_attachments
            WHERE message_id = ANY($1::uuid[])
            ORDER BY created_at ASC
            """,
            msg_ids,
        )
        for a in att_rows:
            key = str(a["message_id"])
            att_map.setdefault(key, []).append({
                "file_name": a["file_name"],
                "content_type": a["content_type"],
                "file_size": a["file_size"],
                "storage_key": a["storage_key"],
                "url": f"/api/v3/chat/files/{a['storage_key']}",
                "thumbnail_url": a["thumbnail_url"],
            })

    # Lazy expiry: sync stale PENDING status from pending_actions
    stale_ids = []
    for m in trimmed:
        raw_meta = m["metadata"] if "metadata" in m.keys() else None
        if raw_meta is not None:
            if isinstance(raw_meta, str):
                try:
                    meta = json.loads(raw_meta)
                except Exception:
                    meta = None
            else:
                meta = dict(raw_meta)
            if meta and meta.get("status") == "PENDING" and meta.get("pending_action_id"):
                stale_ids.append((str(m["id"]), meta["pending_action_id"]))

    if stale_ids:
        pa_ids = [s[1] for s in stale_ids]
        try:
            pa_rows = await pool.fetch(
                """
                SELECT id::text, status FROM pending_actions
                WHERE id = ANY($1::uuid[])
                """,
                [_uuid_mod.UUID(p) for p in pa_ids],
            )
            pa_status = {str(r["id"]): r["status"] for r in pa_rows}
            for msg_id, pa_id in stale_ids:
                actual = pa_status.get(pa_id)
                if actual and actual != "PENDING":
                    try:
                        await pool.execute(
                            """
                            UPDATE chat_messages
                            SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{status}', $1::jsonb)
                            WHERE id = $2::uuid
                            """,
                            f'"{actual}"',
                            _uuid_mod.UUID(msg_id),
                        )
                    except Exception:
                        pass  # Non-fatal
        except Exception as _e:
            logger.warning("[History] Lazy expiry sync failed: %s", _e)

    return _serialize_messages(rows, limit, att_map=att_map)


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
                cs.id, cs.title, cs.summary, cs.status, cs.created_at, cs.updated_at,
                last_msg.content AS last_message_preview,
                last_msg.created_at AS last_message_at,
                first_user.content AS first_user_message,
                msg_count.cnt AS message_count
            FROM chat_sessions cs
            LEFT JOIN LATERAL (
                SELECT content, created_at
                FROM chat_messages
                WHERE session_id = cs.id AND role = 'assistant'
                ORDER BY created_at DESC LIMIT 1
            ) last_msg ON true
            LEFT JOIN LATERAL (
                SELECT content
                FROM chat_messages
                WHERE session_id = cs.id AND role = 'user'
                ORDER BY created_at ASC LIMIT 1
            ) first_user ON true
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
                cs.id, cs.title, cs.summary, cs.status, cs.created_at, cs.updated_at,
                last_msg.content AS last_message_preview,
                last_msg.created_at AS last_message_at,
                first_user.content AS first_user_message,
                msg_count.cnt AS message_count
            FROM chat_sessions cs
            LEFT JOIN LATERAL (
                SELECT content, created_at
                FROM chat_messages
                WHERE session_id = cs.id AND role = 'assistant'
                ORDER BY created_at DESC LIMIT 1
            ) last_msg ON true
            LEFT JOIN LATERAL (
                SELECT content
                FROM chat_messages
                WHERE session_id = cs.id AND role = 'user'
                ORDER BY created_at ASC LIMIT 1
            ) first_user ON true
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
            # FIX_CHAT_TITLE_LIST — prefer the set-once AI title, then legacy summary, then derived
            "title": (r["title"].strip() if r["title"] and r["title"].strip() else None)
                     or r["summary"]
                     or _generate_title(r["first_user_message"] or preview)
                     or "Percakapan",
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
            SELECT id, role, content, message_type, created_at, session_id, metadata
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
            SELECT id, role, content, message_type, created_at, session_id, metadata
            FROM chat_messages
            WHERE session_id = $1::uuid
              AND role IN ('user', 'assistant')
            ORDER BY created_at DESC
            LIMIT $2
            """,
            session_id, fetch_limit,
        )

    return {"data": await _enrich_messages(pool, rows, limit)}


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
                   cm.created_at, cm.session_id, cm.metadata
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
                   cm.created_at, cm.session_id, cm.metadata
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

    return {"data": await _enrich_messages(pool, rows, limit)}
