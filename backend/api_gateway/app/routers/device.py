"""
Device Management Router
Handles linked devices for QR Login system and Remote Scanner

Endpoints:
- GET /api/devices - List all linked devices
- DELETE /api/devices/{device_id} - Logout specific device
- POST /api/devices/logout-all-web - Logout all web sessions
- WS /api/devices/ws/{device_id} - WebSocket for force logout + remote scan

Remote Scanner Endpoints:
- POST /api/devices/remote-scan/request - Desktop triggers scan on mobile
- POST /api/devices/remote-scan/result - Mobile sends scan result
- POST /api/devices/remote-scan/cancel - Cancel active scan
"""
import logging
import os
import uuid
import asyncpg
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.api_gateway.libs.milkyhoop_prisma import Prisma
from backend.api_gateway.app.services.device_service import DeviceService
from backend.api_gateway.app.services.websocket_hub import websocket_hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])


# ================================
# DATABASE HELPERS (for Remote Scanner product lookup)
# ================================

# Wholesale unit names (for detecting if last purchase was wholesale)
WHOLESALE_UNITS = {"karton", "dus", "box", "slop", "bal", "koli", "pack", "lusin", "rim", "gross", "sak"}


async def _get_db_connection():
    """Get async database connection for product lookup"""
    return await asyncpg.connect(os.environ.get("DATABASE_URL"))


async def _lookup_product_by_barcode(tenant_id: str, barcode: str) -> Optional[dict]:
    """
    Enhanced product lookup by barcode for Remote Scanner with last transaction data.
    Uses tenant_id from scan session for isolation.

    Returns dict with:
    - Basic product info (name, unit, category, price, barcode)
    - Last pembelian transaction (last_unit, last_price, hpp_per_unit, units_per_pack)
    - Content unit from last penjualan (content_unit)
    """
    try:
        conn = await _get_db_connection()
        try:
            # 1. Get basic product info
            row = await conn.fetchrow(
                """
                SELECT
                    id,
                    nama_produk as name,
                    satuan as unit,
                    kategori as category,
                    harga_jual as price,
                    barcode
                FROM public.products
                WHERE tenant_id = $1 AND barcode = $2
                LIMIT 1
                """,
                tenant_id,
                barcode,
            )
            if not row:
                return None

            result = dict(row)
            product_name = result["name"]

            # 2. Get last PEMBELIAN (purchase) transaction for this product
            last_tx = await conn.fetchrow(
                """
                SELECT
                    it.satuan as last_unit,
                    it.harga_satuan as last_price,
                    it.hpp_per_unit,
                    it.kuantitas as last_qty
                FROM public.item_transaksi it
                JOIN public.transaksi_harian th ON it.transaksi_id = th.id
                WHERE th.tenant_id = $1
                  AND LOWER(it.nama_produk) = LOWER($2)
                  AND th.jenis_transaksi = 'pembelian'
                ORDER BY th.created_at DESC
                LIMIT 1
                """,
                tenant_id,
                product_name,
            )

            if last_tx:
                result["last_unit"] = last_tx["last_unit"]  # karton, dus, slop
                result["last_price"] = last_tx["last_price"]
                result["hpp_per_unit"] = last_tx["hpp_per_unit"]

                # Compute units_per_pack (qty per wholesale unit)
                if last_tx["last_price"] and last_tx["hpp_per_unit"] and last_tx["hpp_per_unit"] > 0:
                    result["units_per_pack"] = int(last_tx["last_price"] / last_tx["hpp_per_unit"])

                logger.info(
                    f"📸 Last pembelian: unit={last_tx['last_unit']}, "
                    f"price={last_tx['last_price']}, hpp={last_tx['hpp_per_unit']}"
                )

            # 3. Get content_unit from last PENJUALAN (retail sales) if last purchase was wholesale
            last_unit_lower = (result.get("last_unit") or "").lower()
            if last_unit_lower in WHOLESALE_UNITS:
                retail_tx = await conn.fetchrow(
                    """
                    SELECT it.satuan as content_unit
                    FROM public.item_transaksi it
                    JOIN public.transaksi_harian th ON it.transaksi_id = th.id
                    WHERE th.tenant_id = $1
                      AND LOWER(it.nama_produk) = LOWER($2)
                      AND th.jenis_transaksi = 'penjualan'
                      AND LOWER(it.satuan) NOT IN ('karton','dus','box','slop','bal','koli','pack','lusin','rim','gross','sak')
                    ORDER BY th.created_at DESC
                    LIMIT 1
                    """,
                    tenant_id,
                    product_name,
                )

                if retail_tx:
                    result["content_unit"] = retail_tx["content_unit"]
                    logger.info(f"📸 Content unit from penjualan: {retail_tx['content_unit']}")

            return result
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Product lookup failed: {e}")
        return None


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
# REMOTE SCANNER MODELS
# ================================


