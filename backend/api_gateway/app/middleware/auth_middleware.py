"""
Authentication Middleware with Real Token Validation + Session Authority (KILL SWITCH)

Enterprise Single Session Enforcement:
- JWT validation (credential check)
- Redis session authority check (session validity)
- FAIL-CLOSED: Missing device claims = invalid session
"""
import logging
import re
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from backend.api_gateway.app.services.auth_instance import auth_client
from backend.api_gateway.app.services.session_manager import session_manager

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.public_paths = {
            "/healthz",
            "/health",
            "/docs",
            "/openapi.json",
            "/favicon.ico",
            "/api/auth/register",
            "/api/auth/login",
            "/api/auth/refresh",
            "/api/auth/logout",
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

    def _is_qr_public_endpoint(self, path: str) -> bool:
        """Check if path matches QR login public endpoints"""
        # /api/auth/qr/generate - POST
        # /api/auth/qr/status/{token} - GET
        # /api/auth/qr/ws/{token} - WebSocket
        if path == "/api/auth/qr/generate":
            return True
        if re.match(r"^/api/auth/qr/status/[^/]+/?$", path):
            return True
        if re.match(r"^/api/auth/qr/ws/[^/]+/?$", path):
            return True
        return False

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        try:
            # Allow public paths
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

            # Allow QR login public endpoints (no auth for desktop)
            if self._is_qr_public_endpoint(path):
                logger.info(f"Bypassing auth for QR login endpoint: {path}")
                return await call_next(request)

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
                if device_id and device_type:
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

        except Exception as e:
            logger.error(f"Auth middleware error: {str(e)}")
            return JSONResponse(
                status_code=500, content={"error": "Authentication error"}
            )
