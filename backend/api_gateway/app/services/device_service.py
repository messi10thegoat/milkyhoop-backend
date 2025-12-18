"""
Device Service
Manages linked devices for QR Login system

Features:
- Create/register new device
- List user's linked devices
- Logout specific device
- Logout all web devices (cascade logout)

Session Enforcement:
- 1 User = 1 Web Session TOTAL (not per browser)
- Any new login kicks out ALL existing web sessions
- browser_id is for identification only, NOT enforcement
"""
import asyncio
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from pydantic import BaseModel

from backend.api_gateway.libs.milkyhoop_prisma import Prisma
from backend.api_gateway.app.services.websocket_hub import websocket_hub
from backend.api_gateway.app.services.session_manager import session_manager

logger = logging.getLogger(__name__)

# Web session expires after 30 days of inactivity
WEB_SESSION_TTL_DAYS = 30

# Grace period for clients to react to force_logout
GRACE_SECONDS = 0.2

# Max retries for race condition handling
MAX_RETRIES = 2


class DeviceInfo(BaseModel):
    """Device information model"""

    id: str
    device_type: str  # 'mobile' | 'web'
    device_name: Optional[str]
    is_active: bool
    is_primary: bool
    is_current: bool  # True if this is the device making the request
    last_active_at: datetime
    created_at: datetime


