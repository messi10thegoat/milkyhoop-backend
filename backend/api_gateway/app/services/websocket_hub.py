"""
WebSocket Hub Service
Manages WebSocket connections for QR Login and device communication

Connections are managed in two pools:
1. qr_connections: token -> WebSocket (for QR login flow)
2. device_connections: device_id -> tab_id -> WebSocket (for force logout)
   - Multiple tabs can be connected per device_id
   - Force logout broadcasts to ALL tabs of a device
"""
import logging
import asyncio
from typing import Dict
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketHub:
    """
    Singleton WebSocket connection manager
    Handles QR login status updates and device force logout

    Device connections support multi-tab:
    - Each browser tab has unique tab_id (from sessionStorage)
    - Same device_id can have multiple tab connections
    - Force logout broadcasts to ALL tabs of a device
    """

    def __init__(self):
        # QR token -> WebSocket (desktop waiting for approval)
        self.qr_connections: Dict[str, WebSocket] = {}
        # Device ID -> Tab ID -> WebSocket (multi-tab support)
        self.device_connections: Dict[str, Dict[str, WebSocket]] = {}
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


# Singleton instance
websocket_hub = WebSocketHub()
