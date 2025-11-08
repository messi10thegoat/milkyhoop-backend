"""
Authentication Dependencies
Reusable auth dependencies for route protection
"""
import logging
from typing import Dict
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from backend.api_gateway.app.services.auth_instance import auth_client

logger = logging.getLogger(__name__)

security = HTTPBearer()


async def get_current_user_from_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict:
    """
    DEPRECATED: Use request.state.user instead (set by middleware)
    
    This function validates token via auth service.
    However, middleware already does this validation.
    
    For new routes, prefer accessing request.state.user directly.
    This function kept for backward compatibility.
    """
    token = credentials.credentials
    
    try:
        result = await auth_client.validate_token(token)
        
        if not result.get("valid"):
            logger.warning(f"Invalid token: {result.get('message', 'Unknown error')}")
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "Invalid token",
                    "message": result.get("message", "Token validation failed")
                }
            )
        
        return {
            "user_id": result.get("user_id"),
            "tenant_id": result.get("tenant_id", "default"),
            "role": result.get("role", "USER"),
            "email": result.get("email"),
            "username": result.get("username")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Authentication failed"
        )


def get_current_user(request: Request) -> Dict:
    """
    Get current user from request state (set by middleware)
    
    RECOMMENDED: Use this for all protected routes
    Middleware already validated token and set request.state.user
    
    Usage:
        @router.get("/protected")
        async def protected_route(user: dict = Depends(get_current_user)):
            return {"user_id": user["user_id"]}
    
    Returns:
        dict: User info from middleware validation
    
    Raises:
        HTTPException: 401 if not authenticated
    """
    if not hasattr(request.state, 'user') or request.state.user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )
    
    return request.state.user


async def verify_admin(
    current_user: Dict = Depends(get_current_user)
) -> Dict:
    """
    Verify user has ADMIN role
    
    Usage:
        @router.get("/admin")
        async def admin_route(user: dict = Depends(verify_admin)):
            return {"admin": True}
    """
    if current_user.get("role") != "ADMIN":
        logger.warning(f"Non-admin access attempt by user {current_user.get('user_id')}")
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )
    
    return current_user


async def verify_tenant_access(
    tenant_id: str,
    current_user: Dict = Depends(get_current_user)
) -> Dict:
    """
    Verify user has access to specific tenant
    
    Usage:
        @router.get("/{tenant_id}/data")
        async def tenant_data(
            tenant_id: str,
            user: dict = Depends(lambda: verify_tenant_access(tenant_id))
        ):
            return {"data": "..."}
    """
    user_tenant = current_user.get("tenant_id")
    user_role = current_user.get("role")
    
    # Admin can access all tenants
    if user_role == "ADMIN":
        return current_user
    
    # Regular user can only access their own tenant
    if user_tenant != tenant_id:
        logger.warning(
            f"Unauthorized tenant access: user {current_user.get('user_id')} "
            f"tried to access {tenant_id} (owns {user_tenant})"
        )
        raise HTTPException(
            status_code=403,
            detail="Access denied to this tenant"
        )
    
    return current_user
