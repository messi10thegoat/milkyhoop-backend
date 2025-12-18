"""
QR Token Service
Handles QR login token lifecycle: generate, scan, approve, expire

Flow:
1. Desktop calls generate_token() -> returns QR data
2. Mobile scans QR, calls scan_token() -> status becomes "scanned"
3. Mobile calls approve_login() -> status becomes "approved"/"rejected"
4. Desktop receives tokens via WebSocket and logs in
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from pydantic import BaseModel

from backend.api_gateway.libs.milkyhoop_prisma import Prisma
from backend.api_gateway.app.services.websocket_hub import websocket_hub

logger = logging.getLogger(__name__)

# QR Token TTL (2 minutes)
QR_TOKEN_TTL_SECONDS = 120


class QRTokenData(BaseModel):
    """Data returned when generating a QR token"""

    token: str
    qr_url: str  # milkyhoop://login?token=xxx
    expires_at: datetime
    ttl_seconds: int


class QRTokenStatus(BaseModel):
    """Status of a QR token"""

    status: str  # pending, scanned, approved, rejected, expired
    is_expired: bool
    approved_by_user_id: Optional[str] = None
    approved_by_tenant_id: Optional[str] = None


class QRTokenService:
    """
    Service for managing QR login tokens
    """

    def __init__(self, prisma: Prisma):
        self.prisma = prisma

    async def generate_token(
        self,
        web_fingerprint: Optional[str] = None,
        web_user_agent: Optional[str] = None,
        web_ip: Optional[str] = None,
        browser_id: Optional[str] = None,
    ) -> QRTokenData:
        """
        Generate a new QR login token for desktop

        Args:
            web_fingerprint: Browser fingerprint for security validation
            web_user_agent: Browser user agent string
            web_ip: Client IP address
            browser_id: Browser profile ID for single session enforcement

        Returns:
            QRTokenData with token, QR URL, and expiration info
        """
        # Generate secure random token (32 chars)
        token = secrets.token_urlsafe(24)  # ~32 chars

        # Calculate expiration
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=QR_TOKEN_TTL_SECONDS
        )

        # Store in database
        try:
            await self.prisma.qrlogintoken.create(
                data={
                    "token": token,
                    "status": "pending",
                    "webFingerprint": web_fingerprint,
                    "webUserAgent": web_user_agent,
                    "webIp": web_ip,
                    "browserId": browser_id,
                    "expiresAt": expires_at,
                }
            )
            logger.info(
                f"QR token generated: {token[:8]}... browser={browser_id[:8] if browser_id else 'N/A'}..."
            )
        except Exception as e:
            logger.error(f"Failed to create QR token: {e}")
            raise

        return QRTokenData(
            token=token,
            qr_url=f"milkyhoop://login?token={token}",
            expires_at=expires_at,
            ttl_seconds=QR_TOKEN_TTL_SECONDS,
        )

    async def check_status(self, token: str) -> Optional[QRTokenStatus]:
        """
        Check current status of a QR token (called by desktop polling)

        Returns:
            QRTokenStatus or None if token not found
        """
        try:
            qr_token = await self.prisma.qrlogintoken.find_unique(
                where={"token": token}
            )

            if not qr_token:
                return None

            # Check if expired
            is_expired = datetime.now(timezone.utc) > qr_token.expiresAt.replace(
                tzinfo=timezone.utc
            )
            status = (
                "expired"
                if is_expired and qr_token.status == "pending"
                else qr_token.status
            )

            return QRTokenStatus(
                status=status,
                is_expired=is_expired,
                approved_by_user_id=qr_token.approvedByUserId,
                approved_by_tenant_id=qr_token.approvedByTenantId,
            )
        except Exception as e:
            logger.error(f"Failed to check QR token status: {e}")
            return None

    async def scan_token(self, token: str, user_id: str) -> Tuple[bool, str]:
        """
        Mobile scans QR code - updates status to "scanned"

        Args:
            token: The QR token from scanned code
            user_id: ID of user who scanned (from mobile session)

        Returns:
            (success, message)
        """
        try:
            qr_token = await self.prisma.qrlogintoken.find_unique(
                where={"token": token}
            )

            if not qr_token:
                return False, "QR code tidak valid"

            # Check expiration
            if datetime.now(timezone.utc) > qr_token.expiresAt.replace(
                tzinfo=timezone.utc
            ):
                return False, "QR code sudah kadaluarsa"

            # Check status - only pending can be scanned
            if qr_token.status != "pending":
                return False, f"QR code sudah digunakan (status: {qr_token.status})"

            # Update status to scanned
            await self.prisma.qrlogintoken.update(
                where={"token": token}, data={"status": "scanned"}
            )

            # Notify desktop via WebSocket
            await websocket_hub.send_to_qr(
                token,
                {
                    "event": "scanned",
                    "message": "QR code berhasil di-scan. Menunggu konfirmasi...",
                },
            )

            logger.info(f"QR token scanned: {token[:8]}... by user {user_id[:8]}...")
            return True, "QR code berhasil di-scan"

        except Exception as e:
            logger.error(f"Failed to scan QR token: {e}")
            return False, "Gagal memproses QR code"

    async def approve_login(
        self, token: str, user_id: str, tenant_id: str, approved: bool
    ) -> Tuple[bool, str, Optional[dict]]:
        """
        Mobile approves or rejects the QR login

        Args:
            token: The QR token
            user_id: ID of user approving
            tenant_id: Tenant ID of user approving
            approved: True to approve, False to reject

        Returns:
            (success, message, tokens_if_approved)
        """
        try:
            qr_token = await self.prisma.qrlogintoken.find_unique(
                where={"token": token}
            )

            if not qr_token:
                return False, "QR code tidak valid", None

            # Check expiration
            if datetime.now(timezone.utc) > qr_token.expiresAt.replace(
                tzinfo=timezone.utc
            ):
                return False, "QR code sudah kadaluarsa", None

            # Check status - only scanned can be approved
            if qr_token.status != "scanned":
                return (
                    False,
                    f"QR code belum di-scan atau sudah diproses (status: {qr_token.status})",
                    None,
                )

            new_status = "approved" if approved else "rejected"

            # Update token status
            await self.prisma.qrlogintoken.update(
                where={"token": token},
                data={
                    "status": new_status,
                    "approvedByUserId": user_id,
                    "approvedByTenantId": tenant_id,
                    "approvedAt": datetime.now(timezone.utc),
                },
            )

            if approved:
                # Generate tokens for desktop session
                # This will be handled by the router which has access to auth_client
                tokens = await self._generate_web_session_tokens(
                    user_id, tenant_id, qr_token
                )

                # Notify desktop via WebSocket (include device_id for force-logout WebSocket)
                await websocket_hub.send_to_qr(
                    token,
                    {
                        "event": "approved",
                        "message": "Login disetujui!",
                        "access_token": tokens["access_token"],
                        "refresh_token": tokens["refresh_token"],
                        "device_id": tokens.get(
                            "device_id"
                        ),  # For device WebSocket connection
                        "user": tokens.get("user"),
                    },
                )

                logger.info(
                    f"QR login approved: {token[:8]}... device {tokens.get('device_id', 'N/A')[:8]}... by user {user_id[:8]}..."
                )
                return True, "Login berhasil disetujui", tokens
            else:
                # Notify desktop of rejection
                await websocket_hub.send_to_qr(
                    token,
                    {"event": "rejected", "message": "Login ditolak oleh pengguna"},
                )

                logger.info(
                    f"QR login rejected: {token[:8]}... by user {user_id[:8]}..."
                )
                return True, "Login ditolak", None

        except Exception as e:
            logger.error(f"Failed to approve QR login: {e}")
            return False, "Gagal memproses persetujuan", None

    async def _generate_web_session_tokens(
        self, user_id: str, tenant_id: str, qr_token
    ) -> dict:
        """
        Generate JWT tokens for the new web session and register the device.

        This enforces BROWSER-LEVEL single session:
        - Uses browser_id to identify browser profile
        - When same browser_id logs in again, OLD session is kicked
        - Different browsers (incognito, other browser) = different sessions allowed
        - This is WhatsApp-style single session enforcement

        IMPORTANT: Device is registered FIRST to get device_id, then tokens are
        generated WITH device_id included. This ensures remote scanner can work.
        """
        # Import here to avoid circular imports
        from backend.api_gateway.app.services.auth_instance import auth_client
        from backend.api_gateway.app.services.device_service import DeviceService
        import uuid

        try:
            # Get user details
            user = await self.prisma.user.find_unique(
                where={"id": user_id}, include={"tenant": True}
            )

            if not user:
                raise ValueError(f"User not found: {user_id}")

            device_info = f"Web - {qr_token.webUserAgent[:50] if qr_token.webUserAgent else 'Unknown'}"

            # Get browser_id from QR token (or generate fallback)
            browser_id = qr_token.browserId or str(uuid.uuid4())
            if not qr_token.browserId:
                logger.warning(
                    f"No browser_id in QR token, generated fallback: {browser_id[:8]}..."
                )

            # STEP 1: Register web device FIRST to get device_id
            # DeviceService.register_device() will kick same browser_id sessions
            device_service = DeviceService(self.prisma)
            device_id = await device_service.register_device(
                user_id=user_id,
                tenant_id=tenant_id,
                device_type="web",
                browser_id=browser_id,
                device_name=device_info,
                device_fingerprint=qr_token.webFingerprint,
                user_agent=qr_token.webUserAgent,
                refresh_token_hash=None,  # Will update after token generation
                ip_address=qr_token.webIp,
            )

            logger.info(
                f"Web device {device_id[:8]}... registered for user {user_id[:8]}... browser={browser_id[:8]}..."
            )

            # STEP 2: Generate tokens WITH device_id included
            response = await auth_client.generate_tokens_for_qr_login(
                user_id=user_id,
                tenant_id=tenant_id,
                email=user.email,
                role=user.role,
                username=user.username,
                device_info=device_info,
                device_id=device_id,
                device_type="web",
            )

            # STEP 3: Update device with refresh_token_hash
            if response.refresh_token:
                await self.prisma.userdevice.update(
                    where={"id": device_id},
                    data={
                        "refreshTokenHash": device_service.hash_refresh_token(
                            response.refresh_token
                        )
                    },
                )

            return {
                "access_token": response.access_token,
                "refresh_token": response.refresh_token,
                "device_id": device_id,  # Return device_id for WebSocket connection
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "role": user.role,
                    "tenant_id": tenant_id,
                    "tenant_alias": user.tenant.alias if user.tenant else None,
                },
            }
        except Exception as e:
            logger.error(f"Failed to generate web session tokens: {e}")
            raise

    async def cleanup_expired(self) -> int:
        """
        Delete expired QR tokens (cron job)

        Returns:
            Number of tokens deleted
        """
        try:
            result = await self.prisma.execute_raw("SELECT cleanup_expired_qr_tokens()")
            count = result[0]["cleanup_expired_qr_tokens"] if result else 0
            if count > 0:
                logger.info(f"Cleaned up {count} expired QR tokens")
            return count
        except Exception as e:
            logger.error(f"Failed to cleanup expired QR tokens: {e}")
            return 0


# Service factory function
def get_qr_token_service(prisma: Prisma) -> QRTokenService:
    """Factory function to get QR token service instance"""
    return QRTokenService(prisma)
