"""
User router - User profile and tenant management endpoints.
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class TenantInfo(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool = True


class UserTenantsResponse(BaseModel):
    success: bool
    data: List[TenantInfo]


def get_tenant_id(request: Request) -> str:
    """Extract tenant_id from JWT token."""
    user = getattr(request.state, "user", None)
    if user:
        if hasattr(user, "tenant_id"):
            return user.tenant_id
        if isinstance(user, dict):
            return user.get("tenant_id", "")
    return request.headers.get("X-Tenant-ID", "")


@router.get("/user/tenants", response_model=UserTenantsResponse)
async def get_user_tenants(request: Request):
    """
    Get list of tenants the current user has access to.
    Returns the current tenant from JWT token.
    """
    tenant_id = get_tenant_id(request)
    
    # Return current tenant - multi-tenant switching not implemented yet
    if tenant_id:
        return UserTenantsResponse(
            success=True,
            data=[
                TenantInfo(
                    id=tenant_id,
                    name=tenant_id.replace("-", " ").title(),
                    slug=tenant_id,
                    is_active=True
                )
            ]
        )
    
    return UserTenantsResponse(success=True, data=[])
