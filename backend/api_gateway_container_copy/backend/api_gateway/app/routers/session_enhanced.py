"""
Session Management Router - Phase 2 Implementation + Authentication Endpoints
Session CRUD endpoints per documentation + Login/Register handlers
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import structlog
import hashlib
import time

# Import session manager
import sys
sys.path.append('/app/backend/api_gateway/app')
from services.session_manager import SessionManager

logger = structlog.get_logger(__name__)
router = APIRouter()

# Initialize session manager
session_manager = SessionManager()

# ✅ NEW: Pydantic models for auth endpoints
class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    email: str
    password: str
    username: str
    business_name: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: Dict[str, Any]

class RegisterResponse(BaseModel):
    message: str
    user_id: str

def get_current_user(request: Request) -> Dict[str, Any]:
    """Dependency to get current authenticated user"""
    if not hasattr(request.state, 'authenticated') or not request.state.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    return request.state.user

# ✅ NEW: Login endpoint (POST /api/auth/login)
@router.post("/login", response_model=LoginResponse)
async def login(login_request: LoginRequest):
    """
    User authentication endpoint
    Mock implementation for Phase 3 frontend integration
    """
    logger.info("🔐 Login attempt", email=login_request.email)
    
    # Mock authentication logic (replace with real auth service call)
    if login_request.email and login_request.password:
        # Generate mock JWT token
        mock_token = f"jwt_mock_{hashlib.md5(login_request.email.encode()).hexdigest()[:8]}_{int(time.time())}"
        
        # Create mock user data
        mock_user = {
            "id": f"user_{hashlib.md5(login_request.email.encode()).hexdigest()[:8]}",
            "username": login_request.email.split('@')[0],
            "email": login_request.email,
            "tenant_id": "konsultanpsikologi",  # Keep existing tenant for compatibility
            "role": "owner"
        }
        
        logger.info("✅ Mock login successful", user_id=mock_user["id"])
        
        return LoginResponse(
            access_token=mock_token,
            token_type="bearer", 
            user=mock_user
        )
    else:
        logger.warning("❌ Login failed - missing credentials")
        raise HTTPException(status_code=400, detail="Email and password required")

# ✅ NEW: Register endpoint (POST /api/auth/register)
@router.post("/register", response_model=RegisterResponse)
async def register(register_request: RegisterRequest):
    """
    User registration endpoint
    Mock implementation for Phase 3 frontend integration
    """
    logger.info("📝 Registration attempt", email=register_request.email, username=register_request.username)
    
    # Mock registration logic (replace with real auth service call)
    if register_request.email and register_request.password and register_request.username:
        
        # Generate mock user ID
        mock_user_id = f"user_{hashlib.md5(register_request.email.encode()).hexdigest()[:8]}"
        
        logger.info("✅ Mock registration successful", user_id=mock_user_id)
        
        return RegisterResponse(
            message="Registration successful. Please log in to continue.",
            user_id=mock_user_id
        )
    else:
        logger.warning("❌ Registration failed - missing required fields")
        raise HTTPException(status_code=400, detail="Email, password, and username required")

# ✅ EXISTING: Session management endpoints (preserved)
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
async def logout_all_sessions(request: Request, current_user: Dict = Depends(get_current_user)):
    """
    Logout all user sessions
    Per Phase 2 documentation: terminate all user sessions
    """
    
    user_id = current_user.get('user_id')
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found in token")
    
    # Revoke all user sessions
    revoked_count = session_manager.revoke_all_user_sessions(user_id)
    
    logger.info("✅ All sessions logout successful", user_id=user_id, revoked_count=revoked_count)
    return {
        "message": "All sessions logged out successfully", 
        "revoked_sessions": revoked_count,
        "status": "all_sessions_revoked"
    }

@router.get("/sessions")
async def list_active_sessions(current_user: Dict = Depends(get_current_user)):
    """
    List user's active sessions
    Per Phase 2 documentation: session management
    """
    
    user_id = current_user.get('user_id')
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID not found in token")
    
    # Get user sessions
    sessions = session_manager.get_user_sessions(user_id)
    
    logger.info("📋 Sessions listed", user_id=user_id, session_count=len(sessions))
    return {
        "user_id": user_id,
        "active_sessions": len(sessions),
        "sessions": sessions
    }

@router.get("/health")
async def session_health():
    """
    Session service health check - PUBLIC endpoint
    Per Phase 2 documentation: Redis connectivity check
    """
    
    redis_healthy = session_manager.health_check()
    
    return {
        "service": "session_manager",
        "redis_connection": "healthy" if redis_healthy else "unhealthy", 
        "status": "operational" if redis_healthy else "degraded"
    }
