"""
Tenant Validation Middleware
============================
Prevents IDOR (Insecure Direct Object Reference) attacks by validating
that the tenant_id in URL matches the authenticated user's tenant_id from JWT.

Security: Ensures users can only access data from their own tenant.
"""
import re
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class TenantValidationMiddleware(BaseHTTPMiddleware):
    """
    Validates tenant_id in URL against JWT token's tenant_id.

    Protects endpoints like:
    - /api/tenant/{tenant_id}/chat
    - /api/tenant/{tenant_id}/products
    - /api/tenant/{tenant_id}/transactions

    Allows:
    - Public endpoints (no auth required)
    - Endpoints without tenant_id in URL
    - ADMIN role users (can access any tenant)
    """

    # Pattern to extract tenant_id from URL
    TENANT_URL_PATTERN = re.compile(r'^/api/tenant/([^/]+)/')

    # Public tenant endpoints that don't require tenant validation
    # (customer-facing chat endpoints)
    PUBLIC_TENANT_PATHS = {
        "/chat",  # /{tenant_id}/chat is public customer endpoint
    }

    def __init__(self, app):
        super().__init__(app)

    def _extract_tenant_from_url(self, path: str) -> str | None:
        """Extract tenant_id from URL path if present"""
        match = self.TENANT_URL_PATTERN.match(path)
        if match:
            return match.group(1)
        return None

    def _is_public_tenant_endpoint(self, path: str) -> bool:
        """Check if path is a public customer endpoint"""
        # Pattern: /{tenant_id}/chat (no /api prefix)
        if re.match(r'^/[^/]+/chat/?$', path):
            return True
        return False

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip public customer endpoints (/{tenant_id}/chat)
        if self._is_public_tenant_endpoint(path):
            return await call_next(request)

        # Extract tenant_id from URL
        url_tenant_id = self._extract_tenant_from_url(path)

        # If no tenant_id in URL, skip validation
        if not url_tenant_id:
            return await call_next(request)

        # Get user from request state (set by auth middleware)
        user = getattr(request.state, "user", None)

        # If no user (unauthenticated), let auth middleware handle it
        if not user:
            return await call_next(request)

        user_tenant_id = user.get("tenant_id")
        user_role = user.get("role", "").upper()

        # ADMIN can access any tenant
        if user_role == "ADMIN":
            logger.info(
                f"Admin access granted: user={user.get('user_id')} accessing tenant={url_tenant_id}"
            )
            return await call_next(request)

        # Validate tenant_id matches
        if url_tenant_id != user_tenant_id:
            logger.warning(
                f"IDOR attempt blocked: user={user.get('user_id')} "
                f"(tenant={user_tenant_id}) tried to access tenant={url_tenant_id} "
                f"path={path}"
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Forbidden",
                    "code": "TENANT_MISMATCH",
                    "message": "You don't have permission to access this tenant's resources.",
                }
            )

        # Tenant matches, proceed
        return await call_next(request)
