"""Optimistic concurrency (If-Match / lost-update guard).

Opt-in per request: if the client sends an `If-Match` header, it MUST equal the row's
current `updated_at` (ISO-8601). Mismatch -> 412 Precondition Failed with the current
value so the client can refresh and retry. No header -> no-op (backward compatible;
realtime doc_changed already prompts a refetch). Pairs with the realtime layer: a second
tab that edited the same draft bumps `updated_at`, so the stale writer gets 412 instead
of silently clobbering.

Anchor = `updated_at`. Tables without it (e.g. bank_transactions, user_tenant_roles)
cannot use this guard until they gain an `updated_at` column — do not invent a version.
"""
from fastapi import HTTPException, Request


def _norm(v) -> str:
    if v is None:
        return ""
    s = v.isoformat() if hasattr(v, "isoformat") else str(v)
    return s.strip().strip('"').strip("'")


def assert_if_match(request: Request, current_updated_at) -> None:
    """Raise 412 if the request's If-Match header disagrees with current_updated_at."""
    hdr = request.headers.get("if-match")
    if not hdr:
        return
    if _norm(hdr) != _norm(current_updated_at):
        raise HTTPException(
            status_code=412,
            detail={
                "code": "STALE_WRITE",
                "message": "Data ini sudah diubah pihak lain. Muat ulang lalu coba lagi.",
                "current_updated_at": _norm(current_updated_at),
            },
        )


async def assert_if_match_row(request, table, row_id) -> None:
    """Opt-in optimistic guard for a PATCH/PUT handler: when the client sends If-Match,
    fetch the row's current updated_at and compare. Self-contained (resolves tenant +
    pool itself) so a handler adds exactly one call. Row missing / no tenant -> no-op
    (the handler own 404/401 still applies). `table` is a hardcoded literal per call."""
    if not request.headers.get("if-match"):
        return
    user = getattr(request.state, "user", None)
    tenant_id = user.get("tenant_id") if user else None
    if not tenant_id:
        return
    from .db_pool import get_db_pool
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        cur = await conn.fetchval(
            f"SELECT updated_at FROM {table} WHERE id = $1::uuid AND tenant_id = $2",
            str(row_id), tenant_id,
        )
    if cur is not None:
        assert_if_match(request, cur)
