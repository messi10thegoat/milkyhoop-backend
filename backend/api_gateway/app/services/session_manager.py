"""
Session Manager - Enterprise Single Session Enforcement
Redis-based session authority layer (WhatsApp-style)

PRINCIPLE: JWT != Session. Session authority = SERVER STATE (Redis)

Key Structure:
    session:{user_id}:mobile = device_id
    session:{user_id}:web    = device_id

Rules:
    - Mobile login  → set_active_device(mobile) + revoke_device(web)
    - Desktop login → set_active_device(web)
    - Mobile logout → revoke_all()
    - Desktop logout → revoke_device(web)
    - Auth check   → get_active_device()
"""

import redis
import structlog
from typing import Optional, Literal
import os

logger = structlog.get_logger(__name__)

DeviceType = Literal["mobile", "web"]


class SessionManager:
    """
    Redis session authority - single source of truth for active sessions.

    This is the "session switch" - all auth decisions go through here.
    DO NOT bypass this by querying user_devices directly for auth.
    """

    # TTL = refresh_token_expiry + buffer (8 days)
    TTL_SECONDS = 8 * 24 * 60 * 60

    def __init__(self):
        self.redis_host = os.getenv("REDIS_HOST", "redis")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_password = os.getenv("REDIS_PASSWORD", "")  # Empty for dev

        try:
            # Connect with or without password based on environment
            redis_kwargs = {
                "host": self.redis_host,
                "port": self.redis_port,
                "decode_responses": True,
            }
            if self.redis_password:
                redis_kwargs["password"] = self.redis_password

            self.redis = redis.Redis(**redis_kwargs)
            # Test connection
            self.redis.ping()
            logger.info(
                "✅ SessionManager connected to Redis",
                host=self.redis_host,
                port=self.redis_port,
            )
        except Exception as e:
            logger.error("❌ Redis connection failed", error=str(e))
            self.redis = None

    def _key(self, user_id: str, device_type: DeviceType) -> str:
        """
        Generate Redis key for session.
        Format: session:{user_id}:{device_type}

        DO NOT use token hash, browser_id, or fingerprint here.
        """
        return f"session:{user_id}:{device_type}"

    def set_active_device(
        self, user_id: str, device_type: DeviceType, device_id: str
    ) -> bool:
        """
        Set the active device for a user/device_type combination.

        This is an ATOMIC REPLACE operation:
        - Any previous device_id for this user+device_type is automatically replaced
        - The old device becomes unauthorized IMMEDIATELY

        Args:
            user_id: User ID
            device_type: "mobile" or "web"
            device_id: The new active device ID

        Returns:
            True if successful, False otherwise
        """
        if not self.redis:
            logger.error("❌ Redis not available - cannot set active device")
            return False

        try:
            key = self._key(user_id, device_type)
            self.redis.set(key, device_id, ex=self.TTL_SECONDS)
            logger.info(
                f"✅ Session set: {key} = {device_id[:8]}...",
                user_id=user_id[:8],
                device_type=device_type,
                device_id=device_id[:8],
            )
            return True
        except Exception as e:
            logger.error("❌ Failed to set active device", error=str(e))
            return False

    def get_active_device(self, user_id: str, device_type: DeviceType) -> Optional[str]:
        """
        Get the currently active device_id for a user/device_type.

        This is the AUTHORITATIVE check for session validity.
        If the device_id in JWT != this value, the session is REVOKED.

        Args:
            user_id: User ID
            device_type: "mobile" or "web"

        Returns:
            device_id if session exists, None otherwise
        """
        if not self.redis:
            logger.error("❌ Redis not available - cannot get active device")
            return None

        try:
            key = self._key(user_id, device_type)
            device_id = self.redis.get(key)
            return device_id
        except Exception as e:
            logger.error("❌ Failed to get active device", error=str(e))
            return None

    def revoke_device(self, user_id: str, device_type: DeviceType) -> bool:
        """
        Revoke a specific device session.

        After this call, any request with this device_type will be rejected.

        Args:
            user_id: User ID
            device_type: "mobile" or "web"

        Returns:
            True if successful, False otherwise
        """
        if not self.redis:
            logger.error("❌ Redis not available - cannot revoke device")
            return False

        try:
            key = self._key(user_id, device_type)
            result = self.redis.delete(key)
            logger.info(
                f"✅ Session revoked: {key}",
                user_id=user_id[:8],
                device_type=device_type,
            )
            return bool(result)
        except Exception as e:
            logger.error("❌ Failed to revoke device", error=str(e))
            return False

    def revoke_all(self, user_id: str) -> bool:
        """
        Revoke ALL sessions for a user (mobile + web).

        Used when:
        - Mobile user logs out (cascade kill all)
        - Admin force logout
        - Account security event

        Args:
            user_id: User ID

        Returns:
            True if successful, False otherwise
        """
        if not self.redis:
            logger.error("❌ Redis not available - cannot revoke all")
            return False

        try:
            # Atomic delete both keys
            pipe = self.redis.pipeline(transaction=True)
            pipe.delete(self._key(user_id, "mobile"))
            pipe.delete(self._key(user_id, "web"))
            pipe.execute()

            logger.info(
                f"✅ All sessions revoked for user {user_id[:8]}...",
                user_id=user_id[:8],
            )
            return True
        except Exception as e:
            logger.error("❌ Failed to revoke all sessions", error=str(e))
            return False

    def is_session_valid(
        self, user_id: str, device_type: DeviceType, device_id: str
    ) -> bool:
        """
        Check if a session is valid.

        This is the KILL SWITCH for unauthorized sessions.

        Args:
            user_id: User ID from JWT
            device_type: Device type from JWT
            device_id: Device ID from JWT

        Returns:
            True if session is valid, False if revoked/replaced
        """
        active_device_id = self.get_active_device(user_id, device_type)

        if active_device_id is None:
            logger.warning(
                f"⚠️ No active session for {user_id[:8]}:{device_type}",
                user_id=user_id[:8],
                device_type=device_type,
            )
            return False

        if active_device_id != device_id:
            logger.warning(
                f"🚫 Session mismatch: expected {active_device_id[:8]}..., got {device_id[:8]}...",
                user_id=user_id[:8],
                device_type=device_type,
                expected=active_device_id[:8],
                got=device_id[:8],
            )
            return False

        return True

    def activate_mobile_device(self, user_id: str, device_id: str) -> bool:
        """
        ATOMIC: Set mobile session + revoke web session in single transaction.
        No race condition window.

        Called when mobile user logs in - cascade kills web session.

        Args:
            user_id: User ID
            device_id: New mobile device ID

        Returns:
            True if successful, False otherwise
        """
        if not self.redis:
            logger.error("❌ Redis not available - cannot activate mobile device")
            return False

        try:
            pipe = self.redis.pipeline(transaction=True)
            pipe.set(self._key(user_id, "mobile"), device_id, ex=self.TTL_SECONDS)
            pipe.delete(self._key(user_id, "web"))
            pipe.execute()

            logger.info(
                f"✅ Mobile device activated (atomic): user={user_id[:8]}..., device={device_id[:8]}...",
                user_id=user_id[:8],
                device_id=device_id[:8],
            )
            return True
        except Exception as e:
            logger.error("❌ activate_mobile_device failed", error=str(e))
            return False

    def activate_web_device(self, user_id: str, device_id: str) -> bool:
        """
        Set web session (does NOT affect mobile session).

        Called when desktop user logs in via QR.

        Args:
            user_id: User ID
            device_id: New web device ID

        Returns:
            True if successful, False otherwise
        """
        return self.set_active_device(user_id, "web", device_id)

    def health_check(self) -> bool:
        """Check Redis connectivity"""
        try:
            if self.redis:
                self.redis.ping()
                return True
        except:
            pass
        return False


# Singleton instance
session_manager = SessionManager()
