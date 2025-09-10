# Add this code to the end of session.py before the last line

from fastapi import Header

class UserProfileResponse(BaseModel):
    id: str
    username: str 
    email: str
    tenant_id: str
    role: str

@router.get("/me", response_model=UserProfileResponse)
async def get_user_profile(authorization: str = Header(None, alias="Authorization")):
    """Get current user profile using JWT token"""
    try:
        logger.info("🔍 User profile request")
        
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        
        token = authorization.replace("Bearer ", "")
        
        # Extract user from mock JWT
        if "jwt_mock_" in token:
            parts = token.split("_")
            if len(parts) >= 3:
                user_id = parts[1]
                return UserProfileResponse(
                    id=f"user_{user_id}",
                    username=f"user_{user_id[:8]}",
                    email=f"user_{user_id[:8]}@example.com", 
                    tenant_id="konsultanpsikologi",
                    role="owner"
                )
        
        raise HTTPException(status_code=401, detail="Invalid token")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profile endpoint error: {e}")
        raise HTTPException(status_code=500, detail="Server error")
