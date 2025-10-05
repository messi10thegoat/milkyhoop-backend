# USER PROFILE ENDPOINT - Add to session.py

from fastapi import Header, HTTPException
from pydantic import BaseModel

class UserProfileResponse(BaseModel):
    id: str
    username: str
    email: str
    tenant_id: str
    role: str

@router.get("/me", response_model=UserProfileResponse)
async def get_user_profile(authorization: str = Header(None, alias="Authorization")):
    """
    Get current user profile using JWT token
    Required by React frontend for authentication state
    """
    try:
        logger.info("🔍 User profile request with token")
        
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
        
        token = authorization.replace("Bearer ", "")
        
        # Extract user info from mock JWT token
        if "jwt_mock_" in token:
            parts = token.split("_")
            if len(parts) >= 3:
                user_id = parts[1]
                timestamp = parts[2]
                
                return UserProfileResponse(
                    id=f"user_{user_id}",
                    username=f"user_{user_id[:8]}",
                    email=f"user_{user_id[:8]}@example.com",
                    tenant_id="konsultanpsikologi",
                    role="owner"
                )
        
        raise HTTPException(status_code=401, detail="Invalid token format")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_user_profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
