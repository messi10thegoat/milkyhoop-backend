"""
WebSocket Hub Service
Manages WebSocket connections for QR Login, device communication, and Remote Scanner

Connections are managed in two pools:
1. qr_connections: token -> WebSocket (for QR login flow)
2. device_connections: device_id -> tab_id -> WebSocket (for force logout + remote scan)
   - Multiple tabs can be connected per device_id
   - Force logout broadcasts to ALL tabs of a device
   - Remote scan: desktop triggers → mobile receives → mobile sends result

Remote Scanner Flow:
1. Desktop (web) sends remote_scan:request to mobile via WebSocket
2. Mobile opens camera, scans barcode
3. Mobile sends remote_scan:result back to desktop
4. Desktop receives barcode and processes it
"""
import logging
import asyncio
import time
from typing import Dict, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Remote scan request timeout (30 seconds - allows for mobile camera startup + scanning)
REMOTE_SCAN_TIMEOUT = 30


class WebSocketHub:
    """
    Singleton WebSocket connection manager
    Handles QR login status updates, device force logout, and remote scanner

    Device connections support multi-tab:
    - Each browser tab has unique tab_id (from sessionStorage)
    - Same device_id can have multiple tab connections
    - Force logout broadcasts to ALL tabs of a device

    Remote Scanner:
    - Paired desktop can trigger barcode scan on mobile
    - Uses same device_connections with remote_scan:* events
    - 1 mobile + 1 desktop per user (enforced by session management)
    """

    def __init__(self):
        # QR token -> WebSocket (desktop waiting for approval)
        self.qr_connections: Dict[str, WebSocket] = {}
        # Device ID -> Tab ID -> WebSocket (multi-tab support)
        self.device_connections: Dict[str, Dict[str, WebSocket]] = {}
        # Remote scan sessions: scan_id -> {device_id, tab_id, requested_at}
        self.remote_scan_sessions: Dict[str, dict] = {}
        # Lock for thread safety
        self._lock = asyncio.Lock()

    # ================================
    # QR LOGIN WEBSOCKET METHODS
    # ================================

    async def register_qr(self, token: str, websocket: WebSocket) -> None:
        """
        Register a WebSocket for QR login status updates
        Desktop browser connects to wait for mobile approval
        """
        async with self._lock:
            # Close any existing connection for this token
            if token in self.qr_connections:
                try:
                    await self.qr_connections[token].close(
                        code=1000, reason="New connection"
                    )
                except Exception:
                    pass
            self.qr_connections[token] = websocket
            logger.info(f"QR WebSocket registered for token: {token[:8]}...")

    async def unregister_qr(self, token: str) -> None:
        """
        Remove QR WebSocket connection
        Called when token expires, is approved, or connection closes
        """
        async with self._lock:
            if token in self.qr_connections:
                del self.qr_connections[token]
                logger.info(f"QR WebSocket unregistered for token: {token[:8]}...")

    async def send_to_qr(self, token: str, data: dict) -> bool:
        """
        Send status update to desktop browser waiting for QR approval

        Events:
        - {"event": "scanned", "message": "QR code scanned by mobile"}
        - {"event": "approved", "access_token": "...", "refresh_token": "..."}
        - {"event": "rejected", "message": "Login was rejected"}
        - {"event": "expired", "message": "QR code expired"}
        """
        async with self._lock:
            if token not in self.qr_connections:
                logger.warning(f"No QR WebSocket for token: {token[:8]}...")
                return False

            websocket = self.qr_connections[token]

        try:
            await websocket.send_json(data)
            logger.info(
                f"QR WebSocket message sent for token {token[:8]}...: {data.get('event')}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send QR WebSocket message: {e}")
            await self.unregister_qr(token)
            return False

    def is_qr_connected(self, token: str) -> bool:
        """Check if there's an active WebSocket for this QR token"""
        return token in self.qr_connections

    # ================================
    # DEVICE WEBSOCKET METHODS
    # ================================

    async def register_device(
        self, device_id: str, websocket: WebSocket, tab_id: str = "default"
    ) -> None:
        """
        Register a WebSocket for device-specific notifications
        Used to receive force logout commands from mobile

        Args:
            device_id: Device identifier (shared across tabs in localStorage)
            websocket: The WebSocket connection
            tab_id: Unique tab identifier (from sessionStorage, unique per tab)
        """
        async with self._lock:
            if device_id not in self.device_connections:
                self.device_connections[device_id] = {}

            # Close existing connection for this specific tab (if reconnecting)
            if tab_id in self.device_connections[device_id]:
                try:
                    await self.device_connections[device_id][tab_id].close(
                        code=1000, reason="Reconnect"
                    )
                except Exception:
                    pass

            self.device_connections[device_id][tab_id] = websocket
            total_tabs = sum(len(tabs) for tabs in self.device_connections.values())
            logger.warning(
                f"✅ Device WebSocket registered: {device_id[:8]}... tab={tab_id[:8]}... (tabs: {len(self.device_connections[device_id])}, total: {total_tabs})"
            )

    async def unregister_device(self, device_id: str, tab_id: str = "default") -> None:
        """Remove device WebSocket connection for a specific tab"""
        async with self._lock:
            if device_id in self.device_connections:
                if tab_id in self.device_connections[device_id]:
                    del self.device_connections[device_id][tab_id]
                    # Clean up empty device entry
                    if not self.device_connections[device_id]:
                        del self.device_connections[device_id]
                    total_tabs = sum(
                        len(tabs) for tabs in self.device_connections.values()
                    )
                    logger.warning(
                        f"❌ Device WebSocket unregistered: {device_id[:8]}... tab={tab_id[:8]}... (total: {total_tabs})"
                    )

    async def force_logout_device(
        self, device_id: str, reason: str = "Logged out remotely"
    ) -> int:
        """
        Send force logout command to ALL tabs of a specific device (RACE-SAFE)

        Called when:
        - Mobile user logs out all web sessions
        - Mobile user removes a specific web device
        - A new web session invalidates the old one (WhatsApp-style)

        Flow:
        1. Broadcast force_logout message to all tabs FIRST
        2. Wait grace period for clients to process
        3. Close WebSocket connections
        4. Clean up registry

        Returns:
            Number of tabs that received the force_logout message
        """
        tabs_to_logout = []

        async with self._lock:
            if device_id not in self.device_connections:
                logger.warning(f"No device WebSocket for: {device_id[:8]}...")
                return 0

            # Get all tabs for this device (copy to avoid mutation during iteration)
            tabs_to_logout = list(self.device_connections[device_id].items())
            logger.warning(
                f"🔴 Force logout broadcasting to {len(tabs_to_logout)} tabs for device {device_id[:8]}..."
            )

        # 1. Broadcast to all tabs FIRST (outside lock to avoid deadlock)
        success_count = 0
        for tab_id, websocket in tabs_to_logout:
            try:
                await websocket.send_json({"event": "force_logout", "reason": reason})
                logger.warning(
                    f"🔴 Force logout SENT to device {device_id[:8]}... tab={tab_id[:8]}..."
                )
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to send force logout to tab {tab_id[:8]}...: {e}")

        # 2. Grace period for clients to process logout
        await asyncio.sleep(0.2)

        # 3. Close connections and cleanup
        async with self._lock:
            if device_id in self.device_connections:
                for tab_id, websocket in list(
                    self.device_connections[device_id].items()
                ):
                    try:
                        await websocket.close(code=4001, reason="Session replaced")
                    except Exception:
                        pass  # Connection might already be closed

                # 4. Remove all tabs for this device
                del self.device_connections[device_id]
                total_tabs = sum(len(tabs) for tabs in self.device_connections.values())
                logger.warning(
                    f"🔴 Device {device_id[:8]}... cleanup complete (remaining: {total_tabs} tabs)"
                )

        return success_count

    async def force_logout_all_web_devices(
        self, user_id: str, tenant_id: str, reason: str = "All web sessions logged out"
    ) -> int:
        """
        Force logout all web devices for a user
        Called when mobile user clicks "Logout all web"

        Note: This requires mapping user_id -> device_ids which should be
        done via database query in the calling code
        """
        # This is a stub - actual implementation needs device_ids from DB
        logger.info(f"Force logout all web devices requested for user {user_id[:8]}...")
        return 0

    # ================================
    # UTILITY METHODS
    # ================================

    def get_stats(self) -> dict:
        """Get connection statistics for monitoring"""
        total_tabs = sum(len(tabs) for tabs in self.device_connections.values())
        return {
            "qr_connections": len(self.qr_connections),
            "device_connections": len(
                self.device_connections
            ),  # Number of unique devices
            "total_tabs": total_tabs,  # Total WebSocket connections (multi-tab)
            "active_remote_scans": len(self.remote_scan_sessions),
        }

    async def cleanup_stale_connections(self) -> int:
        """
        Clean up stale/dead connections
        Should be called periodically (e.g., every minute)
        """
        cleaned = 0

        # Check QR connections
        async with self._lock:
            stale_qr = []
            for token, ws in self.qr_connections.items():
                try:
                    # Try to ping - if fails, connection is dead
                    await ws.send_json({"event": "ping"})
                except Exception:
                    stale_qr.append(token)

            for token in stale_qr:
                del self.qr_connections[token]
                cleaned += 1

            # Check device connections (multi-tab structure)
            stale_tabs = []  # List of (device_id, tab_id) tuples
            for device_id, tabs in self.device_connections.items():
                for tab_id, ws in tabs.items():
                    try:
                        await ws.send_json({"event": "ping"})
                    except Exception:
                        stale_tabs.append((device_id, tab_id))

            # Remove stale tabs
            for device_id, tab_id in stale_tabs:
                if (
                    device_id in self.device_connections
                    and tab_id in self.device_connections[device_id]
                ):
                    del self.device_connections[device_id][tab_id]
                    # Clean up empty device entry
                    if not self.device_connections[device_id]:
                        del self.device_connections[device_id]
                    cleaned += 1

        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} stale WebSocket connections")

        return cleaned

    # ================================
    # REMOTE SCANNER METHODS
    # ================================

    async def send_remote_scan_request(
        self,
        mobile_device_id: str,
        scan_id: str,
        desktop_device_id: str,
        desktop_tab_id: str,
        tenant_id: str,
    ) -> bool:
        """
        Send remote scan request from desktop to mobile

        Args:
            mobile_device_id: Mobile device ID to send request to
            scan_id: Unique scan session ID
            desktop_device_id: Requesting desktop device ID (for result routing)
            desktop_tab_id: Requesting desktop tab ID
            tenant_id: Tenant ID for isolation (stored for validation on result)

        Returns:
            True if request was sent, False if mobile not connected
        """
        async with self._lock:
            if mobile_device_id not in self.device_connections:
                logger.warning(
                    f"📸 Remote scan FAILED: mobile {mobile_device_id[:8]}... not connected"
                )
                return False

            # Get mobile WebSocket (mobile only has one "tab")
            mobile_tabs = self.device_connections[mobile_device_id]
            if not mobile_tabs:
                return False

            # Store scan session for result routing (with tenant for isolation)
            self.remote_scan_sessions[scan_id] = {
                "tenant_id": tenant_id,
                "mobile_device_id": mobile_device_id,
                "desktop_device_id": desktop_device_id,
                "desktop_tab_id": desktop_tab_id,
                "requested_at": time.time(),
            }

            # Get first (and only) mobile connection
            mobile_ws = list(mobile_tabs.values())[0]

        try:
            await mobile_ws.send_json(
                {
                    "event": "remote_scan:request",
                    "scan_id": scan_id,
                }
            )
            logger.info(
                f"📸 Remote scan REQUEST sent: scan={scan_id[:8]}... -> mobile={mobile_device_id[:8]}..."
            )
            return True
        except Exception as e:
            logger.error(f"📸 Remote scan request FAILED: {e}")
            # Clean up failed session
            async with self._lock:
                if scan_id in self.remote_scan_sessions:
                    del self.remote_scan_sessions[scan_id]
            return False

    async def send_remote_scan_result(
        self,
        scan_id: str,
        barcode: Optional[str],
        error: Optional[str] = None,
        product: Optional[dict] = None,
    ) -> bool:
        """
        Send scan result from mobile back to desktop

        Args:
            scan_id: The scan session ID
            barcode: Scanned barcode (None if cancelled or error)
            error: Error message if scan failed
            product: Pre-fetched product data for latency optimization

        Returns:
            True if result was sent, False if desktop not connected
        """
        async with self._lock:
            if scan_id not in self.remote_scan_sessions:
                logger.warning(
                    f"📸 Remote scan result: unknown scan_id {scan_id[:8]}..."
                )
                return False

            session = self.remote_scan_sessions.pop(scan_id)
            desktop_device_id = session["desktop_device_id"]
            desktop_tab_id = session["desktop_tab_id"]

            # Find desktop WebSocket
            if desktop_device_id not in self.device_connections:
                logger.warning(
                    f"📸 Remote scan result DROPPED: desktop {desktop_device_id[:8]}... disconnected"
                )
                return False

            tabs = self.device_connections[desktop_device_id]
            if desktop_tab_id not in tabs:
                logger.warning(
                    f"📸 Remote scan result DROPPED: tab {desktop_tab_id[:8]}... closed"
                )
                return False

            desktop_ws = tabs[desktop_tab_id]

        try:
            if error:
                await desktop_ws.send_json(
                    {
                        "event": "remote_scan:error",
                        "scan_id": scan_id,
                        "error": error,
                    }
                )
                logger.info(f"📸 Remote scan ERROR sent: {scan_id[:8]}... -> {error}")
            elif barcode:
                await desktop_ws.send_json(
                    {
                        "event": "remote_scan:result",
                        "scan_id": scan_id,
                        "barcode": barcode,
                        "product": product,  # Pre-fetched for latency optimization
                    }
                )
                logger.info(
                    f"📸 Remote scan RESULT sent: {scan_id[:8]}... -> {barcode[:20]}... (product={'yes' if product else 'no'})"
                )
            else:
                await desktop_ws.send_json(
                    {
                        "event": "remote_scan:cancelled",
                        "scan_id": scan_id,
                    }
                )
                logger.info(f"📸 Remote scan CANCELLED: {scan_id[:8]}...")
            return True
        except Exception as e:
            logger.error(f"📸 Remote scan result FAILED: {e}")
            return False

    async def cancel_remote_scan(self, scan_id: str) -> bool:
        """
        Cancel an active remote scan session (desktop cancelled)

        Sends cancel event to mobile to close scanner UI
        """
        async with self._lock:
            if scan_id not in self.remote_scan_sessions:
                return False

            session = self.remote_scan_sessions.pop(scan_id)
            mobile_device_id = session["mobile_device_id"]

            if mobile_device_id not in self.device_connections:
                return False

            mobile_tabs = self.device_connections[mobile_device_id]
            if not mobile_tabs:
                return False

            mobile_ws = list(mobile_tabs.values())[0]

        try:
            await mobile_ws.send_json(
                {
                    "event": "remote_scan:cancel",
                    "scan_id": scan_id,
                }
            )
            logger.info(f"📸 Remote scan CANCEL sent to mobile: {scan_id[:8]}...")
            return True
        except Exception as e:
            logger.error(f"📸 Remote scan cancel FAILED: {e}")
            return False

    async def cleanup_expired_scan_sessions(self) -> int:
        """
        Clean up expired remote scan sessions
        Should be called periodically
        """
        cleaned = 0
        now = time.time()

        async with self._lock:
            expired = [
                scan_id
                for scan_id, session in self.remote_scan_sessions.items()
                if now - session["requested_at"] > REMOTE_SCAN_TIMEOUT
            ]

            for scan_id in expired:
                session = self.remote_scan_sessions.pop(scan_id)
                cleaned += 1

                # Notify desktop about timeout
                desktop_device_id = session["desktop_device_id"]
                desktop_tab_id = session["desktop_tab_id"]

                if desktop_device_id in self.device_connections:
                    tabs = self.device_connections[desktop_device_id]
                    if desktop_tab_id in tabs:
                        try:
                            await tabs[desktop_tab_id].send_json(
                                {
                                    "event": "remote_scan:timeout",
                                    "scan_id": scan_id,
                                }
                            )
                            logger.info(f"📸 Remote scan TIMEOUT: {scan_id[:8]}...")
                        except Exception:
                            pass

        return cleaned

    def is_mobile_online(self, mobile_device_id: str) -> bool:
        """Check if mobile device is connected for remote scanning"""
        return mobile_device_id in self.device_connections

    def get_scan_session_tenant(self, scan_id: str) -> Optional[str]:
        """
        Get tenant_id for a scan session (for validation before processing result)

        Args:
            scan_id: The scan session ID

        Returns:
            tenant_id if session exists, None otherwise
        """
        session = self.remote_scan_sessions.get(scan_id)
        return session.get("tenant_id") if session else None

    def pop_and_validate_session(
        self, scan_id: str, user_tenant: str
    ) -> Optional[dict]:
        """
        Atomically pop session and validate tenant (RACE-SAFE)

        This method combines pop + validation in one operation to prevent
        race conditions with duplicate result calls.

        Args:
            scan_id: The scan session ID
            user_tenant: Tenant ID from the request (to validate ownership)

        Returns:
            Session dict if valid, None if not found or tenant mismatch
        """
        session = self.remote_scan_sessions.pop(scan_id, None)
        if not session:
            logger.warning(
                f"📸 Session not found or already consumed: {scan_id[:8]}..."
            )
            return None

        if session.get("tenant_id") != user_tenant:
            logger.warning(
                f"🔒 TENANT MISMATCH: {user_tenant} tried to access scan from {session.get('tenant_id')}"
            )
            return None

        logger.info(f"📸 Session claimed: {scan_id[:8]}... by tenant {user_tenant}")
        return session

    async def send_scan_result_to_desktop(
        self,
        session: dict,
        scan_id: str,
        barcode: Optional[str],
        error: Optional[str] = None,
        product: Optional[dict] = None,
    ) -> bool:
        """
        Send scan result to desktop WebSocket (session already popped)

        This is called AFTER pop_and_validate_session() succeeds.
        Does NOT pop session - that's already done atomically.

        Args:
            session: The popped session dict (from pop_and_validate_session)
            scan_id: The scan session ID (for logging)
            barcode: Scanned barcode (None if cancelled or error)
            error: Error message if scan failed
            product: Pre-fetched product data for latency optimization

        Returns:
            True if result was sent, False if desktop not connected
        """
        desktop_device_id = session["desktop_device_id"]
        desktop_tab_id = session["desktop_tab_id"]

        async with self._lock:
            # Find desktop WebSocket
            if desktop_device_id not in self.device_connections:
                logger.warning(
                    f"📸 Remote scan result DROPPED: desktop {desktop_device_id[:8]}... disconnected"
                )
                return False

            tabs = self.device_connections[desktop_device_id]
            if desktop_tab_id not in tabs:
                logger.warning(
                    f"📸 Remote scan result DROPPED: tab {desktop_tab_id[:8]}... closed"
                )
                return False

            desktop_ws = tabs[desktop_tab_id]

        try:
            if error:
                await desktop_ws.send_json(
                    {
                        "event": "remote_scan:error",
                        "scan_id": scan_id,
                        "error": error,
                    }
                )
                logger.info(f"📸 Remote scan ERROR sent: {scan_id[:8]}... -> {error}")
            elif barcode:
                await desktop_ws.send_json(
                    {
                        "event": "remote_scan:result",
                        "scan_id": scan_id,
                        "barcode": barcode,
                        "product": product,
                    }
                )
                logger.info(
                    f"📸 Remote scan RESULT sent: {scan_id[:8]}... -> {barcode[:20]}... (product={'yes' if product else 'no'})"
                )
            else:
                await desktop_ws.send_json(
                    {
                        "event": "remote_scan:cancelled",
                        "scan_id": scan_id,
                    }
                )
                logger.info(f"📸 Remote scan CANCELLED: {scan_id[:8]}...")
            return True
        except Exception as e:
            logger.error(f"📸 Remote scan result send FAILED: {e}")
            return False


# Singleton instance
websocket_hub = WebSocketHub()
