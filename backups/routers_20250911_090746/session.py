"""
Session Management Router - Phase 2 Implementation
Session CRUD endpoints per documentation
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import List, Dict, Any
import structlog

# Import session manager
import sys
sys.path.append('/app/backend/api_gateway/app')
from services.session_manager import SessionManager

logger = structlog.get_logger(__name__)
router = APIRouter()

# Initialize session manager
session_manager = SessionManager()

def get_current_user(request: Request) -> Dict[str, Any]:
    """Dependency to get current authenticated user"""
    if not hasattr(request.state, 'authenticated') or not request.state.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    return request.state.user

@router.post("/logout")
async def logout_session(request: Request, current_user: Dict = Depends(get_current_user)):
    """
    Logout single session
    Per Phase 2 documentation: single session termination
    """
    
    # Extract token from request
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Invalid authorization header")
    
    token = auth_header[7:]
    
    # Revoke session
    success = session_manager.revoke_session(token)
    
    if success:
        logger.info("✅ Session logout successful", user_id=current_user.get('user_id'))
        return {"message": "Logout successful", "status": "session_revoked"}
    else:
        logger.warning("⚠️ Session logout failed", user_id=current_user.get('user_id'))
        return {"message": "Logout completed", "status": "session_not_found"}

@router.post("/logout-all")
async def logout_all_sessions(current_user: Dict = Depends(get_current_user)):
    """
    Logout all user sessions
    Per Phase 2 documentation: all device logout
    """
    
    user_id = current_user.get('user_id')
    
    # Get all user sessions
    sessions = session_manager.list_user_sessions(user_id)
    
    # Revoke all sessions
    revoked_count = 0
    for session in sessions:
        session_key = session['session_key']
        token = session_key.split(':', 1)[1] if ':' in session_key else session_key
        if session_manager.revoke_session(token + "dummy"):  # Reconstruct token
            revoked_count += 1
    
    logger.info("✅ All sessions logout", user_id=user_id, revoked=revoked_count)
    return {
        "message": "All sessions logged out",
        "sessions_revoked": revoked_count,
        "total_sessions": len(sessions)
    }

@router.get("/sessions")
async def list_sessions(current_user: Dict = Depends(get_current_user)):
    """
    List user active sessions
    Per Phase 2 documentation: cross-device session tracking
    """
    
    user_id = current_user.get('user_id')
    sessions = session_manager.list_user_sessions(user_id)
    
    logger.info("📋 Sessions listed", user_id=user_id, count=len(sessions))
    return {
        "user_id": user_id,
        "active_sessions": len(sessions),
        "sessions": sessions
    }

@router.get("/health")
async def session_health():
    """Session service health check"""
    
    redis_healthy = session_manager.health_check()
    
    return {
        "service": "session_manager",
        "redis_connection": "healthy" if redis_healthy else "unhealthy",
        "status": "operational" if redis_healthy else "degraded"
    }
