"""
Device Management Router
Handles linked devices for QR Login system

Endpoints:
- GET /api/devices - List all linked devices
- DELETE /api/devices/{device_id} - Logout specific device
- POST /api/devices/logout-all-web - Logout all web sessions
- WS /api/devices/ws/{device_id} - WebSocket for force logout notifications
"""
import logging
from typing import List
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.api_gateway.libs.milkyhoop_prisma import Prisma
from backend.api_gateway.app.services.device_service import DeviceService
from backend.api_gateway.app.services.websocket_hub import websocket_hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])


# ================================
# RESPONSE MODELS
# ================================


class DeviceListResponse(BaseModel):
    """Response with list of devices"""

    success: bool
    devices: List[dict]
    count: int


class DeviceActionResponse(BaseModel):
    """Response for device actions (logout, etc.)"""

    success: bool
    message: str


class LogoutAllResponse(BaseModel):
    """Response for logout all web devices"""

    success: bool
    message: str
    count: int  # Number of devices logged out


# ================================
# HELPER FUNCTIONS
# ================================


def get_prisma(request: Request) -> Prisma:
    """Get Prisma client from app state"""
    from backend.api_gateway.app.main import prisma

    return prisma


def get_device_service(request: Request) -> DeviceService:
    """Get device service"""
    return DeviceService(get_prisma(request))


def get_user_from_request(request: Request) -> dict:
    """Extract authenticated user from request"""
    if not hasattr(request.state, "user"):
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    user_id = user.get("user_id")
    tenant_id = user.get("tenant_id")

    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user session")

    return {"user_id": user_id, "tenant_id": tenant_id}


# ================================
# ENDPOINTS (All require authentication)
# ================================


@router.get("", response_model=DeviceListResponse)
async def list_devices(request: Request):
    """
    List all linked devices for current user

    Returns:
        List of active devices with metadata
    """
    try:
        user = get_user_from_request(request)
        service = get_device_service(request)

        # Get device ID from request state (if available)
        current_device_id = getattr(request.state, "device_id", None)

        devices = await service.list_devices(
            user_id=user["user_id"],
            tenant_id=user["tenant_id"],
            current_device_id=current_device_id,
        )

        # Convert to dict for JSON response
        device_list = []
        for d in devices:
            device_list.append(
                {
                    "id": d.id,
                    "device_type": d.device_type,
                    "device_name": d.device_name,
                    "is_active": d.is_active,
                    "is_primary": d.is_primary,
                    "is_current": d.is_current,
                    "last_active_at": d.last_active_at.isoformat(),
                    "created_at": d.created_at.isoformat(),
                }
            )

        return DeviceListResponse(
            success=True, devices=device_list, count=len(device_list)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list devices: {e}")
        raise HTTPException(status_code=500, detail="Gagal mengambil daftar perangkat")


@router.delete("/{device_id}", response_model=DeviceActionResponse)
async def logout_device(request: Request, device_id: str):
    """
    Logout a specific device

    Args:
        device_id: ID of device to logout

    Note:
        - Cannot logout the primary (mobile) device
        - Cannot logout the current device (use regular logout instead)
    """
    try:
        user = get_user_from_request(request)
        service = get_device_service(request)

        # Check if trying to logout current device
        current_device_id = getattr(request.state, "device_id", None)
        if current_device_id and current_device_id == device_id:
            raise HTTPException(
                status_code=400,
                detail="Tidak dapat logout perangkat yang sedang digunakan. Gunakan menu Logout.",
            )

        success = await service.logout_device(
            device_id=device_id, user_id=user["user_id"], tenant_id=user["tenant_id"]
        )

        if not success:
            raise HTTPException(
                status_code=400,
                detail="Gagal logout perangkat. Perangkat tidak ditemukan atau tidak dapat di-logout.",
            )

        return DeviceActionResponse(
            success=True, message="Perangkat berhasil di-logout"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to logout device: {e}")
        raise HTTPException(status_code=500, detail="Gagal logout perangkat")


@router.post("/logout-all-web", response_model=LogoutAllResponse)
async def logout_all_web_devices(request: Request):
    """
    Logout all web devices for current user

    This is a cascade logout - all active web sessions will be terminated.
    Primary device (mobile) will NOT be affected.
    """
    try:
        user = get_user_from_request(request)
        service = get_device_service(request)

        count = await service.logout_all_web_devices(
            user_id=user["user_id"], tenant_id=user["tenant_id"]
        )

        if count == 0:
            return LogoutAllResponse(
                success=True, message="Tidak ada sesi web yang aktif", count=0
            )

        return LogoutAllResponse(
            success=True,
            message=f"{count} perangkat web berhasil di-logout",
            count=count,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to logout all web devices: {e}")
        raise HTTPException(status_code=500, detail="Gagal logout semua perangkat web")


@router.get("/stats")
async def get_device_stats(request: Request):
    """
    Get device statistics for monitoring (admin only)
    """
    try:
        user = get_user_from_request(request)

        # Check if user has admin/owner role
        role = request.state.user.get("role", "FREE")
        if role not in ["ADMIN", "CORPORATE"]:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        from backend.api_gateway.app.services.websocket_hub import websocket_hub

        ws_stats = websocket_hub.get_stats()

        return {"success": True, "websocket_stats": ws_stats}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get device stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get stats")


# ================================
# WEBSOCKET ENDPOINT
# ================================


@router.websocket("/ws/{device_id}")
async def device_websocket(
    websocket: WebSocket, device_id: str, tab_id: str = "default"
):
    """
    WebSocket for device-specific notifications (force logout)

    Web sessions connect here after login to receive real-time notifications:
    - {"event": "force_logout", "reason": "Session digantikan oleh login baru"}
    - {"event": "ping"} / {"event": "pong"} for keepalive

    Args:
        device_id: Device identifier from login response (shared in localStorage)
        tab_id: Unique tab identifier (from sessionStorage, unique per browser tab)
                Allows multiple tabs to connect for the same device_id

    URL format: /api/devices/ws/{device_id}?tab_id={tab_id}
    """
    await websocket.accept()

    try:
        # Register this WebSocket for the device + tab
        await websocket_hub.register_device(device_id, websocket, tab_id)

        # Send initial connected event
        await websocket.send_json(
            {
                "event": "connected",
                "message": "Device WebSocket terhubung",
                "device_id": device_id,
                "tab_id": tab_id,
            }
        )

        # Keep connection alive and handle messages
        while True:
            try:
                data = await websocket.receive_json()

                if data.get("event") == "ping":
                    await websocket.send_json({"event": "pong"})

            except WebSocketDisconnect as e:
                logger.warning(
                    f"🔌 Device WebSocket {device_id[:8]}... tab={tab_id[:8]}... DISCONNECT: code={e.code if hasattr(e, 'code') else 'N/A'}"
                )
                break
            except Exception as e:
                logger.warning(
                    f"🔌 Device WebSocket {device_id[:8]}... tab={tab_id[:8]}... ERROR: {type(e).__name__}: {e}"
                )
                break

    except Exception as e:
        logger.error(f"Device WebSocket error: {e}")

    finally:
        # Cleanup
        logger.warning(
            f"🔌 Device WebSocket {device_id[:8]}... tab={tab_id[:8]}... cleanup (finally block)"
        )
        await websocket_hub.unregister_device(device_id, tab_id)
