"""
Role-Based Access Control (RBAC) Middleware
Enforces role requirements on protected endpoints
"""
import logging
import re
from typing import Dict, List, Set, Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class RBACMiddleware(BaseHTTPMiddleware):
    """
    Enforces role-based access control on API endpoints.

    Roles hierarchy (highest to lowest):
    - ADMIN: Full access to all endpoints
    - OWNER: Access to tenant management + user operations
    - USER: Standard user access
    - FREE: Limited free tier access
    """

    # Role hierarchy - higher roles include permissions of lower roles
    ROLE_HIERARCHY = {
        "ADMIN": 4,
        "OWNER": 3,
        "USER": 2,
        "FREE": 1,
    }

    # Regex pattern for barcode registration: /api/products/{uuid}/barcode
    # Allows FREE tier to register barcodes to their products
    BARCODE_REGISTER_PATTERN = re.compile(r'^/api/products/[^/]+/barcode$')

    def __init__(self, app):
        super().__init__(app)

        # Define endpoint access requirements
        # Format: path_prefix -> minimum required role
        # NOTE: More specific routes MUST come before generic routes!
        self.protected_routes: Dict[str, str] = {
            # Admin-only endpoints
            "/api/admin/": "ADMIN",

            # Owner/Admin endpoints (tenant management)
            "/api/tenant/settings": "OWNER",
            "/api/tenant/users": "OWNER",
            "/api/tenant/billing": "OWNER",

            # Free tier endpoints (MUST be before /api/products/)
            "/api/chat/": "FREE",
            "/api/tenant/chat": "FREE",
            "/api/products/search/pos": "FREE",  # Autocomplete for POS
            "/api/products/barcode/": "FREE",    # Barcode lookup for POS
            "/api/inventory/": "FREE",           # Inventory access for FREE tier
            "/api/members/": "FREE",             # Customer/Members access for FREE tier

            # Standard user endpoints (FREE tier can also do transactions)
            "/api/transactions/": "FREE",
            "/api/products/": "USER",
            "/api/customers/": "USER",
            "/api/kasbank/": "USER",
            "/api/debt/": "USER",
            "/api/insight/": "USER",
        }

        # Endpoints that bypass RBAC (handled by auth middleware)
        self.public_paths: Set[str] = {
            "/healthz",
            "/health",
            "/docs",
            "/openapi.json",
            "/api/auth/",
        }

    def _get_role_level(self, role: str) -> int:
        """Get numeric level for role (higher = more permissions)"""
        return self.ROLE_HIERARCHY.get(role.upper(), 0)

    def _has_permission(self, user_role: str, required_role: str) -> bool:
        """Check if user's role meets the required role level"""
        user_level = self._get_role_level(user_role)
        required_level = self._get_role_level(required_role)
        return user_level >= required_level

    def _get_required_role(self, path: str) -> Optional[str]:
        """Get required role for a given path, or None if no RBAC needed"""
        # Check if path is public
        for public_path in self.public_paths:
            if path.startswith(public_path):
                return None

        # Special case: barcode registration endpoint for FREE tier
        # Pattern: /api/products/{product_id}/barcode
        if self.BARCODE_REGISTER_PATTERN.match(path):
            return "FREE"

        # Check protected routes
        for route_prefix, required_role in self.protected_routes.items():
            if path.startswith(route_prefix):
                return required_role

        # Default: require at least FREE tier for any authenticated endpoint
        return "FREE"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Get required role for this endpoint
        required_role = self._get_required_role(path)

        # If no role required (public endpoint), proceed
        if required_role is None:
            return await call_next(request)

        # Get user from request state (set by auth middleware)
        user = getattr(request.state, "user", None)

        # If no user info, let auth middleware handle it
        if not user:
            return await call_next(request)

        user_role = user.get("role", "FREE")

        # Check if user has required permission
        if not self._has_permission(user_role, required_role):
            logger.warning(
                f"RBAC denied: user_id={user.get('user_id')} role={user_role} "
                f"required={required_role} path={path}"
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Forbidden",
                    "code": "INSUFFICIENT_PERMISSIONS",
                    "message": f"This action requires {required_role} role or higher.",
                    "required_role": required_role,
                    "your_role": user_role,
                }
            )

        return await call_next(request)
