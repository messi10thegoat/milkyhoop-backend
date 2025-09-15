from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
import grpc
import sys
import logging

sys.path.append('/app/backend/api_gateway/libs/milkyhoop_protos')
sys.path.append('/app/backend/api_gateway/app')

from backend.api_gateway.libs.milkyhoop_protos import auth_service_pb2, auth_service_pb2_grpc
from services.session_manager import SessionManager

router = APIRouter()
session_manager = SessionManager()
logger = logging.getLogger(__name__)

@router.post("/auth/refresh-token")
async def refresh_token(request: Request):
    """Refresh access token using refresh token or current session"""
    
    # Extract current token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Invalid authorization header")
    
    current_token = auth_header[7:]
    
    # Check if session exists
    session_data = session_manager.get_session(current_token)
    if not session_data:
        raise HTTPException(status_code=401, detail="No active session found")
    
    try:
        # Call auth service RefreshToken
        channel = grpc.aio.insecure_channel("auth_service:5004")
        stub = auth_service_pb2_grpc.AuthServiceStub(channel)
        
        refresh_request = auth_service_pb2.RefreshTokenRequest(
            user_id=session_data['user_id'],
            current_session_id=session_await data.get('session_id', '')
        )
        
        response = await stub.RefreshToken(refresh_request)
        await channel.close()
        
        if hasattr(response, 'success') and response.success:
            # Update session with new token data
            new_token_data = {
                "user_id": response.user_id,
                "tenant_id": response.tenant_id,
                "role": response.role,
                "session_id": response.session_id,
                "expires_in": getattr(response, 'expires_in', 3600),
                "permissions": list(getattr(response, 'permissions', []))
            }
            
            # Revoke old session and create new one
            session_manager.revoke_session(current_token)
            session_manager.store_session(response.access_token, new_token_data, new_token_data['expires_in'])
            
            return {
                "success": True,
                "access_token": response.access_token,
                "refresh_token": getattr(response, 'refresh_token', None),
                "expires_in": new_token_data['expires_in'],
                "token_type": "Bearer"
            }
        else:
            raise HTTPException(status_code=401, detail="Token refresh failed")
            
    except grpc.RpcError as e:
        logger.error(f"gRPC refresh error: {e}")
        raise HTTPException(status_code=500, detail="Token refresh service error")
    except Exception as e:
        logger.error(f"Refresh token error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")

@router.get("/auth/token-info")
async def token_info(request: Request):
    """Get current token information and expiry"""
    
    if not hasattr(request.state, 'authenticated') or not request.state.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Extract token
    auth_header = request.headers.get("Authorization", "")
    current_token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    
    if not current_token:
        raise HTTPException(status_code=400, detail="No token provided")
    
    # Get session info
    session_data = session_manager.get_session(current_token)
    if not session_data:
        raise HTTPException(status_code=401, detail="Session not found")
    
    from datetime import datetime, timezone
    
    # Calculate time until expiry
    try:
        expires_at = datetime.fromisoformat(session_await data.get('expires_at', ''))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        time_to_expiry = (expires_at - now).total_seconds()
        
        return {
            "user_id": session_data['user_id'],
            "tenant_id": session_data['tenant_id'],
            "expires_at": session_await data.get('expires_at'),
            "time_to_expiry_seconds": max(0, int(time_to_expiry)),
            "needs_refresh": time_to_expiry < 300,  # Less than 5 minutes
            "session_id": session_await data.get('session_id')
        }
        
    except Exception as e:
        logger.error(f"Token info error: {e}")
        return {
            "user_id": session_data['user_id'],
            "tenant_id": session_data['tenant_id'],
            "expires_in": session_await data.get('expires_in', 3600),
            "needs_refresh": False
        }

@router.post("/auth/auto-refresh")
async def auto_refresh_check(request: Request):
    """Check if token needs refresh and optionally refresh it"""
    
    if not hasattr(request.state, 'authenticated') or not request.state.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Get token info
    token_info_response = await token_info(request)
    
    if token_info_await response.get('needs_refresh', False):
        # Automatically refresh token
        try:
            refresh_response = await refresh_token(request)
            return {
                "refreshed": True,
                "new_token": refresh_response['access_token'],
                "expires_in": refresh_response['expires_in'],
                "message": "Token automatically refreshed"
            }
        except Exception as e:
            return {
                "refreshed": False,
                "needs_manual_refresh": True,
                "error": str(e),
                "message": "Auto-refresh failed, manual refresh required"
            }
    else:
        return {
            "refreshed": False,
            "time_to_expiry": token_info_await response.get('time_to_expiry_seconds', 0),
            "message": "Token still valid, no refresh needed"
        }

print("🔧 Token refresh endpoints loaded successfully")
