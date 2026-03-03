"""
Redis Singleton Client
=======================
Single global Redis connection pool initialized at app startup.
Replaces per-request redis.from_url() with reusable pool.

Performance improvement:
- Before: ~10ms new TCP connection per cache operation
- After: ~0.1ms reuse existing connection from pool

Iron Laws compliance:
- Redis used as read-only cache, NOT source of truth (Law 21)
- All cached data derived from journal queries (Law 1, 16)
- Cache keys include tenant_id for isolation (Law 24)
"""
import logging
import os

from ..config import settings

logger = logging.getLogger(__name__)

# Try import redis
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("redis.asyncio not available")

_redis = None


async def get_redis():
    """
    Get or create singleton Redis client with connection pool.
    Returns None if Redis is unavailable (app continues without cache).
    """
    global _redis
    if _redis is not None:
        return _redis
    
    if not REDIS_AVAILABLE:
        return None
    
    try:
        redis_url = settings.REDIS_URL
        max_conns = int(os.getenv("REDIS_POOL_MAX", "20"))
        
        _redis = aioredis.from_url(
            redis_url,
            max_connections=max_conns,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        # Test connection
        await _redis.ping()
        logger.info(f"Redis client connected (pool max={max_conns})")
        return _redis
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. App continues without cache.")
        _redis = None
        return None


async def close_redis():
    """Close Redis on app shutdown."""
    global _redis
    if _redis:
        await _redis.close()
        _redis = None
        logger.info("Redis client closed")
