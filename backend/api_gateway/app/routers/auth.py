import asyncio
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status, Request
from pydantic import BaseModel
from backend.api_gateway.app.services.auth_instance import auth_client
from backend.api_gateway.app.services.audit_logger import log_auth_event, AuditEventType

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize auth client

# =====================================================
# REQUEST/RESPONSE MODELS
# =====================================================

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    username: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class ValidateTokenRequest(BaseModel):
    access_token: str

class AuthResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

# =====================================================
# AUTHENTICATION ENDPOINTS
# =====================================================

@router.post("/register", response_model=AuthResponse)
async def register_user(request: RegisterRequest, http_request: Request):
    """User registration endpoint"""
    try:
        logger.info(f"Registration request for email: {request.email}")
        
        # Connect to auth service
        
        # Call registration service
        result = await auth_client.register_user(
            email=request.email,
            password=request.password,
            name=request.name,
            username=request.username
        )
        
        if result["success"]:
            # Log successful registration
            await log_auth_event(
                event_type=AuditEventType.REGISTER,
                user_id=result["user_id"],
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={"email": request.email}
            )
            
            return AuthResponse(
                success=True,
                message="Registration successful",
                data={
                    "user_id": result["user_id"],
                    "email": request.email,
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"]
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result["message"]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed due to server error"
        )
    finally:
        await auth_client.disconnect()

@router.post("/login", response_model=AuthResponse)
async def login_user(request: LoginRequest, http_request: Request):
    """User login endpoint"""
    try:
        logger.info(f"Login request for email: {request.email}")
        
        # Connect to auth service
        
        # Call login service
        result = await auth_client.login_user(
            email=request.email,
            password=request.password
        )
        
        if result["success"]:
            # Log successful login
            await log_auth_event(
                event_type=AuditEventType.LOGIN,
                user_id=result["user_id"],
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={
                    "email": result["email"],
                    "tenant_id": result.get("tenant_id")
                }
            )
            
            return AuthResponse(
                success=True,
                message="Login successful",
                data={
                    "user_id": result["user_id"],
                    "email": result["email"],
                    "name": result["name"],
                    "role": result["role"],
                    "tenant_id": result["tenant_id"],
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"]
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=result["message"]
            )
            
    except HTTPException as http_exc:
        # Log failed login (HTTP exceptions like 401)
        await log_auth_event(
            event_type=AuditEventType.FAILED_LOGIN,
            ip_address=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
            success=False,
            error_message=str(http_exc.detail),
            metadata={"email": request.email}
        )
        raise
    except Exception as e:
        # Log failed login (server errors)
        logger.error(f"Login error: {e}")
        await log_auth_event(
            event_type=AuditEventType.FAILED_LOGIN,
            ip_address=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
            success=False,
            error_message=str(e),
            metadata={"email": request.email}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed due to server error"
        )
    finally:
        await auth_client.disconnect()

@router.post("/validate", response_model=AuthResponse)
async def validate_token(request: ValidateTokenRequest):
    """Token validation endpoint"""
    try:
        logger.info("Token validation request")
        
        # Connect to auth service
        
        # Call token validation service
        result = await auth_client.validate_token(request.access_token)
        
        if result["valid"]:
            return AuthResponse(
                success=True,
                message="Token is valid",
                data={
                    "user_id": result["user_id"],
                    "tenant_id": result["tenant_id"],
                    "role": result["role"],
                    "expires_at": result["expires_at"]
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=result["message"]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token validation failed due to server error"
        )
    finally:
        await auth_client.disconnect()

@router.get("/profile/{user_id}", response_model=AuthResponse)
async def get_user_profile(user_id: str):
    """Get user profile endpoint"""
    try:
        logger.info(f"Get profile request for user: {user_id}")
        
        # Connect to auth service
        
        # Call profile service
        result = await auth_client.get_user_profile(user_id)
        
        if result["success"]:
            return AuthResponse(
                success=True,
                message="Profile retrieved successfully",
                data={
                    "user_id": result["user_id"],
                    "email": result["email"],
                    "name": result["name"],
                    "username": result["username"],
                    "role": result["role"]
                }
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result["message"]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get profile error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Get profile failed due to server error"
        )
    finally:
        await auth_client.disconnect()

@router.get("/health")
async def auth_health_check():
    """Auth service health check"""
    try:
        await auth_client.disconnect()
        return {"status": "healthy", "service": "auth"}
    except Exception as e:
        logger.error(f"Auth health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service unavailable"
        )


# =====================================================
# WEEK 2 DAY 4: TOKEN REFRESH & SESSION MANAGEMENT
# =====================================================

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None
    logout_all_devices: bool = False

class SessionResponse(BaseModel):
    session_id: str
    device: Optional[str] = "Unknown"
    ip_address: Optional[str] = None
    created_at: Optional[str] = None
    last_active: Optional[str] = None

@router.post("/refresh", response_model=AuthResponse)
async def refresh_access_token(data: RefreshTokenRequest, http_request: Request):
    """
    Refresh access token using refresh token
    
    Request:
        - refresh_token: Valid refresh token
        
    Response:
        - success: Boolean
        - access_token: New JWT access token
        - refresh_token: New refresh token
        - expires_at: Token expiration timestamp
    """
    try:
        logger.info("Token refresh request received")
        
        # Call auth service
        result = await auth_client.refresh_token(data.refresh_token)
        
        if result.get("success"):
            logger.info("Token refreshed successfully")
            
            # Log successful token refresh
            await log_auth_event(
                event_type=AuditEventType.TOKEN_REFRESH,
                user_id=result.get("user_id"),
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True
            )
            
            return AuthResponse(
                success=True,
                message="Token refreshed successfully",
                data={
                    "access_token": result["access_token"],
                    "refresh_token": result["refresh_token"],
                    "expires_at": result.get("expires_at")
                }
            )
        else:
            error_msg = result.get("error", "Token refresh failed")
            logger.warning(f"Token refresh failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_msg
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in refresh endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh error: {str(e)}"
        )

@router.get("/sessions")
async def list_user_sessions(user_id: str):
    """
    List all active sessions for authenticated user
    
    Query Parameters:
        - user_id: User ID (from JWT token in production)
        
    Response:
        - success: Boolean
        - sessions: List of active sessions
        - total: Total session count
    """
    try:
        logger.info(f"Listing sessions for user: {user_id}")
        
        # Call auth service
        result = await auth_client.list_active_sessions(user_id)
        
        if result.get("success"):
            logger.info(f"Found {result.get('total', 0)} active sessions")
            return {
                "success": True,
                "data": {
                    "sessions": result["sessions"],
                    "total": result["total"]
                }
            }
        else:
            error_msg = result.get("error", "Failed to list sessions")
            logger.warning(f"List sessions failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in list sessions endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"List sessions error: {str(e)}"
        )

@router.delete("/sessions/{session_id}")
async def revoke_user_session(session_id: str, user_id: str, http_request: Request):
    """
    Revoke a specific user session (logout from device)
    
    Path Parameters:
        - session_id: Session ID to revoke
        
    Query Parameters:
        - user_id: User ID (from JWT token in production)
        
    Response:
        - success: Boolean
        - message: Success message
    """
    try:
        logger.info(f"Revoking session {session_id} for user {user_id}")
        
        # Call auth service
        result = await auth_client.revoke_session(session_id, user_id)
        
        if result.get("success"):
            logger.info(f"Session {session_id} revoked successfully")
            
            # Log session revocation
            await log_auth_event(
                event_type=AuditEventType.SESSION_REVOKED,
                user_id=user_id,
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={"session_id": session_id}
            )
            
            return {
                "success": True,
                "message": result.get("message", "Session revoked successfully")
            }
        else:
            error_msg = result.get("error", "Failed to revoke session")
            logger.warning(f"Revoke session failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in revoke session endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Revoke session error: {str(e)}"
        )

@router.post("/logout", response_model=AuthResponse)
async def logout_user(data: LogoutRequest, user_id: str, http_request: Request):
    """
    Logout user - revoke refresh token(s)
    
    Query Parameters:
        - user_id: User ID (from JWT token in production)
        
    Request Body:
        - refresh_token: Specific token to revoke (optional)
        - logout_all_devices: If true, logout from all devices
        
    Response:
        - success: Boolean
        - message: Success message
        - revoked_tokens: Number of tokens revoked
    """
    try:
        logger.info(f"Logout request for user: {user_id}")
        
        # Call auth service
        result = await auth_client.logout(
            user_id=user_id,
            refresh_token=data.refresh_token,
            logout_all_devices=data.logout_all_devices
        )
        
        if result.get("success"):
            logger.info(f"User {user_id} logged out successfully")
            
            # Log logout event
            await log_auth_event(
                event_type=AuditEventType.LOGOUT,
                user_id=user_id,
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={
                    "logout_all_devices": data.logout_all_devices,
                    "revoked_tokens": result.get("revoked_tokens", 0)
                }
            )
            
            return AuthResponse(
                success=True,
                message=result.get("message", "Logged out successfully"),
                data={
                    "revoked_tokens": result.get("revoked_tokens", 0)
                }
            )
        else:
            error_msg = result.get("error", "Logout failed")
            logger.warning(f"Logout failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in logout endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout error: {str(e)}"
        )

class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None
    logout_all_devices: bool = False

@router.post("/logout", response_model=AuthResponse)
async def logout_user(data: LogoutRequest, user_id: str, http_request: Request):
    """
    Logout user - revoke refresh token(s)
    
    Query Parameters:
        - user_id: User ID (from JWT token in production)
        
    Request Body:
        - refresh_token: Specific token to revoke (optional)
        - logout_all_devices: If true, logout from all devices
        
    Response:
        - success: Boolean
        - message: Success message
        - revoked_tokens: Number of tokens revoked
    """
    try:
        logger.info(f"Logout request for user: {user_id}")
        
        # Call auth service
        result = await auth_client.logout(
            user_id=user_id,
            refresh_token=data.refresh_token,
            logout_all_devices=data.logout_all_devices
        )
        
        if result.get("success"):
            logger.info(f"User {user_id} logged out successfully")
            
            # Log logout event
            await log_auth_event(
                event_type=AuditEventType.LOGOUT,
                user_id=user_id,
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                success=True,
                metadata={
                    "logout_all_devices": data.logout_all_devices,
                    "revoked_tokens": result.get("revoked_tokens", 0)
                }
            )
            
            return AuthResponse(
                success=True,
                message=result.get("message", "Logged out successfully"),
                data={
                    "revoked_tokens": result.get("revoked_tokens", 0)
                }
            )
        else:
            error_msg = result.get("error", "Logout failed")
            logger.warning(f"Logout failed: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in logout endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout error: {str(e)}"
        )