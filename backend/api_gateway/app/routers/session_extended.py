from typing import Dict
from fastapi import Depends, HTTPException
from pydantic import BaseModel
import structlog

logger = structlog.get_logger(__name__)

class UserProfileResponse(BaseModel):
    id: str
    username: str
    email: str
    tenant_id: str
    role: str

# Mock function for getting current user (replace with actual implementation)
async def get_current_user_from_token(authorization: str = None) -> Dict:
    """Extract user info from JWT token"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    
    token = authorization.replace("Bearer ", "")
    
    # Mock user extraction from token (replace with actual JWT validation)
    if "jwt_mock_" in token:
        user_id = token.split("_")[2] if len(token.split("_")) > 2 else "unknown"
        return {
            "user_id": f"user_{user_id[:8]}",
            "username": "extracted_user",
            "email": "user@example.com", 
            "tenant_id": "konsultanpsikologi",
            "role": "owner"
        }
    
    raise HTTPException(status_code=401, detail="Invalid token")

@router.get("/me", response_model=UserProfileResponse)
async def get_user_profile(authorization: str = Header(None, alias="Authorization")):
    """
    Get current user profile using JWT token
    Compatible with React frontend authentication
    """
    try:
        logger.info("Getting user profile from token")
        
        current_user = await get_current_user_from_token(authorization)
        
        return UserProfileResponse(
            id=current_user["user_id"],
            username=current_user["username"],
            email=current_user["email"],
            tenant_id=current_user["tenant_id"],
            role=current_user["role"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user profile: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user profile")
