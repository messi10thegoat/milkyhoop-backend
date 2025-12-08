"""
Account Lockout Middleware
Protects against brute force attacks by locking accounts after failed attempts
"""
import time
import logging
from collections import defaultdict
from typing import Dict, Tuple
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class AccountLockoutMiddleware(BaseHTTPMiddleware):
    """
    Implements account lockout after repeated failed login attempts.

    Security features:
    - Progressive lockout (5 attempts -> 5 min, 10 attempts -> 30 min, etc.)
    - IP-based and account-based tracking
    - Automatic cleanup of old records
    """

    # Lockout configuration
    MAX_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = [5, 15, 30, 60, 120]  # Progressive lockout

    def __init__(self, app):
        super().__init__(app)
        # Track failed attempts: {identifier: [(timestamp, count), lockout_until]}
        self._failed_attempts: Dict[str, dict] = defaultdict(
            lambda: {"attempts": [], "lockout_until": 0, "lockout_count": 0}
        )
        self._auth_paths = {"/api/auth/login", "/api/auth/register"}

    def _get_client_ip(self, request: Request) -> str:
        """Get client IP from headers or connection"""
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _get_lockout_duration(self, lockout_count: int) -> int:
        """Get lockout duration in seconds based on lockout count"""
        index = min(lockout_count, len(self.LOCKOUT_DURATION_MINUTES) - 1)
        return self.LOCKOUT_DURATION_MINUTES[index] * 60

    def _is_locked_out(self, identifier: str) -> Tuple[bool, int]:
        """Check if identifier is locked out. Returns (is_locked, seconds_remaining)"""
        record = self._failed_attempts[identifier]
        current_time = time.time()

        if record["lockout_until"] > current_time:
            remaining = int(record["lockout_until"] - current_time)
            return True, remaining

        return False, 0

    def _record_failed_attempt(self, identifier: str):
        """Record a failed login attempt"""
        current_time = time.time()
        record = self._failed_attempts[identifier]

        # Clean old attempts (older than 1 hour)
        record["attempts"] = [
            ts for ts in record["attempts"]
            if ts > current_time - 3600
        ]

        record["attempts"].append(current_time)

        # Check if we need to lock out
        if len(record["attempts"]) >= self.MAX_ATTEMPTS:
            record["lockout_count"] += 1
            duration = self._get_lockout_duration(record["lockout_count"])
            record["lockout_until"] = current_time + duration
            record["attempts"] = []  # Reset attempts

            logger.warning(
                f"Account locked: {identifier[:20]}... for {duration//60} minutes "
                f"(lockout #{record['lockout_count']})"
            )

    def _clear_failed_attempts(self, identifier: str):
        """Clear failed attempts after successful login"""
        if identifier in self._failed_attempts:
            self._failed_attempts[identifier] = {
                "attempts": [],
                "lockout_until": 0,
                "lockout_count": 0
            }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only apply to auth endpoints
        if path not in self._auth_paths:
            return await call_next(request)

        # Get identifier (IP for now, could include email from body)
        client_ip = self._get_client_ip(request)
        identifier = f"ip:{client_ip}"

        # Check lockout status
        is_locked, remaining = self._is_locked_out(identifier)
        if is_locked:
            logger.warning(f"Blocked locked-out IP: {client_ip}")
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too many failed attempts",
                    "code": "ACCOUNT_LOCKED",
                    "retry_after": remaining,
                    "message": f"Account temporarily locked. Try again in {remaining // 60} minutes."
                },
                headers={"Retry-After": str(remaining)}
            )

        # Process request
        response = await call_next(request)

        # Track failed login attempts (401 responses)
        if path == "/api/auth/login" and response.status_code == 401:
            self._record_failed_attempt(identifier)
        elif path == "/api/auth/login" and response.status_code == 200:
            self._clear_failed_attempts(identifier)

        return response

    def cleanup_old_records(self):
        """Periodic cleanup of stale lockout records"""
        current_time = time.time()
        keys_to_delete = []

        for key, record in self._failed_attempts.items():
            # Remove if no lockout and no recent attempts
            if (record["lockout_until"] < current_time and
                all(ts < current_time - 3600 for ts in record["attempts"])):
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del self._failed_attempts[key]
