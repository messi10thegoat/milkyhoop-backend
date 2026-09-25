"""
Session Management Router
Handles user session operations with proper authentication
"""
import logging
from typing import Dict
from fastapi import APIRouter, HTTPException, Request

from backend.api_gateway.app.services.auth_instance import auth_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/session",
    tags=["session"]
)


@router.get("/list")
async def list_sessions(request: Request):
    """
    List all active sessions for current user (requires authentication)
    Auth handled by middleware - user info in request.state.user
    """
    try:
        # Get user from middleware (already validated)
        if not hasattr(request.state, 'user'):
            raise HTTPException(
                status_code=401, 
                detail="Authentication required"
            )
        
        user = request.state.user
        user_id = user["user_id"]
        
        logger.info(f"Listing sessions for user {user_id}")
        
        # TODO: Implement real session listing from auth service
        # For now, return mock data
        return {
            "success": True,
            "sessions": [
                {
                    "session_id": "current-session",
                    "device": "web",
                    "created_at": "2025-10-13T16:00:00Z",
                    "last_active": "2025-10-13T16:45:00Z"
                }
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List sessions error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list sessions")


@router.post("/logout")
async def logout_session(request: Request, session_id: str = None):
    """DIPARKIR (26 Sep 2026, audit sesi): dulu stub TODO yang membalas sukses tanpa
    mencabut apa pun -> rasa aman palsu. Tak ada konsep "sesi" terpisah dari
    perangkat: cabut per perangkat lewat DELETE /api/devices/{id}. FE 0 pemanggil."""
    raise HTTPException(
        status_code=409,
        detail={"code": "FEATURE_NOT_AVAILABLE",
                "message": "Gunakan pengelolaan perangkat untuk keluar dari satu perangkat."},
    )


@router.post("/logout-all")
async def logout_all_sessions(request: Request):
    """Keluar dari SEMUA perangkat — sungguhan (26 Sep 2026, audit sesi).

    Dulu stub TODO yang membalas sukses tanpa mencabut apa pun. Kini, untuk
    pengguna dari JWT (bukan dari parameter): sesi Redis semua perangkat
    dicabut (AuthMiddleware menolak token perangkat itu seketika) DAN semua
    refresh token dicabut di auth_service.
    """
    from backend.api_gateway.app.services.session_manager import session_manager

    user = getattr(request.state, "user", None) or {}
    user_id = user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    ok_redis = session_manager.revoke_all(str(user_id))
    hasil = await auth_client.logout(
        user_id=str(user_id), refresh_token=None, logout_all_devices=True
    )
    if not ok_redis or not hasil.get("success"):
        logger.error(f"[logout-all] pencabutan tak lengkap user={str(user_id)[:8]} redis={ok_redis}")
        raise HTTPException(status_code=503, detail="Pencabutan sesi belum lengkap; coba lagi.")
    return {
        "success": True,
        "message": "Semua perangkat telah dikeluarkan",
        "revoked_tokens": hasil.get("revoked_tokens", 0),
    }