class RemoteScanRequest(BaseModel):
    """Request to trigger remote scan on mobile"""

    tab_id: str  # Desktop tab requesting the scan


class RemoteScanRequestResponse(BaseModel):
    """Response after requesting remote scan"""

    success: bool
    scan_id: Optional[str] = None
    message: str


class RemoteScanResultRequest(BaseModel):
    """Mobile sending scan result back"""

    scan_id: str
    barcode: Optional[str] = None  # None if cancelled
    error: Optional[str] = None  # Error message if scan failed


class RemoteScanResultResponse(BaseModel):
    """Response after sending scan result"""

    success: bool
    message: str


class RemoteScanCancelRequest(BaseModel):
    """Cancel active scan request"""

    scan_id: str


class MobileStatusResponse(BaseModel):
    """Response for mobile connection status"""

    success: bool
    is_online: bool
    message: str


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
        _user = get_user_from_request(request)  # Auth check

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
# REMOTE SCANNER ENDPOINTS
# ================================


@router.get("/mobile-status", response_model=MobileStatusResponse)
async def get_mobile_status(request: Request):
    """
    Check if user's mobile device is online and ready for remote scanning

    Called by desktop to show connection status before attempting scan
    """
    try:
        user = get_user_from_request(request)
        service = get_device_service(request)

        # Find mobile device for this user
        mobile_device = await service.get_mobile_device(
            user_id=user["user_id"], tenant_id=user["tenant_id"]
        )

        if not mobile_device:
            return MobileStatusResponse(
                success=True,
                is_online=False,
                message="Tidak ada perangkat mobile yang terhubung",
            )

        # Check if mobile is online
        is_online = websocket_hub.is_mobile_online(mobile_device.id)

        return MobileStatusResponse(
            success=True,
            is_online=is_online,
            message="Mobile online" if is_online else "Mobile offline",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get mobile status: {e}")
        raise HTTPException(status_code=500, detail="Gagal mengecek status mobile")


@router.post("/remote-scan/request", response_model=RemoteScanRequestResponse)
async def request_remote_scan(request: Request, body: RemoteScanRequest):
    """
    Desktop triggers barcode scan on paired mobile device

    Flow:
    1. Desktop calls this endpoint with tab_id
    2. Server sends WebSocket event to mobile: remote_scan:request
    3. Mobile opens camera scanner
    4. Mobile sends result via POST /remote-scan/result
    5. Server routes result back to desktop via WebSocket

    Requirements:
    - User must be logged in on both desktop AND mobile
    - Mobile must have active WebSocket connection
    """
    try:
        user = get_user_from_request(request)
        service = get_device_service(request)

        # Get desktop device_id from token (stored in request.state.user by auth middleware)
        desktop_device_id = (
            request.state.user.get("device_id")
            if hasattr(request.state, "user")
            else None
        )
        if not desktop_device_id:
            raise HTTPException(
                status_code=400,
                detail="Device ID tidak ditemukan. Login ulang diperlukan.",
            )

        # Find mobile device for this user
        mobile_device = await service.get_mobile_device(
            user_id=user["user_id"], tenant_id=user["tenant_id"]
        )

        if not mobile_device:
            raise HTTPException(
                status_code=400,
                detail="Tidak ada perangkat mobile yang terhubung",
            )

        # Check if mobile is online
        if not websocket_hub.is_mobile_online(mobile_device.id):
            return RemoteScanRequestResponse(
                success=False,
                message="Mobile tidak online. Buka aplikasi di HP untuk scan.",
            )

        # Generate unique scan ID
        scan_id = str(uuid.uuid4())

        # Send request to mobile (with tenant_id for isolation)
        sent = await websocket_hub.send_remote_scan_request(
            mobile_device_id=mobile_device.id,
            scan_id=scan_id,
            desktop_device_id=desktop_device_id,
            desktop_tab_id=body.tab_id,
            tenant_id=user["tenant_id"],
        )

        if not sent:
            return RemoteScanRequestResponse(
                success=False,
                message="Gagal mengirim request ke mobile. Coba lagi.",
            )

        return RemoteScanRequestResponse(
            success=True,
            scan_id=scan_id,
            message="Scan request terkirim ke mobile",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Remote scan request failed: {e}")
        raise HTTPException(status_code=500, detail="Gagal meminta scan")


@router.post("/remote-scan/result", response_model=RemoteScanResultResponse)
async def send_remote_scan_result(request: Request, body: RemoteScanResultRequest):
    """
    Mobile sends barcode scan result back to desktop

    Called by mobile after scanning barcode or cancelling

    Uses atomic pop_and_validate_session() to prevent race conditions
    with duplicate calls.
    """
    try:
        # Mobile must be authenticated
        user = get_user_from_request(request)
        user_tenant = user["tenant_id"]

        # ATOMIC: Pop session and validate tenant in one operation (RACE-SAFE)
        # This prevents duplicate calls from both succeeding
        session = websocket_hub.pop_and_validate_session(body.scan_id, user_tenant)
        if not session:
            # Session not found, expired, or tenant mismatch
            # (pop_and_validate_session already logged the reason)
            return RemoteScanResultResponse(
                success=False,
                message="Scan session tidak ditemukan atau sudah expired",
            )

        # LATENCY OPTIMIZATION: Lookup product before sending to desktop
        product = None
        if body.barcode and not body.error:
            product = await _lookup_product_by_barcode(session["tenant_id"], body.barcode)
            if product:
                logger.info(f"📸 Product pre-fetched: {product.get('name', 'unknown')}")
            else:
                logger.info(f"📸 Product not found for barcode: {body.barcode}")

        # Send result to desktop via WebSocket (session already popped)
        sent = await websocket_hub.send_scan_result_to_desktop(
            session=session,
            scan_id=body.scan_id,
            barcode=body.barcode,
            error=body.error,
            product=product,
        )

        if not sent:
            return RemoteScanResultResponse(
                success=False,
                message="Desktop tidak terhubung",
            )

        return RemoteScanResultResponse(
            success=True,
            message="Hasil scan terkirim ke desktop",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Remote scan result failed: {e}")
        raise HTTPException(status_code=500, detail="Gagal mengirim hasil scan")


@router.post("/remote-scan/cancel", response_model=RemoteScanResultResponse)
async def cancel_remote_scan(request: Request, body: RemoteScanCancelRequest):
    """
    Cancel an active remote scan session

    Can be called by either desktop or mobile
    """
    try:
        _user = get_user_from_request(request)  # Auth check

        # Cancel the scan session
        cancelled = await websocket_hub.cancel_remote_scan(body.scan_id)

        if not cancelled:
            return RemoteScanResultResponse(
                success=False,
                message="Scan session tidak ditemukan atau sudah selesai",
            )

        return RemoteScanResultResponse(
            success=True,
            message="Scan dibatalkan",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Remote scan cancel failed: {e}")
        raise HTTPException(status_code=500, detail="Gagal membatalkan scan")


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