class DeviceService:
    """
    Service for managing user devices
    """

    def __init__(self, prisma: Prisma):
        self.prisma = prisma

    async def register_device(
        self,
        user_id: str,
        tenant_id: str,
        device_type: str,  # 'mobile' | 'web'
        browser_id: str,  # Browser profile ID (for identification only)
        device_name: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
        user_agent: Optional[str] = None,
        refresh_token_hash: Optional[str] = None,
        ip_address: Optional[str] = None,
        device_id: Optional[
            str
        ] = None,  # Pre-generated device_id for consistent ID across JWT/Redis/WS
    ) -> str:
        """
        Register a new device for a user (1 USER = 1 WEB SESSION TOTAL)

        For web devices: Only 1 active session per user/tenant (regardless of browser)
        - ANY new login kicks out ALL existing web sessions
        - browser_id is for identification/logging only, NOT enforcement
        - WebSocket force_logout sent FIRST (side-effect), then DB transaction

        For mobile devices: Mark as primary

        Returns:
            device_id
        """
        # For web devices: kick ALL existing web sessions (not just same browser_id)
        if device_type == "web":
            # 1) Find ALL active web sessions (NO browser_id filter!)
            existing_sessions = await self.prisma.userdevice.find_many(
                where={
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "deviceType": "web",
                    "isActive": True,
                }
            )

            # 2) Notify via WebSocket FIRST (side-effect OUTSIDE transaction)
            if existing_sessions:
                logger.warning(
                    f"🔄 New login: found {len(existing_sessions)} existing web sessions for user={user_id[:8]}... - kicking ALL"
                )
                for existing in existing_sessions:
                    try:
                        tabs_notified = await websocket_hub.force_logout_device(
                            existing.id, "Session digantikan oleh login baru"
                        )
                        logger.info(
                            f"Force logout sent to {tabs_notified} tabs for device {existing.id[:8]}..."
                        )
                    except Exception as e:
                        logger.exception(
                            f"[Device] WS force_logout failed for {existing.id[:8]}...: {e}"
                        )

                # Grace period for clients to react
                await asyncio.sleep(GRACE_SECONDS)

            # 3) DB changes with retry for race condition
            attempt = 0
            while attempt < MAX_RETRIES:
                attempt += 1
                try:
                    # Deactivate all existing sessions
                    for existing in existing_sessions:
                        try:
                            await self.prisma.userdevice.update(
                                where={"id": existing.id},
                                data={
                                    "isActive": False,
                                    "expiresAt": datetime.now(timezone.utc),
                                },
                            )
                            # Revoke refresh token
                            if existing.refreshTokenHash:
                                try:
                                    await self.prisma.refreshtoken.update_many(
                                        where={"tokenHash": existing.refreshTokenHash},
                                        data={"revokedAt": datetime.now(timezone.utc)},
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            logger.debug(
                                f"[Device] deactivate ignore for {existing.id[:8]}..."
                            )

                    # Create new device
                    expires_at = datetime.now(timezone.utc) + timedelta(
                        days=WEB_SESSION_TTL_DAYS
                    )
                    # Build create data - include device_id if provided
                    create_data = {
                        "userId": user_id,
                        "tenantId": tenant_id,
                        "deviceType": device_type,
                        "browserId": browser_id,  # For identification only
                        "deviceName": device_name
                        or self._generate_device_name(user_agent),
                        "deviceFingerprint": device_fingerprint,
                        "userAgent": user_agent,
                        "refreshTokenHash": refresh_token_hash,
                        "isActive": True,
                        "isPrimary": False,
                        "lastIp": ip_address,
                        "expiresAt": expires_at,
                    }
                    # Use provided device_id as DB record ID for consistent ID across JWT/Redis/WS
                    if device_id:
                        create_data["id"] = device_id
                    device = await self.prisma.userdevice.create(data=create_data)

                    logger.info(
                        f"✅ Device registered: {device.id[:8]}... (web) browser={browser_id[:8]}... for user {user_id[:8]}..."
                    )

                    # CRITICAL: Set session in Redis for auth middleware validation
                    session_manager.set_active_device(user_id, device_type, device.id)
                    logger.info(
                        f"✅ Session set in Redis: user={user_id[:8]}... device_type={device_type} device_id={device.id[:8]}..."
                    )

                    return device.id

                except Exception as e:
                    logger.warning(f"[Device] Retry {attempt}/{MAX_RETRIES}: {e}")
                    # Re-fetch for retry
                    existing_sessions = await self.prisma.userdevice.find_many(
                        where={
                            "userId": user_id,
                            "tenantId": tenant_id,
                            "deviceType": "web",
                            "isActive": True,
                        }
                    )
                    if attempt >= MAX_RETRIES:
                        logger.error(f"[Device] Failed after {MAX_RETRIES} retries")
                        raise
                    await asyncio.sleep(0.1 * attempt)

            raise RuntimeError("Failed to register device after retries")

        else:
            # Mobile device registration with SINGLE SESSION ENFORCEMENT + CASCADE
            # 1 User = 1 Mobile Session + CASCADE kicks all web sessions

            # 1) Find ALL active mobile sessions
            existing_mobile = await self.prisma.userdevice.find_many(
                where={
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "deviceType": "mobile",
                    "isActive": True,
                }
            )

            # 2) Find ALL active web sessions (for CASCADE logout)
            existing_web = await self.prisma.userdevice.find_many(
                where={
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "deviceType": "web",
                    "isActive": True,
                }
            )

            all_existing = existing_mobile + existing_web

            # 3) Notify ALL via WebSocket FIRST (side-effect OUTSIDE transaction)
            if all_existing:
                logger.warning(
                    f"🔄 Mobile login: kicking {len(existing_mobile)} mobile + {len(existing_web)} web sessions for user={user_id[:8]}..."
                )
                for existing in existing_mobile:
                    try:
                        tabs_notified = await websocket_hub.force_logout_device(
                            existing.id, "Session digantikan oleh login baru"
                        )
                        logger.info(
                            f"Force logout sent to {tabs_notified} tabs for mobile device {existing.id[:8]}..."
                        )
                    except Exception as e:
                        logger.exception(
                            f"[Device] WS force_logout failed for mobile {existing.id[:8]}...: {e}"
                        )

                for existing in existing_web:
                    try:
                        tabs_notified = await websocket_hub.force_logout_device(
                            existing.id, "Sesi web dihentikan karena login mobile baru"
                        )
                        logger.info(
                            f"Force logout sent to {tabs_notified} tabs for web device {existing.id[:8]}..."
                        )
                    except Exception as e:
                        logger.exception(
                            f"[Device] WS force_logout failed for web {existing.id[:8]}...: {e}"
                        )

                # Grace period for clients to react
                await asyncio.sleep(GRACE_SECONDS)

            # 4) Deactivate ALL existing sessions (mobile + web)
            for existing in all_existing:
                try:
                    await self.prisma.userdevice.update(
                        where={"id": existing.id},
                        data={
                            "isActive": False,
                            "expiresAt": datetime.now(timezone.utc),
                        },
                    )
                    # Revoke refresh token
                    if existing.refreshTokenHash:
                        try:
                            await self.prisma.refreshtoken.update_many(
                                where={"tokenHash": existing.refreshTokenHash},
                                data={"revokedAt": datetime.now(timezone.utc)},
                            )
                        except Exception:
                            pass
                except Exception:
                    logger.debug(f"[Device] deactivate ignore for {existing.id[:8]}...")

            # 5) Create new mobile device
            try:
                # Build create data - include device_id if provided
                create_data = {
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "deviceType": device_type,
                    "browserId": browser_id,
                    "deviceName": device_name or self._generate_device_name(user_agent),
                    "deviceFingerprint": device_fingerprint,
                    "userAgent": user_agent,
                    "refreshTokenHash": refresh_token_hash,
                    "isActive": True,
                    "isPrimary": True,
                    "lastIp": ip_address,
                    "expiresAt": None,  # Mobile doesn't expire
                }
                # Use provided device_id as DB record ID for consistent ID across JWT/Redis/WS
                if device_id:
                    create_data["id"] = device_id
                device = await self.prisma.userdevice.create(data=create_data)

                logger.info(
                    f"✅ Device registered: {device.id[:8]}... (mobile) for user {user_id[:8]}..."
                )
                return device.id

            except Exception as e:
                logger.error(f"Failed to register mobile device: {e}")
                raise

    async def list_devices(
        self, user_id: str, tenant_id: str, current_device_id: Optional[str] = None
    ) -> List[DeviceInfo]:
        """
        List all active devices for a user

        Args:
            user_id: User ID
            tenant_id: Tenant ID
            current_device_id: ID of device making the request (for is_current flag)

        Returns:
            List of DeviceInfo
        """
        try:
            devices = await self.prisma.userdevice.find_many(
                where={"userId": user_id, "tenantId": tenant_id, "isActive": True},
                order_by={"lastActiveAt": "desc"},
            )

            return [
                DeviceInfo(
                    id=d.id,
                    device_type=d.deviceType,
                    device_name=d.deviceName,
                    is_active=d.isActive,
                    is_primary=d.isPrimary,
                    is_current=(d.id == current_device_id),
                    last_active_at=d.lastActiveAt,
                    created_at=d.createdAt,
                )
                for d in devices
            ]

        except Exception as e:
            logger.error(f"Failed to list devices: {e}")
            return []

    async def get_mobile_device(self, user_id: str, tenant_id: str):
        """
        Get the active mobile device for a user (for Remote Scanner)

        Returns:
            Mobile device record or None
        """
        try:
            device = await self.prisma.userdevice.find_first(
                where={
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "deviceType": "mobile",
                    "isActive": True,
                }
            )
            return device
        except Exception as e:
            logger.error(f"Failed to get mobile device: {e}")
            return None

    async def logout_device(
        self, device_id: str, user_id: str, tenant_id: str, cascade: bool = True
    ) -> bool:
        """
        Logout a specific device

        Args:
            device_id: ID of device to logout
            user_id: User ID (for authorization)
            tenant_id: Tenant ID (for authorization)
            cascade: If True and device is mobile, also logout all web sessions

        Returns:
            True if successful
        """
        try:
            # Verify device belongs to user
            device = await self.prisma.userdevice.find_first(
                where={
                    "id": device_id,
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "isActive": True,
                }
            )

            if not device:
                logger.warning(f"Device not found or unauthorized: {device_id[:8]}...")
                return False

            # If mobile (primary) device logs out, CASCADE logout all web sessions first
            if device.isPrimary and device.deviceType == "mobile" and cascade:
                logger.warning(
                    f"🔴 Mobile logout: cascading to all web sessions for user {user_id[:8]}..."
                )
                await self.logout_all_web_devices(user_id, tenant_id)

            # Notify device via WebSocket FIRST
            tabs_notified = await websocket_hub.force_logout_device(
                device_id, "Session dihentikan oleh pengguna"
            )
            logger.info(
                f"Force logout sent to {tabs_notified} tabs for device {device_id[:8]}..."
            )

            # Deactivate device
            await self.prisma.userdevice.update(
                where={"id": device_id},
                data={"isActive": False, "expiresAt": datetime.now(timezone.utc)},
            )

            # Revoke refresh token if exists
            if device.refreshTokenHash:
                try:
                    await self.prisma.refreshtoken.update_many(
                        where={"tokenHash": device.refreshTokenHash},
                        data={"revokedAt": datetime.now(timezone.utc)},
                    )
                except Exception:
                    pass  # Token might not exist

            logger.info(f"Device logged out: {device_id[:8]}...")
            return True

        except Exception as e:
            logger.error(f"Failed to logout device: {e}")
            return False

    async def logout_all_web_devices(self, user_id: str, tenant_id: str) -> int:
        """
        Logout all web devices for a user (cascade logout)
        Called from mobile when user wants to logout all web sessions

        Returns:
            Number of devices logged out
        """
        try:
            # Get all active web devices
            devices = await self.prisma.userdevice.find_many(
                where={
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "deviceType": "web",
                    "isActive": True,
                }
            )

            count = 0
            for device in devices:
                # Notify via WebSocket FIRST (before deactivating)
                tabs_notified = await websocket_hub.force_logout_device(
                    device.id, "Semua sesi web dihentikan"
                )
                logger.info(
                    f"Force logout sent to {tabs_notified} tabs for device {device.id[:8]}..."
                )

                # Then deactivate
                await self.prisma.userdevice.update(
                    where={"id": device.id},
                    data={"isActive": False, "expiresAt": datetime.now(timezone.utc)},
                )

                # Revoke refresh token
                if device.refreshTokenHash:
                    try:
                        await self.prisma.refreshtoken.update_many(
                            where={"tokenHash": device.refreshTokenHash},
                            data={"revokedAt": datetime.now(timezone.utc)},
                        )
                    except Exception:
                        pass

                count += 1

            logger.info(f"Logged out {count} web devices for user {user_id[:8]}...")
            return count

        except Exception as e:
            logger.error(f"Failed to logout all web devices: {e}")
            return 0

    async def update_device_activity(
        self, device_id: str, ip_address: Optional[str] = None
    ) -> None:
        """
        Update device last activity timestamp
        Called on each authenticated request
        """
        try:
            await self.prisma.userdevice.update(
                where={"id": device_id},
                data={"lastActiveAt": datetime.now(timezone.utc), "lastIp": ip_address},
            )
        except Exception as e:
            # Non-critical error, just log
            logger.debug(f"Failed to update device activity: {e}")

    async def cleanup_expired_devices(self) -> int:
        """
        Cleanup expired web device sessions
        Should be called via cron job

        Returns:
            Number of devices deactivated
        """
        try:
            result = await self.prisma.userdevice.update_many(
                where={
                    "deviceType": "web",
                    "isActive": True,
                    "expiresAt": {"lt": datetime.now(timezone.utc)},
                },
                data={"isActive": False},
            )

            if result.count > 0:
                logger.info(f"Cleaned up {result.count} expired web devices")

            return result.count

        except Exception as e:
            logger.error(f"Failed to cleanup expired devices: {e}")
            return 0

    def _generate_device_name(self, user_agent: Optional[str]) -> str:
        """Generate a human-readable device name from user agent"""
        if not user_agent:
            return "Unknown Device"

        ua_lower = user_agent.lower()

        # Detect browser
        browser = "Browser"
        if "chrome" in ua_lower:
            browser = "Chrome"
        elif "firefox" in ua_lower:
            browser = "Firefox"
        elif "safari" in ua_lower:
            browser = "Safari"
        elif "edge" in ua_lower:
            browser = "Edge"

        # Detect OS
        os_name = "Desktop"
        if "windows" in ua_lower:
            os_name = "Windows"
        elif "mac" in ua_lower:
            os_name = "Mac"
        elif "linux" in ua_lower:
            os_name = "Linux"
        elif "android" in ua_lower:
            os_name = "Android"
        elif "iphone" in ua_lower or "ipad" in ua_lower:
            os_name = "iOS"

        return f"{browser} - {os_name}"

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        """Hash a refresh token for secure storage"""
        return hashlib.sha256(token.encode()).hexdigest()


# Service factory function
def get_device_service(prisma: Prisma) -> DeviceService:
    """Factory function to get device service instance"""
    return DeviceService(prisma)
