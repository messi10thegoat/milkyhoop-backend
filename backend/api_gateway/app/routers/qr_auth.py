"""
QR Authentication Router
Handles QR-based login flow (WhatsApp-style)

Endpoints:
- POST /api/auth/qr/generate - Generate QR token (desktop, no auth)
- GET /api/auth/qr/status/{token} - Poll status (desktop, no auth)
- WS /api/auth/qr/ws/{token} - WebSocket for real-time updates (desktop)
- POST /api/auth/qr/scan - Scan QR code (mobile, requires auth)
- POST /api/auth/qr/approve - Approve/reject login (mobile, requires auth)
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.api_gateway.libs.milkyhoop_prisma import Prisma
from backend.api_gateway.app.services.qr_token_service import QRTokenService
from backend.api_gateway.app.services.websocket_hub import websocket_hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/qr", tags=["qr-auth"])


# ================================
# REQUEST/RESPONSE MODELS
# ================================


class GenerateQRRequest(BaseModel):
    """Request to generate QR code"""

    fingerprint: Optional[str] = None
    browser_id: Optional[str] = None  # Browser profile ID for single session


class GenerateQRResponse(BaseModel):
    """Response with QR code data"""

    success: bool
    token: str
    qr_url: str  # milkyhoop://login?token=xxx
    expires_at: str
    ttl_seconds: int


class StatusResponse(BaseModel):
    """QR token status response"""

    success: bool
    status: str  # pending, scanned, approved, rejected, expired
    is_expired: bool
    message: str


class ScanRequest(BaseModel):
    """Request to scan QR code (from mobile)"""

    token: str


class ApproveRequest(BaseModel):
    """Request to approve/reject login"""

    token: str
    approved: bool  # True to approve, False to reject


class ApproveResponse(BaseModel):
    """Response after approve/reject"""

    success: bool
    message: str


# ================================
# HELPER FUNCTIONS
# ================================


def get_prisma(request: Request) -> Prisma:
    """Get Prisma client from app state"""
    from backend.api_gateway.app.main import prisma

    return prisma


def get_qr_service(request: Request) -> QRTokenService:
    """Get QR token service"""
    return QRTokenService(get_prisma(request))


def get_client_ip(request: Request) -> str:
    """Get client IP address"""
    # Check X-Forwarded-For header (behind proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ================================
# PUBLIC ENDPOINTS (No auth required)
# ================================


@router.post("/generate", response_model=GenerateQRResponse)
async def generate_qr_token(
    request: Request, body: GenerateQRRequest = GenerateQRRequest()
):
    """
    Generate a new QR login token for desktop browser

    This endpoint does NOT require authentication.
    Desktop browser calls this, displays QR code, then waits via WebSocket.

    Args (in body):
        fingerprint: Browser fingerprint for security
        browser_id: Browser profile ID for single session enforcement
    """
    try:
        service = get_qr_service(request)

        # Extract web info
        user_agent = request.headers.get("User-Agent")
        client_ip = get_client_ip(request)

        result = await service.generate_token(
            web_fingerprint=body.fingerprint,
            web_user_agent=user_agent,
            web_ip=client_ip,
            browser_id=body.browser_id,
        )

        return GenerateQRResponse(
            success=True,
            token=result.token,
            qr_url=result.qr_url,
            expires_at=result.expires_at.isoformat(),
            ttl_seconds=result.ttl_seconds,
        )

    except Exception as e:
        logger.error(f"Failed to generate QR token: {e}")
        raise HTTPException(status_code=500, detail="Gagal membuat QR code")


@router.get("/status/{token}", response_model=StatusResponse)
async def check_qr_status(request: Request, token: str):
    """
    Check current status of a QR token (polling endpoint)

    This endpoint does NOT require authentication.
    Desktop browser polls this while waiting for mobile approval.
    """
    try:
        service = get_qr_service(request)
        status = await service.check_status(token)

        if not status:
            return StatusResponse(
                success=False,
                status="not_found",
                is_expired=True,
                message="QR code tidak ditemukan atau sudah kadaluarsa",
            )

        messages = {
            "pending": "Menunggu scan dari perangkat mobile...",
            "scanned": "QR code telah di-scan, menunggu konfirmasi...",
            "approved": "Login disetujui!",
            "rejected": "Login ditolak",
            "expired": "QR code sudah kadaluarsa",
        }

        return StatusResponse(
            success=True,
            status=status.status,
            is_expired=status.is_expired,
            message=messages.get(status.status, "Unknown status"),
        )

    except Exception as e:
        logger.error(f"Failed to check QR status: {e}")
        raise HTTPException(status_code=500, detail="Gagal mengecek status")


@router.websocket("/ws/{token}")
async def qr_websocket(websocket: WebSocket, token: str):
    """
    WebSocket for real-time QR login status updates

    Desktop browser connects here after generating QR code.
    Receives events:
    - {"event": "scanned", "message": "..."}
    - {"event": "approved", "access_token": "...", "refresh_token": "...", "user": {...}}
    - {"event": "rejected", "message": "..."}
    - {"event": "expired", "message": "..."}
    """
    await websocket.accept()

    try:
        # Register this WebSocket for the token
        await websocket_hub.register_qr(token, websocket)

        # Send initial connected event
        await websocket.send_json(
            {
                "event": "connected",
                "message": "WebSocket terhubung. Scan QR code dengan perangkat mobile.",
            }
        )

        # Keep connection alive and handle messages
        while True:
            try:
                # Wait for messages (heartbeat/ping)
                data = await websocket.receive_json()

                if data.get("event") == "ping":
                    await websocket.send_json({"event": "pong"})

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug(f"WebSocket receive error: {e}")
                break

    except Exception as e:
        logger.error(f"WebSocket error: {e}")

    finally:
        # Cleanup
        await websocket_hub.unregister_qr(token)


# ================================
# AUTHENTICATED ENDPOINTS (Mobile app)
# ================================


@router.post("/scan")
async def scan_qr_code(request: Request, body: ScanRequest):
    """
    Mobile app scans QR code

    Requires authentication (mobile must be logged in).
    Updates QR status to "scanned" and notifies desktop via WebSocket.
    """
    try:
        # Check authentication
        if not hasattr(request.state, "user"):
            raise HTTPException(status_code=401, detail="Authentication required")

        user = request.state.user
        user_id = user.get("user_id")

        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid user session")

        service = get_qr_service(request)
        success, message = await service.scan_token(body.token, user_id)

        if not success:
            raise HTTPException(status_code=400, detail=message)

        return {
            "success": True,
            "message": message,
            "requires_approval": True,  # Mobile should show approve/reject buttons
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to scan QR code: {e}")
        raise HTTPException(status_code=500, detail="Gagal memproses QR code")


@router.post("/approve", response_model=ApproveResponse)
async def approve_qr_login(request: Request, body: ApproveRequest):
    """
    Mobile app approves or rejects the QR login

    Requires authentication (mobile must be logged in).
    If approved, generates tokens for desktop and sends via WebSocket.
    """
    try:
        # Check authentication
        if not hasattr(request.state, "user"):
            raise HTTPException(status_code=401, detail="Authentication required")

        user = request.state.user
        user_id = user.get("user_id")
        tenant_id = user.get("tenant_id")

        if not user_id or not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user session")

        service = get_qr_service(request)
        success, message, tokens = await service.approve_login(
            token=body.token,
            user_id=user_id,
            tenant_id=tenant_id,
            approved=body.approved,
        )

        if not success:
            raise HTTPException(status_code=400, detail=message)

        return ApproveResponse(success=True, message=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to approve QR login: {e}")
        raise HTTPException(status_code=500, detail="Gagal memproses persetujuan")
