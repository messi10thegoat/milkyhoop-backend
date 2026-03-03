"""
Stale-While-Revalidate Cache
==============================
Redis-backed cache with background refresh.

Behavior:
1. Cache HIT (fresh)  -> return instantly (~1ms)
2. Cache HIT (stale)  -> return stale data, refresh in background
3. Cache MISS          -> fetch from DB, cache result, return

This prevents cache stampede: when TTL expires, only ONE request
triggers a DB query. All others get stale (but recent) data.

Iron Laws compliance:
- Cache is read optimization only, NOT source of truth (Law 21)
- All cached data derived from journal queries (Law 1, 16)
- Cache keys MUST include tenant_id (Law 24)
- Write-through invalidation on financial mutations (Law 8)
- Stale data is acceptable for read-only dashboard views
- System works without cache (graceful degradation)
"""
import json
import asyncio
import logging
from datetime import datetime

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# Background refresh locks - prevent multiple simultaneous refreshes per key
_refresh_locks: dict[str, asyncio.Lock] = {}


async def cached_fetch(
    cache_key: str,
    fetch_fn,
    ttl: int = 30,
    stale_ttl: int = 300,
):
    """
    Cache wrapper with stale-while-revalidate pattern.
    
    Args:
        cache_key:  Redis key (MUST include tenant_id)
        fetch_fn:   Async function returning dict (DB query)
        ttl:        Fresh TTL seconds (data considered "fresh")
        stale_ttl:  Stale TTL seconds (max age before hard refresh)
    
    Returns:
        dict from cache or fresh fetch
    """
    r = await get_redis()
    
    if r is not None:
        try:
            raw = await r.get(cache_key)
            if raw:
                cached = json.loads(raw)
                cached_at = cached.get("_cached_at", 0)
                age = datetime.utcnow().timestamp() - cached_at
                
                # Fresh - serve immediately
                if age < ttl:
                    return cached["data"]
                
                # Stale but within stale_ttl - serve stale, refresh in background
                if age < stale_ttl:
                    _trigger_background_refresh(cache_key, fetch_fn, ttl, stale_ttl)
                    return cached["data"]
                
                # Expired - fall through to fresh fetch
        except Exception as e:
            logger.warning(f"Cache read error for {cache_key}: {e}")
    
    # Cache MISS or EXPIRED or Redis down - fetch fresh
    data = await fetch_fn()
    await _store_cache(cache_key, data, stale_ttl)
    return data


async def _store_cache(cache_key: str, data, stale_ttl: int):
    """Store data in Redis with timestamp for age tracking."""
    r = await get_redis()
    if r is None:
        return
    
    try:
        payload = {
            "data": data,
            "_cached_at": datetime.utcnow().timestamp(),
        }
        await r.setex(cache_key, stale_ttl, json.dumps(payload, default=str))
    except Exception as e:
        logger.warning(f"Cache write error for {cache_key}: {e}")


def _trigger_background_refresh(cache_key, fetch_fn, ttl, stale_ttl):
    """Refresh cache in background - fire and forget."""
    if cache_key not in _refresh_locks:
        _refresh_locks[cache_key] = asyncio.Lock()
    
    lock = _refresh_locks[cache_key]
    
    async def _refresh():
        if lock.locked():
            return  # Another refresh already in progress
        async with lock:
            try:
                data = await fetch_fn()
                await _store_cache(cache_key, data, stale_ttl)
            except Exception as e:
                logger.warning(f"Background cache refresh failed for {cache_key}: {e}")
            finally:
                # Cleanup lock reference after TTL
                await asyncio.sleep(ttl)
                _refresh_locks.pop(cache_key, None)
    
    asyncio.create_task(_refresh())


async def invalidate_dashboard_cache(tenant_id: str):
    """
    Invalidate ALL dashboard cache for a tenant.
    
    MUST be called after ANY write operation that affects dashboard:
    - Create/update/delete invoice
    - Create/update/delete bill
    - Create/update/delete expense
    - Receive payment
    - Post journal entry
    - Bank reconciliation
    
    Uses SCAN (not KEYS) - safe for production Redis.
    
    Iron Law 8 compliance: cache invalidation is explicit, not silent.
    """
    r = await get_redis()
    if r is None:
        return
    
    pattern = f"dashboard:*:{tenant_id}:*"
    try:
        cursor = 0
        deleted_count = 0
        while True:
            cursor, keys = await r.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await r.delete(*keys)
                deleted_count += len(keys)
            if cursor == 0:
                break
        if deleted_count > 0:
            logger.info(f"Invalidated {deleted_count} dashboard cache keys for tenant {tenant_id}")
    except Exception as e:
        logger.warning(f"Cache invalidation error for {tenant_id}: {e}")
