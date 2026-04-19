"""
Database Connection Pool Singleton
===================================
Replaces per-request asyncpg.connect() with shared connection pool.

Performance improvement:
- Before: ~50ms to create new connection per request
- After: ~0.1ms to acquire connection from pool

Sizing rationale (2 vCPU / 8 GB server):
- min_size=2: 2 warm connections (singleton pool only)
- max_size=10: Cap at 10 (Postgres max_connections=100, shared across services)

Iron Laws compliance:
- Read-only optimization, does not touch journal creation (Law 0)
- Pool connections are ephemeral, no persistent state (Law 11)
- All queries still go through Prisma/asyncpg with tenant_id filter (Law 24)
- Singleton pool pattern, no per-router pools (Law 32)
"""
import asyncpg
import logging
import os

from ..config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_db_pool() -> asyncpg.Pool:
    """
    Get or create singleton asyncpg connection pool.
    Thread-safe: asyncpg.create_pool is async but pool itself is shared.
    """
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        min_size = int(os.getenv("DB_POOL_MIN", "2"))
        max_size = int(os.getenv("DB_POOL_MAX", "10"))

        _pool = await asyncpg.create_pool(
            **db_config,
            min_size=min_size,
            max_size=max_size,
            max_inactive_connection_lifetime=300,  # Close idle conns after 5 min
            command_timeout=15,  # Kill queries >15s
        )
        logger.info(f"DB pool created: min={min_size}, max={max_size}")
    return _pool


async def close_db_pool():
    """Close pool on app shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("DB pool closed")


async def run_with_pool(fn, *args, **kwargs):
    """
    Acquire connection from pool, run async function, release.
    Safe for asyncio.gather - each call gets its own connection.
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await fn(conn, *args, **kwargs)


# === PoolConnectionWrapper ===
# Wraps a pool connection so existing handler code that calls .close()
# safely RELEASES the connection back to the pool instead of terminating it.
# This lets us migrate routers from asyncpg.connect() to pool without
# rewriting every handler's try/finally.


class PoolConnectionWrapper:
    """
    Wrapper for asyncpg.Connection acquired from a pool.
    Override close() to release back to pool instead of disconnecting.
    All other attribute access is forwarded to the underlying connection.
    """

    __slots__ = ("_conn", "_pool", "_released")

    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._released = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        """Release connection back to pool. Idempotent."""
        if self._released:
            return
        self._released = True
        try:
            await self._pool.release(self._conn)
        except Exception as e:
            logger.warning(f"PoolConnectionWrapper release failed: {e}")


async def get_db_connection() -> PoolConnectionWrapper:
    """
    Acquire a wrapped connection from the singleton pool.
    Drop-in replacement for asyncpg.connect():

        # Old (BAD - bypasses pool, slow, exhausts max_connections):
        conn = await asyncpg.connect(**db_config)
        try:
            ...
        finally:
            await conn.close()

        # New (GOOD - uses singleton, .close() returns to pool):
        from ..services.db_pool import get_db_connection
        conn = await get_db_connection()
        try:
            ...
        finally:
            await conn.close()
    """
    pool = await get_db_pool()
    raw = await pool.acquire()
    return PoolConnectionWrapper(raw, pool)


# === DEFENSE IN DEPTH: Patch asyncpg.create_pool ===
# Law 32 hardening: any code that calls asyncpg.create_pool() without
# max_inactive_connection_lifetime gets stale connections after postgres
# server-side timeouts. We patch the default to 300s (5 min).
# Also default min_size=0 to prevent startup burst exceeding max_connections
# when 30+ routers each try to warm min_size=2 simultaneously.
import asyncpg as _asyncpg_module

_original_create_pool = _asyncpg_module.create_pool


def _patched_create_pool(*args, **kwargs):
    # Inject defaults if caller didn't specify
    if "max_inactive_connection_lifetime" not in kwargs:
        kwargs["max_inactive_connection_lifetime"] = 300.0
    if "command_timeout" not in kwargs:
        kwargs["command_timeout"] = 30
    if "min_size" not in kwargs:
        kwargs["min_size"] = 0
    return _original_create_pool(*args, **kwargs)


# Only patch once
if not getattr(_asyncpg_module.create_pool, "_milkyhoop_patched", False):
    _patched_create_pool._milkyhoop_patched = True
    _asyncpg_module.create_pool = _patched_create_pool
    logger.info("asyncpg.create_pool patched: default max_inactive=300s, min_size=0")
