# Additional endpoint for user profile
@router.get("/me", response_model=UserProfileResponse)
async def get_user_profile(current_user: Dict = Depends(get_current_user)):
    """
    Get current user profile using JWT token
    """
    try:
        return UserProfileResponse(
            id=current_user.get("user_id"),
            username=current_user.get("username", "unknown"),
            email=current_user.get("email", "unknown"),
            tenant_id=current_user.get("tenant_id"),
            role=current_user.get("role", "user")
        )
    except Exception as e:
        logger.error(f"Error getting user profile: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user profile")

# Add response model
class UserProfileResponse(BaseModel):
    id: str
    username: str
    email: str
    tenant_id: str
    role: str
