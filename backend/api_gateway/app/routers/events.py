"""SSE realtime — GET /api/events/stream. Tahap 1.

Auth: middleware biasa memvalidasi Bearer di request.state.user (fetch-stream + header,
token 7-hari TAK masuk URL/log). Per-event autz: tenant sama + modul READ lewat policy
engine `can()` YANG SAMA dgn middleware (normalize_module_name: sales_invoice->INVOICE) —
satu sumber, tak ada drift (C4). Pencabutan (E): recheck user+membership tiap 60s & saat
membership_changed NOTIFY & saat token exp. Payload minimal {tbl,id,op}.
"""
import asyncio
import base64
import json
import logging
import time
from typing import Optional, Set

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..services.realtime import hub, ClientConn, TBL_MODULE
from ..services.policy_engine_client import get_policy_engine

router = APIRouter()
logger = logging.getLogger(__name__)

HEARTBEAT_S = 20
RECHECK_S = 60
MODULES = list(set(TBL_MODULE.values()))  # nama MIDDLEWARE (mis. 'sales_invoice')


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


async def _build_ctx(user_id: str, tenant_id: str, role: str):
    return await get_policy_engine().get_user_context(user_id, tenant_id, role)


async def _allowed_from_ctx(ctx) -> Set[str]:
    """Modul (nama middleware) yg boleh READ — via can() yg SAMA dgn middleware. Fail-closed."""
    pe = get_policy_engine()
    allowed: Set[str] = set()
    for m in MODULES:
        try:
            if await pe.can(ctx, "R", m):
                allowed.add(m)
        except Exception as e:
            logger.error(f"realtime can({m}) error: {e}")
    return allowed


def _revocation_from_ctx(ctx, role: str) -> Optional[str]:
    if getattr(ctx, "membership_active", True) is False:
        return "MEMBERSHIP_INACTIVE"
    # I4 (26 Sep 2026): `role` = TIER JWT (semua pengguna nyata ADMIN) -> dulu
    # pengecualian tier membuat anggota yang DIHAPUS tetap dianggap aktif.
    if (
        not getattr(ctx, "business_role_id", None)
        and getattr(ctx, "business_role_code", None) != "OWNER"
    ):
        return "MEMBERSHIP_INACTIVE"
    return None


async def _user_deleted(user_id: str) -> bool:
    """True bila baris "User" hilang (akun dihapus) — USER_NOT_FOUND. Fail-open saat gangguan DB."""
    try:
        async with get_policy_engine().pool.acquire() as conn:
            exists = await conn.fetchval('SELECT 1 FROM "User" WHERE id = $1', str(user_id))
        return not exists
    except Exception as e:
        logger.warning(f"realtime user-exist check error (fail-open): {e}")
        return False


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

    ctx = await _build_ctx(user_id, tenant_id, role)
    allowed = await _allowed_from_ctx(ctx)
    conn = ClientConn(tenant_id, user_id, allowed, exp)
    hub.register(conn)

    async def _recheck():
        """None kalau masih sah (dan refresh allowed_modules); else kode revoked."""
        if await _user_deleted(user_id):
            return "USER_NOT_FOUND"
        c2 = await _build_ctx(user_id, tenant_id, role)
        code = _revocation_from_ctx(c2, role)
        if code:
            return code
        conn.allowed_modules = await _allowed_from_ctx(c2)
        return None

    async def gen():
        last_recheck = time.time()
        try:
            # revocation-at-connect
            code0 = _revocation_from_ctx(ctx, role)
            if code0 or await _user_deleted(user_id):
                yield _sse({"type": "revoked", "code": code0 or "USER_NOT_FOUND"})
                return
            yield _sse({"type": "hello"})  # klien memicu resync awal
            while True:
                if conn.exp and time.time() >= conn.exp:
                    yield _sse({"type": "revoked", "code": "TOKEN_EXPIRED"})
                    return
                # bangun dekat exp supaya tutup TEPAT saat kedaluwarsa (bukan tunggu heartbeat)
                timeout = HEARTBEAT_S
                if conn.exp:
                    timeout = max(0.5, min(HEARTBEAT_S, conn.exp - time.time()))
                try:
                    msg = await asyncio.wait_for(conn.queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    if conn.exp and time.time() >= conn.exp:
                        continue  # kembali ke atas -> TOKEN_EXPIRED
                    yield ": keepalive\n\n"  # heartbeat komentar SSE (< CF 100s, nginx 300s)
                    msg = None

                if msg is not None:
                    if msg.get("type") == "recheck":
                        code = await _recheck()
                        if code:
                            yield _sse({"type": "revoked", "code": code})
                            return
                        last_recheck = time.time()
                    else:
                        yield _sse(msg)

                if time.time() - last_recheck >= RECHECK_S:
                    code = await _recheck()
                    if code:
                        yield _sse({"type": "revoked", "code": code})
                        return
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
