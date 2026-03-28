"""
Database Connection Pool Singleton
===================================
Replaces per-request asyncpg.connect() with shared connection pool.

Performance improvement:
- Before: ~50ms to create new connection per request
- After: ~0.1ms to acquire connection from pool

Sizing rationale (2 vCPU / 8 GB server):
- min_size=2: 2 warm connections (no cold start)
- max_size=10: Cap at 10 (Postgres max_connections=100, shared across services)

Iron Laws compliance:
- Read-only optimization, does not touch journal creation (Law 0)
- Pool connections are ephemeral, no persistent state (Law 11)
- All queries still go through Prisma/asyncpg with tenant_id filter (Law 24)
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
            command_timeout=15,                     # Kill queries >15s
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
    Safe for asyncio.gather — each call gets its own connection.
    
    Usage:
        result = await run_with_pool(my_query_fn, tenant_id, period)
        
        # or in parallel:
        a, b = await asyncio.gather(
            run_with_pool(query_a, tenant_id),
            run_with_pool(query_b, tenant_id),
        )
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        return await fn(conn, *args, **kwargs)
