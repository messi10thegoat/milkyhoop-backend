"""SSE realtime — GET /api/events/stream. Tahap 1.

Auth: middleware biasa memvalidasi Bearer di request.state.user (fetch-stream + header,
token 7-hari TAK masuk URL/log). Per-event autz: tenant sama + modul READ (D). Pencabutan:
recheck user+membership tiap 60s & saat membership_changed NOTIFY & saat token exp (E).
Payload minimal {tbl,id,op}; klien ambil ulang lewat API biasa.
"""
import asyncio
import base64
import json
import logging
import time
from typing import Optional, Set

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..services.realtime import hub, ClientConn
from ..services.policy_engine_client import get_policy_engine

router = APIRouter()
logger = logging.getLogger(__name__)

HEARTBEAT_S = 20
RECHECK_S = 60


def _decode_exp(request: Request) -> Optional[float]:
    """Ambil klaim exp dari Bearer (tanpa verifikasi tanda tangan — hanya utk jadwal tutup)."""
    try:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        seg = auth.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg))
        exp = claims.get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


async def _allowed_modules(user_id: str, tenant_id: str) -> Set[str]:
    pe = get_policy_engine()
    try:
        eff = await pe.get_effective_permissions(user_id, tenant_id)
        perms = eff.get("effective_permissions", {}) or {}
        return {m for m, v in perms.items() if "R" in ((v or {}).get("actions") or [])}
    except Exception as e:
        logger.error(f"realtime _allowed_modules error: {e}")
        return set()  # fail-closed: tak ada modul → tak ada event


async def _revocation_code(user_id: str, tenant_id: str, role: str) -> Optional[str]:
    """None = masih sah; else kode pencabutan (USER_NOT_FOUND / MEMBERSHIP_INACTIVE)."""
    pe = get_policy_engine()
    try:
        async with pe.pool.acquire() as conn:
            exists = await conn.fetchval('SELECT 1 FROM "User" WHERE id = $1', str(user_id))
        if not exists:
            return "USER_NOT_FOUND"
    except Exception as e:
        logger.warning(f"realtime user-exist check error (fail-open): {e}")
        # gangguan DB sesaat TAK BOLEH memutus semua; biarkan lewat (sama filosofi auth_mw)
    try:
        ctx = await pe.get_user_context(user_id, tenant_id, role)
        if getattr(ctx, "membership_active", True) is False:
            return "MEMBERSHIP_INACTIVE"
        has_role = bool(getattr(ctx, "business_role_id", None))
        if not has_role and role not in ("OWNER", "ADMIN"):
            return "MEMBERSHIP_INACTIVE"
    except Exception as e:
        logger.warning(f"realtime membership check error (fail-open): {e}")
    return None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@router.get("/stream")
async def stream(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return StreamingResponse(
            iter([_sse({"type": "revoked", "code": "UNAUTHENTICATED"})]),
            media_type="text/event-stream",
            status_code=401,
        )
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")
    role = user.get("role", "USER")
    exp = _decode_exp(request)

    allowed = await _allowed_modules(user_id, tenant_id)
    conn = ClientConn(tenant_id, user_id, allowed, exp)
    hub.register(conn)

    async def gen():
        last_recheck = time.time()
        try:
            yield _sse({"type": "hello"})  # klien memicu resync awal
            while True:
                # tutup saat token kedaluwarsa (E / gerbang 8)
                if conn.exp and time.time() >= conn.exp:
                    yield _sse({"type": "revoked", "code": "TOKEN_EXPIRED"})
                    return
                try:
                    msg = await asyncio.wait_for(conn.queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # heartbeat komentar SSE (< CF 100s, nginx 300s)
                    msg = None

                if msg is not None:
                    if msg.get("type") == "recheck":
                        code = await _revocation_code(user_id, tenant_id, role)
                        if code:
                            yield _sse({"type": "revoked", "code": code})
                            return
                        conn.allowed_modules = await _allowed_modules(user_id, tenant_id)
                        last_recheck = time.time()
                    else:
                        yield _sse(msg)

                if time.time() - last_recheck >= RECHECK_S:
                    code = await _revocation_code(user_id, tenant_id, role)
                    if code:
                        yield _sse({"type": "revoked", "code": code})
                        return
                    conn.allowed_modules = await _allowed_modules(user_id, tenant_id)
                    last_recheck = time.time()
        except asyncio.CancelledError:
            pass
        finally:
            hub.unregister(conn.id)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",  # C6: lawan buffering proxy apa pun
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
