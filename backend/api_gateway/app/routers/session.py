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
async def logout_session(request: Request, session_id: str):
    """
    Logout specific session (requires authentication)
    """
    try:
        if not hasattr(request.state, 'user'):
            raise HTTPException(
                status_code=401,
                detail="Authentication required"
            )
        
        user = request.state.user
        user_id = user["user_id"]
        
        logger.info(f"Logout session {session_id} for user {user_id}")
        
        # TODO: Implement session revocation in auth service
        return {
            "success": True,
            "message": f"Session {session_id} logged out"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Logout error: {str(e)}")
        raise HTTPException(status_code=500, detail="Logout failed")


@router.post("/logout-all")
async def logout_all_sessions(request: Request):
    """
    Logout all sessions for current user (requires authentication)
    """
    try:
        if not hasattr(request.state, 'user'):
            raise HTTPException(
                status_code=401,
                detail="Authentication required"
            )
        
        user = request.state.user
        user_id = user["user_id"]
        
        logger.info(f"Logout all sessions for user {user_id}")
        
        # TODO: Implement revoke all sessions in auth service
        return {
            "success": True,
            "message": "All sessions logged out"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Logout all error: {str(e)}")
        raise HTTPException(status_code=500, detail="Logout all failed")
