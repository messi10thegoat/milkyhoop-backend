"""
RLS Tenant Context — Law 24 Runtime Enforcement
================================================
Automatically sets PostgreSQL session variable `app.tenant_id` on every
asyncpg connection acquire, so that Row-Level Security policies are enforced.

Uses Python's contextvars to propagate tenant_id from auth middleware to DB layer
without modifying any router files.

Usage:
    1. Import and call `patch_asyncpg_pool()` at app startup (once)
    2. Call `set_tenant_context(tenant_id)` in auth middleware after extracting tenant_id
    3. All subsequent `pool.acquire()` calls in that request will automatically
       SET app.tenant_id before returning the connection.

Rollback: If this causes issues, simply ALTER ROLE milkyadmin BYPASSRLS;
"""
import contextvars
import logging

logger = logging.getLogger(__name__)

# Context variable — set per-request by auth middleware
_tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'rls_tenant_id', default=None
)


def set_tenant_context(tenant_id: str | None) -> None:
    """Set the tenant context for the current request. Called by auth middleware."""
    _tenant_id_var.set(tenant_id)


def get_tenant_context() -> str | None:
    """Get the current tenant context (for debugging/logging)."""
    return _tenant_id_var.get()


def patch_asyncpg_pool() -> None:
    """
    Monkey-patch asyncpg's PoolAcquireContext to automatically set
    app.tenant_id on every connection acquire.

    MUST be called once at app startup, before any pool is created.
    """
    import asyncpg.pool

    _original_aenter = asyncpg.pool.PoolAcquireContext.__aenter__

    async def _patched_aenter(self):
        # Call original to get the connection
        conn = await _original_aenter(self)

        # Set tenant context if available
        tenant_id = _tenant_id_var.get()
        if tenant_id:
            try:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id)
                )
            except Exception as e:
                logger.error(f"Failed to set RLS tenant context: {e}")
                # Don't fail the request — let the query proceed
                # (RLS will block access if NOBYPASSRLS is active)

        return conn

    asyncpg.pool.PoolAcquireContext.__aenter__ = _patched_aenter
    logger.info("✅ asyncpg pool patched for RLS tenant context (Law 24)")
