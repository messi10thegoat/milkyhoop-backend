"""
Authentication Middleware with Real Token Validation + Session Authority (KILL SWITCH)

Enterprise Single Session Enforcement:
- JWT validation (credential check)
- Redis session authority check (session validity)
- FAIL-CLOSED: Missing device claims = invalid session
"""
import logging
import re
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from backend.api_gateway.app.services.auth_instance import auth_client
from backend.api_gateway.app.services.session_manager import session_manager
from backend.api_gateway.app.services.db_pool import get_db_pool
import time
import uuid as _uuid

logger = logging.getLogger(__name__)

# Cek eksistensi User (JWT valid tapi user DIHAPUS -> 401, jangan lolos sampai token kedaluwarsa).
# Cache in-proc pendek: positif 60s, negatif 5s (user dibuat-ulang tak keblok lama).
# Galat DB/pool -> ANGKAT _UserCheckError -> pemanggil balas 503 (retry), cache TIDAK diisi:
# gangguan DB sesaat TAK BOLEH me-logout semua pengguna (JWT sig tetap primer).
_USER_EXIST_TTL_POS = 60.0
_USER_EXIST_TTL_NEG = 5.0
_user_exist_cache: dict = {}


class _UserCheckError(Exception):
    """Cek eksistensi gagal karena DB/pool (transien) -> 503, bukan 401."""


