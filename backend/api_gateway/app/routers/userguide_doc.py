"""
Router: Userguide Doc Fetch (Phase 2B-2)

Single GET endpoint that resolves a `docs:doc_id` citation (emitted by
`search_userguide` tool) to the full markdown content of the source document.

Endpoint:
  GET /api/v3/chat/userguide-doc/{doc_id}

doc_id format: `<module>.<type>.<slug>` (e.g. `faktur-penjualan.how-to.bikin-faktur-baru`,
`_meta.konsep.lifecycle-dokumen`).

Strategy:
  - Aggregate chunks of the doc from `userguide_chunks` table (already indexed,
    container-mounted file paths are not guaranteed). Sort by `chunk_index`,
    strip per-chunk header preservation prefix `[Doc: ...]\n[Section: ...]\n\n`.
  - Permission re-check via PolicyEngine (mirrors `search_userguide`):
    if doc has `required_module` + `required_action`, user must satisfy unless
    OWNER. Fail-closed on PolicyEngine error.
  - In-process LRU cache for assembled markdown (size 128). Invalidated per
    container restart (acceptable — re-index ships fresh chunks anyway).

Response shape (200):
  {
    doc_id, doc_title, doc_path, module, type,
    content_markdown, required_module, required_action, last_updated
  }

403 / 404 use FastAPI HTTPException with `detail` so the frontend can
surface a friendly fallback.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..services.unified_agent.db_utils import get_session_db_pool

logger = logging.getLogger("userguide_doc")
logger.setLevel(logging.INFO)

router = APIRouter()

# doc_id sanity: lowercase letters, digits, dot, dash, underscore only.
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]{3,255}$")

# Strip header preservation block emitted by chunker:
#   [Doc: <title>]\n[Section: <heading>]\n\n
_HEADER_PREFIX_RE = re.compile(
    r"^\s*\[Doc:[^\]]*\]\s*\n(?:\[Section:[^\]]*\]\s*\n)?\s*\n?",
    re.IGNORECASE,
)

# Strip overlap context line emitted by chunker for non-first chunks:
#   [Konteks sebelumnya] <overlap_text>\n\n
_OVERLAP_PREFIX_RE = re.compile(
    r"^\s*\[Konteks sebelumnya\][^\n]*\n+",
    re.IGNORECASE | re.MULTILINE,
)

# Defensive: any leftover [Doc:] / [Section:] lines that may appear mid-content.
_STRAY_HEADER_LINE_RE = re.compile(
    r"^\s*\[(?:Doc|Section):[^\]]*\]\s*\n",
    re.IGNORECASE | re.MULTILINE,
)


def _get_user_context(request: Request) -> dict:
    """Extract user context from request.state (set by AuthMiddleware)."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or ""
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user_id}


@lru_cache(maxsize=128)
def _strip_chunk_header(content: str) -> str:
    """Remove header preservation prefix and overlap context from a chunk."""
    out = _HEADER_PREFIX_RE.sub("", content, count=1)
    out = _OVERLAP_PREFIX_RE.sub("", out)
    out = _STRAY_HEADER_LINE_RE.sub("", out)
    return out


async def _check_user_can_access(
    user_id: str,
    tenant_id: str,
    required_module: str | None,
    required_action: str | None,
) -> tuple[bool, bool]:
    """
    Returns (allowed, is_owner).

    - If required_module is NULL → allowed (informational doc).
    - OWNER → always allowed.
    - Otherwise check effective permissions match module + action.
    - Fail-closed on PolicyEngine error (allowed=False).
    """
    if not required_module:
        return True, False

    try:
        from ..services.policy_engine_client import get_policy_engine  # noqa: E402

        pe = get_policy_engine()
        eff = await pe.get_effective_permissions(user_id, tenant_id)
        if eff.get("role_code") == "OWNER":
            return True, True

        eff_perms = eff.get("effective_permissions", {}) or {}
        # Mirror userguide_search.USERGUIDE_TO_DB_MODULE mapping.
        from ..services.userguide_search import USERGUIDE_TO_DB_MODULE  # noqa: E402

        db_module = USERGUIDE_TO_DB_MODULE.get(required_module, required_module)
        info = eff_perms.get(db_module) or {}
        actions = list((info or {}).get("actions") or [])
        if required_action and required_action in actions:
            return True, False
        return False, False
    except Exception:
        logger.warning(
            "[userguide-doc] PolicyEngine resolve failed for user=%s tenant=%s",
            user_id,
            tenant_id,
            exc_info=True,
        )
        return False, False


@router.get("/userguide-doc/{doc_id}")
async def get_userguide_doc(request: Request, doc_id: str) -> dict[str, Any]:
    """
    Fetch full assembled markdown for a userguide document by `doc_id`.

    Used by the frontend citation drawer. Re-checks permissions even though
    `search_userguide` already filtered — defense in depth (a stale message
    in chat history may reference a doc the user has since lost access to).
    """
    if not _DOC_ID_RE.match(doc_id):
        raise HTTPException(status_code=400, detail="Invalid doc_id format")

    ctx = _get_user_context(request)

    try:
        pool = await get_session_db_pool()
    except Exception as e:
        logger.exception("[userguide-doc] pool init failed")
        raise HTTPException(status_code=500, detail=f"DB pool: {str(e)[:120]}")

    rows = await pool.fetch(
        """
        SELECT chunk_index, content, doc_title, doc_path, module, type,
               required_module, required_action, last_updated
          FROM userguide_chunks
         WHERE doc_id = $1
         ORDER BY chunk_index ASC
        """,
        doc_id,
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{doc_id}' tidak ditemukan di indeks userguide.",
        )

    head = rows[0]
    required_module = head["required_module"]
    required_action = head["required_action"]

    allowed, is_owner = await _check_user_can_access(
        ctx["user_id"], ctx["tenant_id"], required_module, required_action
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Akses ditolak. Dokumen ini butuh izin "
                f"{required_module}.{required_action}. Hubungi owner untuk minta akses."
            ),
        )

    # Assemble: strip per-chunk header prefix + overlap context, join with blank-line separator.
    parts: list[str] = []
    for r in rows:
        body = _strip_chunk_header(r["content"] or "").rstrip()
        if body:
            parts.append(body)
    aggregated = "\n\n".join(parts).strip()

    # Dedupe near-duplicate consecutive paragraphs across chunk boundaries
    # (chunker overlap of ~50 tokens often duplicates paragraphs at boundaries).
    paragraphs = re.split(r"\n\n+", aggregated)
    deduped: list[str] = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if deduped and deduped[-1] == p:
            continue
        deduped.append(p)
    content_markdown = "\n\n".join(deduped)

    last_updated = head["last_updated"]
    return {
        "doc_id": doc_id,
        "doc_title": head["doc_title"],
        "doc_path": head["doc_path"],
        "module": head["module"],
        "type": head["type"],
        "content_markdown": content_markdown,
        "required_module": required_module,
        "required_action": required_action,
        "last_updated": last_updated.isoformat() if last_updated else None,
        "is_owner": is_owner,
        "chunks_assembled": len(rows),
    }
