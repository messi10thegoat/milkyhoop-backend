"""
Rate Limiting Middleware
=========================
Redis-backed distributed rate limiter for production.
Uses sliding window algorithm with atomic Redis operations.

Features:
- Distributed rate limiting across multiple instances
- Sliding window for smooth rate limiting
- Per-IP and per-user rate limits
- Stricter limits for auth endpoints
- Graceful fallback to in-memory if Redis unavailable
"""
import time
import asyncio
import logging
from collections import defaultdict
from typing import Dict, Tuple, Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import settings

logger = logging.getLogger(__name__)

# Try to import redis, fallback to in-memory if not available
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available, using in-memory rate limiting")


class RedisRateLimiter:
    """
    Redis-backed rate limiter using sliding window.
    Uses sorted sets for efficient windowed counting.
    """

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._client: Optional[redis.Redis] = None
        self._connected = False

    async def connect(self):
        """Initialize Redis connection"""
        if not REDIS_AVAILABLE:
            return False

        try:
            self._client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                retry_on_timeout=True
            )
            # Test connection
            await self._client.ping()
            self._connected = True
            logger.info("Redis rate limiter connected")
            return True
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            self._connected = False
            return False

    async def close(self):
        """Close Redis connection"""
        if self._client:
            await self._client.close()
            self._connected = False

    async def is_rate_limited(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> Tuple[bool, int, int]:
        """
        Check if key is rate limited using Redis sorted set.
        Returns: (is_limited, remaining_requests, retry_after_seconds)
        """
        if not self._connected or not self._client:
            return False, max_requests, 0

        try:
            now = time.time()
            window_start = now - window_seconds
            redis_key = f"ratelimit:{key}"

            # Use pipeline for atomic operations
            async with self._client.pipeline(transaction=True) as pipe:
                # Remove old entries
                pipe.zremrangebyscore(redis_key, 0, window_start)
                # Count current entries
                pipe.zcard(redis_key)
                # Add current request
                pipe.zadd(redis_key, {str(now): now})
                # Set expiry
                pipe.expire(redis_key, window_seconds + 1)

                results = await pipe.execute()
                request_count = results[1]  # zcard result

            if request_count >= max_requests:
                # Get oldest timestamp in window for retry-after
                oldest = await self._client.zrange(redis_key, 0, 0, withscores=True)
                if oldest:
                    oldest_time = oldest[0][1]
                    retry_after = int(oldest_time + window_seconds - now) + 1
                else:
                    retry_after = window_seconds
                return True, 0, max(1, retry_after)

            remaining = max_requests - request_count - 1
            return False, max(0, remaining), 0

        except Exception as e:
            logger.error(f"Redis rate limit error: {e}")
            # Fail open - allow request on error
            return False, max_requests, 0


class InMemoryRateLimiter:
    """
    Fallback in-memory rate limiter.
    Used when Redis is not available.
    """

    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)
        self._last_cleanup = time.time()

    def is_rate_limited(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> Tuple[bool, int, int]:
        """Check if key is rate limited using sliding window"""
        current_time = time.time()
        window_start = current_time - window_seconds

        # Clean old requests outside window
        self._requests[key] = [
            ts for ts in self._requests[key]
            if ts > window_start
        ]

        request_count = len(self._requests[key])

        if request_count >= max_requests:
            oldest = min(self._requests[key]) if self._requests[key] else current_time
            retry_after = int(oldest + window_seconds - current_time) + 1
            return True, 0, max(1, retry_after)

        # Add current request
        self._requests[key].append(current_time)
        remaining = max_requests - request_count - 1

        # Periodic cleanup
        if current_time - self._last_cleanup > 60:
            self._cleanup()
            self._last_cleanup = current_time

        return False, max(0, remaining), 0

    def _cleanup(self):
        """Remove stale entries"""
        current_time = time.time()
        max_window = 3600  # 1 hour max

        keys_to_delete = []
        for key, timestamps in self._requests.items():
            self._requests[key] = [
                ts for ts in timestamps
                if current_time - ts < max_window
            ]
            if not self._requests[key]:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del self._requests[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Production-ready rate limiter middleware.
    Uses Redis for distributed rate limiting with in-memory fallback.
    """

    _redis_limiter: Optional[RedisRateLimiter] = None
    _memory_limiter: Optional[InMemoryRateLimiter] = None
    _initialized = False

    def __init__(self, app):
        super().__init__(app)

        # Auth endpoints get stricter limits (brute force protection)
        self.auth_paths = {
            "/api/auth/login",
            "/api/auth/register",
            "/api/auth/refresh",
            "/api/auth/forgot-password",
            "/api/auth/reset-password",
        }

        # Paths exempt from rate limiting
        self.exempt_paths = {
            "/healthz",
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/metrics",
        }

        # Fast paths - skip rate limit for speed-critical endpoints
        self.fast_paths = {
            "/api/products/search/pos",  # Autocomplete <100ms
            "/api/products/barcode/",    # Barcode lookup
        }

    async def _ensure_initialized(self):
        """Lazy initialization of rate limiters"""
        if self._initialized:
            return

        # Try Redis first
        if REDIS_AVAILABLE and settings.REDIS_URL:
            self._redis_limiter = RedisRateLimiter(settings.REDIS_URL)
            connected = await self._redis_limiter.connect()
            if connected:
                self._initialized = True
                return

        # Fall back to in-memory
        logger.warning("Using in-memory rate limiter (not suitable for multi-instance)")
        self._memory_limiter = InMemoryRateLimiter()
        self._initialized = True

    def _get_client_key(self, request: Request) -> str:
        """Get unique identifier for client (IP + optional user ID)"""
        # Get real IP from proxy headers
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        else:
            real_ip = request.headers.get("X-Real-IP")
            if real_ip:
                client_ip = real_ip
            else:
                client_ip = request.client.host if request.client else "unknown"

        # If authenticated, include user_id for per-user limiting
        user = getattr(request.state, "user", None)
        if user and user.get("user_id"):
            return f"user:{user['user_id']}"

        return f"ip:{client_ip}"

    def _get_limits(self, path: str) -> Tuple[int, int]:
        """Get rate limit settings based on path"""
        if path in self.auth_paths:
            # Stricter limits for auth endpoints (brute force protection)
            return settings.RATE_LIMIT_AUTH_REQUESTS, settings.RATE_LIMIT_AUTH_WINDOW
        else:
            # Standard limits for other endpoints
            return settings.RATE_LIMIT_REQUESTS, settings.RATE_LIMIT_WINDOW

    async def _check_rate_limit(
        self,
        client_key: str,
        path: str
    ) -> Tuple[bool, int, int]:
        """Check rate limit using appropriate backend"""
        max_requests, window_seconds = self._get_limits(path)

        if self._redis_limiter and self._redis_limiter._connected:
            return await self._redis_limiter.is_rate_limited(
                client_key, max_requests, window_seconds
            )
        elif self._memory_limiter:
            return self._memory_limiter.is_rate_limited(
                client_key, max_requests, window_seconds
            )
        else:
            # No limiter available, allow request
            return False, max_requests, 0

    async def dispatch(self, request: Request, call_next):
        # Skip if rate limiting disabled
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path

        # Skip exempt paths
        if path in self.exempt_paths:
            return await call_next(request)

        # Skip fast paths (autocomplete etc)
        for fast_path in self.fast_paths:
            if path.startswith(fast_path):
                return await call_next(request)

        # Ensure initialized
        await self._ensure_initialized()

        client_key = self._get_client_key(request)
        is_limited, remaining, retry_after = await self._check_rate_limit(client_key, path)

        if is_limited:
            logger.warning(
                f"Rate limit exceeded for {client_key} on {path}",
                extra={
                    "client": client_key,
                    "path": path,
                    "retry_after": retry_after,
                    "event_type": "RATE_LIMITED"
                }
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too many requests",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "retry_after": retry_after,
                    "message": f"Rate limit exceeded. Please retry after {retry_after} seconds."
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Remaining": "0",
                }
            )

        # Add rate limit headers to response
        response = await call_next(request)
        max_requests, _ = self._get_limits(path)
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