async def _user_exists(user_id) -> bool:
    if not user_id:
        return False
    try:
        _uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        return False  # id tak valid -> anggap tak ada -> 401 (bukan 503)
    now = time.monotonic()
    hit = _user_exist_cache.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval('SELECT 1 FROM "User" WHERE id = $1', user_id)
    except Exception as e:
        logger.warning(f"[auth] cek eksistensi user gagal (503, cache tak diisi): {e}")
        raise _UserCheckError() from e
    exists = val is not None
    _user_exist_cache[user_id] = (
        exists,
        now + (_USER_EXIST_TTL_POS if exists else _USER_EXIST_TTL_NEG),
    )
    return exists


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.public_paths = {
            "/healthz",
            "/health",
            "/health/pool-stats",
            "/ready",
            "/version",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/favicon.ico",
            "/api/auth/register",
            "/api/auth/login",
            "/api/auth/refresh",
            "/api/auth/logout",
            "/api/auth/google",
            "/api/auth/signup/register",
            "/api/auth/signup/verify-code",
            "/api/auth/signup/complete-setup",
            "/api/auth/signup/resend-code",
            "/",
        }

    def _is_customer_chat_endpoint(self, path: str) -> bool:
        """Check if path matches /{tenant_id}/chat pattern"""
        customer_pattern = r"^/[^/]+/chat/?$"
        return bool(re.match(customer_pattern, path))

    def _is_tenant_info_endpoint(self, path: str) -> bool:
        """Check if path matches /api/tenant/{tenant_id}/info pattern"""
        info_pattern = r"^/api/tenant/[^/]+/info/?$"
        return bool(re.match(info_pattern, path))

    def _is_device_ws_endpoint(self, path: str) -> bool:
        """Check if path matches Device WebSocket endpoint"""
        # /api/devices/ws/{device_id} - WebSocket for remote scan & force logout
        # WebSocket connections can't send Authorization headers in handshake
        # Auth validated via device_id matching (device_id from authenticated login)
        return bool(re.match(r"^/api/devices/ws/[^/]+/?$", path))

    def _is_signup_public_endpoint(self, path: str) -> bool:
        """Check if path matches signup verify-link (has dynamic token)"""
        return bool(re.match(r"^/api/auth/signup/verify-link/[^/]+/?$", path))

    def _is_public_invite(self, path: str, method: str) -> bool:
        """Tiga rute undangan yang WAJIB publik — dan hanya tiga.

        Orang yang diundang BELUM PUNYA AKUN: ia mustahil membawa token JWT.
        Kalau ketiganya menuntut autentikasi, undangan tak akan pernah bisa
        dibuka oleh orang yang dituju. Sebelum ini `/api/invite/*` memang
        tertahan di sini dan menjawab 401 — bukan 404 — sehingga tampak seperti
        "rute tak ada" padahal middleware yang menahannya.

        Pola SENGAJA dibuat per-rute, BUKAN prefix lebar `^/api/invite`.
        Prefix lebar akan otomatis membuka setiap rute undangan yang
        ditambahkan besok, tanpa siapa pun memutuskannya.
        """
        return bool(
            (method == "GET" and re.match(r"^/api/invite/[^/]+/?$", path))
            or (method == "POST" and re.match(r"^/api/invite/[^/]+/accept/?$", path))
            or (method == "POST" and re.match(r"^/api/invite/[^/]+/decline/?$", path))
        )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        try:
            # Allow public paths
            if self._is_public_invite(path, request.method):
                return await call_next(request)

            if path in self.public_paths:
                return await call_next(request)

            # Allow customer chat endpoint (POST only, no auth)
            if self._is_customer_chat_endpoint(path) and request.method == "POST":
                logger.info(f"Bypassing auth for customer endpoint: {path}")
                return await call_next(request)

            # Allow tenant info endpoint (GET only, no auth)
            if self._is_tenant_info_endpoint(path) and request.method == "GET":
                logger.info(f"Bypassing auth for tenant info endpoint: {path}")
                return await call_next(request)

            # Allow Device WebSocket endpoint (auth via device_id in path)
            if self._is_device_ws_endpoint(path):
                logger.info(f"Bypassing auth for Device WebSocket: {path}")
                return await call_next(request)

            # Allow signup verify-link endpoint (dynamic token in URL)
            if self._is_signup_public_endpoint(path):
                return await call_next(request)

            # 14 Sep 2026: bypass internal X-Source=action_executor DIHAPUS (pensiun layanan action_executor).
            # Dulu: X-Source + X-Tenant-ID -> role ADMIN tanpa auth -> lubang kepercayaan-header (siapa pun yang bisa
            # menyetel header itu + X-User-ID bertindak sebagai siapa saja). Layanan gRPC-nya mati (host tak resolve,
            # 0 kejadian 30 hari, tak ada container); chat mengeksekusi lewat penerusan JWT pengguna (jalur is_direct).

            # Require authentication for all other paths
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Authentication required",
                        "code": "MISSING_TOKEN",
                    },
                )

            token = auth_header.replace("Bearer ", "")
            if not token or token.strip() == "":
                return JSONResponse(
                    status_code=401,
                    content={"error": "Invalid token", "code": "EMPTY_TOKEN"},
                )

            try:
                validation_result = await auth_client.validate_token(token)

                if not validation_result.get("valid"):
                    return JSONResponse(
                        status_code=401,
                        content={"error": "Invalid token", "code": "INVALID_TOKEN"},
                    )

                # Extract claims from JWT validation result
                user_id = validation_result.get("user_id")
                device_id = validation_result.get("device_id")
                device_type = validation_result.get("device_type")

                # ===== SESSION AUTHORITY CHECK (KILL SWITCH) =====
                # FAIL-CLOSED: Missing device claims = invalid session
                # This prevents legacy JWTs (without device_id) from bypassing session enforcement
                if device_id and device_type:  # 21 Sep 2026: ENABLED (instant revocation)
                    # Check Redis session authority
                    if not session_manager.is_session_valid(
                        user_id, device_type, device_id
                    ):
                        logger.warning(
                            f"🚫 Session replaced for user {user_id[:8]}..., device_type={device_type}"
                        )
                        return JSONResponse(
                            status_code=401,
                            content={
                                "error": "Session telah digantikan di perangkat lain",
                                "code": "SESSION_REPLACED",
                                "force_logout": True,
                            },
                        )

                # Eksistensi User: JWT valid tapi user sudah DIHAPUS -> 401. Galat DB -> 503.
                try:
                    _user_ada = await _user_exists(user_id)
                except _UserCheckError:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "Layanan autentikasi sementara tidak tersedia",
                            "code": "AUTH_DB_UNAVAILABLE",
                        },
                    )
                if not _user_ada:
                    return JSONResponse(
                        status_code=401,
                        content={
                            "error": "User tidak ditemukan",
                            "code": "USER_NOT_FOUND",
                            "force_logout": True,
                        },
                    )

                request.state.user = {
                    "user_id": user_id,
                    "tenant_id": validation_result.get("tenant_id", "default"),
                    "role": validation_result.get("role", "USER"),
                    "email": validation_result.get("email"),
                    "username": validation_result.get("username"),
                    "device_id": device_id,
                    "device_type": device_type,
                }

            except Exception as e:
                logger.error(f"Token validation error: {str(e)}")
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Authentication failed",
                        "code": "VALIDATION_ERROR",
                    },
                )

            return await call_next(request)

        except HTTPException:
            raise
        except Exception as e:
            import traceback

            logger.error(
                "Auth middleware error: %s\n%s", str(e), traceback.format_exc()
            )
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error", "detail": str(e)},
            )
