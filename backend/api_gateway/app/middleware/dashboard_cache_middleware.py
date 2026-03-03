"""
Dashboard Cache Invalidation Middleware

Automatically invalidates dashboard Redis cache after successful write operations
on financial endpoints. This eliminates the need to add invalidation calls to
each individual router.

Pattern: Intercept POST/PUT/PATCH/DELETE responses on financial paths.
If status < 400 (success), invalidate dashboard cache for the tenant.
"""
import logging
import asyncio
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Financial endpoint path prefixes that affect dashboard data
FINANCIAL_PATHS = frozenset({
    "/api/sales-invoices",
    "/api/invoices",
    "/api/bills",
    "/api/expenses",
    "/api/receive-payments",
    "/api/bill-payments",
    "/api/journals",
    "/api/credit-notes",
    "/api/vendor-credits",
    "/api/kasbank",
    "/api/bank-transactions",
    "/api/bank-transfers",
    "/api/customer-deposits",
    "/api/vendor-deposits",
    "/api/cheques",
    "/api/opening-balance",
    "/api/sales-receipts",
    "/api/recurring-invoices",
    "/api/recurring-bills",
    "/api/fixed-assets",
    "/api/payroll",
    "/api/fiscal-years",
    "/api/periods",
    "/api/bank-reconciliation",
    "/api/stock-adjustments",
    "/api/production-costing",
})

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

SKIP_SUBPATHS = frozenset({
    "/calculate",
    "/search",
    "/export",
    "/preview",
    "/validate",
})


class DashboardCacheInvalidationMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Quick exit: only process write methods
        if request.method not in WRITE_METHODS:
            return response

        # Quick exit: only process successful writes
        if response.status_code >= 400:
            return response

        path = request.url.path

        # Check if path matches a financial endpoint
        if not self._is_financial_path(path):
            return response

        # Skip read-only POST operations
        if self._is_skip_subpath(path):
            return response

        # Extract tenant_id
        tenant_id = self._get_tenant_id(request)
        if not tenant_id:
            logger.warning(f"[DashboardCacheMW] No tenant_id for {request.method} {path}")
            return response

        # Fire-and-forget invalidation
        logger.info(f"[DashboardCacheMW] Invalidating cache: tenant={tenant_id} {request.method} {path}")
        asyncio.ensure_future(self._invalidate(tenant_id, request.method, path))

        return response

    def _is_financial_path(self, path: str) -> bool:
        for prefix in FINANCIAL_PATHS:
            if path.startswith(prefix):
                return True
        return False

    def _is_skip_subpath(self, path: str) -> bool:
        for skip in SKIP_SUBPATHS:
            if path.endswith(skip):
                return True
        return False

    def _get_tenant_id(self, request: Request) -> str | None:
        if not hasattr(request.state, 'user') or not request.state.user:
            return None
        user = request.state.user
        if isinstance(user, dict):
            return user.get('tenant_id')
        return getattr(user, 'tenant_id', None)

    async def _invalidate(self, tenant_id: str, method: str, path: str):
        try:
            from ..services.cache import invalidate_dashboard_cache
            deleted = await invalidate_dashboard_cache(tenant_id)
            logger.info(f"[DashboardCacheMW] Cache invalidated: tenant={tenant_id} deleted={deleted}")
        except Exception as e:
            logger.warning(f"[DashboardCacheMW] Invalidation failed: {e}")
