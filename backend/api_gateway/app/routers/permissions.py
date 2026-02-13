"""
Permissions Router - Granular Permission Management
Manages granular_permissions table for feature-level access control.

Permission codes format: module:feature:action
Example: payroll:weekly:create, expense:transaction:read

tenant_id = '__SYSTEM__' for default permissions
tenant_id = actual tenant_id for tenant-specific overrides
"""
import logging
import uuid
from typing import Optional, List
from fastapi import APIRouter, Request, Query, HTTPException
from pydantic import BaseModel
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter(tags=["permissions"])


# === SCHEMAS ===

class PermissionItem(BaseModel):
    code: str
    is_granted: bool
    is_custom: bool = False  # True if this is a tenant override


class PermissionListResponse(BaseModel):
    success: bool = True
    data: List[PermissionItem]
    role_id: Optional[str] = None
    role_code: Optional[str] = None


class PermissionUpdateItem(BaseModel):
    code: str
    is_granted: bool


class PermissionUpdateRequest(BaseModel):
    permissions: List[PermissionUpdateItem]


class PermissionCheckResponse(BaseModel):
    granted: bool


# === HELPERS ===

def _get_user_context(request: Request) -> dict:
    """Extract user context from request state"""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context - missing tenant_id")
    return user


def _get_db_pool(request: Request):
    """Get database pool from app state"""
    if not hasattr(request.app.state, "db_pool") or not request.app.state.db_pool:
        raise HTTPException(status_code=500, detail="Database pool not available")
    return request.app.state.db_pool


async def _check_is_owner(conn, tenant_id: str, user_id: str) -> bool:
    """Check if user has OWNER role in tenant"""
    result = await conn.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM user_tenant_roles utr
            JOIN roles r ON r.id = utr.role_id
            WHERE utr.tenant_id = $1
            AND utr.user_id = $2::uuid
            AND r.code = 'OWNER'
            AND r.is_active = TRUE
        )
    """, tenant_id, user_id)
    return result


async def _get_user_role_id(conn, tenant_id: str, user_id: str) -> Optional[str]:
    """Get user's role_id in the tenant"""
    result = await conn.fetchval("""
        SELECT utr.role_id FROM user_tenant_roles utr
        WHERE utr.tenant_id = $1 AND utr.user_id = $2::uuid
        LIMIT 1
    """, tenant_id, user_id)
    return str(result) if result else None


def _is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID"""
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


async def _resolve_role_id(conn, role_id_or_code: str, tenant_id: str) -> tuple:
    """
    Resolve role_id and role info from either UUID or role code.
    Returns (role_id, role_code, role_name) or raises HTTPException if not found.
    """
    if _is_valid_uuid(role_id_or_code):
        # It's a UUID, query by id
        role = await conn.fetchrow(
            "SELECT id, code, name FROM roles WHERE id = $1::uuid", 
            role_id_or_code
        )
    else:
        # It's a code (like "OWNER", "CASHIER"), query by code
        # Check both tenant-specific roles and system roles
        role = await conn.fetchrow("""
            SELECT id, code, name FROM roles 
            WHERE UPPER(code) = $1 
            AND (tenant_id = $2 OR tenant_id = '__SYSTEM__' OR tenant_id IS NULL)
            AND is_active = TRUE
            ORDER BY 
                CASE WHEN tenant_id = $2 THEN 0 ELSE 1 END
            LIMIT 1
        """, role_id_or_code.upper(), tenant_id)
    
    if not role:
        raise HTTPException(status_code=404, detail=f"Role not found: {role_id_or_code}")
    
    return str(role["id"]), role["code"], role["name"]


# === ENDPOINTS ===

@router.get("/role/{role_id}")
async def get_role_permissions(request: Request, role_id: str):
    """
    Get all permissions for a role (merged system defaults + tenant overrides).
    Returns permissions with is_custom flag indicating tenant-specific overrides.
    
    role_id can be:
    - A UUID (e.g., "550e8400-e29b-41d4-a716-446655440000")
    - A role code (e.g., "OWNER", "CASHIER", "STAFF") - case insensitive
    """
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]

        pool = _get_db_pool(request)
        async with pool.acquire() as conn:
            # Resolve role_id (accepts both UUID and code)
            resolved_role_id, role_code, role_name = await _resolve_role_id(conn, role_id, tenant_id)

            # Get merged permissions: system defaults + tenant overrides
            # Tenant overrides take precedence
            query = """
                WITH system_perms AS (
                    SELECT permission_code, is_granted, false as is_custom
                    FROM granular_permissions
                    WHERE role_id = $1::uuid AND tenant_id = '__SYSTEM__'
                ),
                tenant_perms AS (
                    SELECT permission_code, is_granted, true as is_custom
                    FROM granular_permissions
                    WHERE role_id = $1::uuid AND tenant_id = $2
                )
                SELECT 
                    COALESCE(tp.permission_code, sp.permission_code) as code,
                    COALESCE(tp.is_granted, sp.is_granted) as is_granted,
                    COALESCE(tp.is_custom, false) as is_custom
                FROM system_perms sp
                FULL OUTER JOIN tenant_perms tp ON sp.permission_code = tp.permission_code
                ORDER BY code
            """
            rows = await conn.fetch(query, resolved_role_id, tenant_id)

            permissions = [
                {
                    "code": row["code"],
                    "is_granted": row["is_granted"],
                    "is_custom": row["is_custom"]
                } for row in rows
            ]

            return {
                "success": True,
                "data": permissions,
                "role_id": resolved_role_id,
                "role_code": role_code
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting role permissions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get permissions: {str(e)}")


@router.put("/role/{role_id}")
async def update_role_permissions(request: Request, role_id: str, data: PermissionUpdateRequest):
    """
    Update permissions for a role. Creates tenant-specific overrides.
    Only OWNER can update permissions.
    
    role_id can be:
    - A UUID (e.g., "550e8400-e29b-41d4-a716-446655440000")
    - A role code (e.g., "OWNER", "CASHIER", "STAFF") - case insensitive
    """
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        user_id = user["user_id"]

        pool = _get_db_pool(request)
        async with pool.acquire() as conn:
            # Check if user is OWNER
            is_owner = await _check_is_owner(conn, tenant_id, user_id)
            if not is_owner:
                raise HTTPException(status_code=403, detail="Only Owner can update permissions")

            # Resolve role_id (accepts both UUID and code)
            resolved_role_id, role_code, role_name = await _resolve_role_id(conn, role_id, tenant_id)

            # Upsert tenant-specific overrides
            updated_count = 0
            for perm in data.permissions:
                # Validate permission code exists in system defaults
                valid = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM granular_permissions 
                        WHERE tenant_id = '__SYSTEM__' 
                        AND role_id = $1::uuid 
                        AND permission_code = $2
                    )
                """, resolved_role_id, perm.code)

                if not valid:
                    logger.warning(f"Permission code {perm.code} not found for role {resolved_role_id}")
                    continue

                # Upsert tenant override
                await conn.execute("""
                    INSERT INTO granular_permissions (tenant_id, role_id, permission_code, is_granted, updated_at, granted_by)
                    VALUES ($1, $2::uuid, $3, $4, NOW(), $5::uuid)
                    ON CONFLICT (role_id, tenant_id, permission_code) 
                    DO UPDATE SET is_granted = EXCLUDED.is_granted, updated_at = NOW(), granted_by = EXCLUDED.granted_by
                """, tenant_id, resolved_role_id, perm.code, perm.is_granted, user_id)
                updated_count += 1

            # Return updated permissions using resolved role_id
            return await get_role_permissions(request, resolved_role_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating role permissions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update permissions: {str(e)}")


@router.post("/role/{role_id}/reset")
async def reset_role_permissions(request: Request, role_id: str):
    """
    Reset role permissions to system defaults.
    Deletes all tenant-specific overrides for this role.
    Only OWNER can reset permissions.
    
    role_id can be:
    - A UUID (e.g., "550e8400-e29b-41d4-a716-446655440000")
    - A role code (e.g., "OWNER", "CASHIER", "STAFF") - case insensitive
    """
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        user_id = user["user_id"]

        pool = _get_db_pool(request)
        async with pool.acquire() as conn:
            # Check if user is OWNER
            is_owner = await _check_is_owner(conn, tenant_id, user_id)
            if not is_owner:
                raise HTTPException(status_code=403, detail="Only Owner can reset permissions")

            # Resolve role_id (accepts both UUID and code)
            resolved_role_id, role_code, role_name = await _resolve_role_id(conn, role_id, tenant_id)

            # Delete tenant-specific overrides (keep __SYSTEM__)
            result = await conn.execute("""
                DELETE FROM granular_permissions 
                WHERE role_id = $1::uuid AND tenant_id = $2 AND tenant_id != '__SYSTEM__'
            """, resolved_role_id, tenant_id)

            # Parse deleted count
            deleted_count = int(result.split()[-1]) if result else 0

            return {
                "success": True,
                "message": f"Reset {deleted_count} permission override(s) to system defaults",
                "data": {"deleted_count": deleted_count}
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting role permissions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reset permissions: {str(e)}")


@router.get("/check")
async def check_permission(request: Request, code: str = Query(..., description="Permission code to check")):
    """
    Check if the current user has a specific permission.
    Returns { granted: true/false }
    """
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        user_id = user["user_id"]

        pool = _get_db_pool(request)
        async with pool.acquire() as conn:
            # Get user's role_id
            role_id = await _get_user_role_id(conn, tenant_id, user_id)
            if not role_id:
                return {"granted": False}

            # Check permission with tenant override priority
            query = """
                SELECT COALESCE(
                    (SELECT is_granted FROM granular_permissions 
                     WHERE tenant_id = $1 AND role_id = $2::uuid AND permission_code = $3),
                    (SELECT is_granted FROM granular_permissions 
                     WHERE tenant_id = '__SYSTEM__' AND role_id = $2::uuid AND permission_code = $3),
                    FALSE
                ) as granted
            """
            result = await conn.fetchval(query, tenant_id, role_id, code)

            return {"granted": result or False}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking permission: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to check permission: {str(e)}")


@router.get("/my-permissions")
async def get_my_permissions(request: Request):
    """
    Get all permissions for the current user based on their role.
    """
    try:
        user = _get_user_context(request)
        tenant_id = user["tenant_id"]
        user_id = user["user_id"]

        pool = _get_db_pool(request)
        async with pool.acquire() as conn:
            # Get user's role_id
            role_id = await _get_user_role_id(conn, tenant_id, user_id)
            if not role_id:
                return {"success": True, "data": []}

            # Get merged permissions
            query = """
                WITH system_perms AS (
                    SELECT permission_code, is_granted, false as is_custom
                    FROM granular_permissions
                    WHERE role_id = $1::uuid AND tenant_id = '__SYSTEM__'
                ),
                tenant_perms AS (
                    SELECT permission_code, is_granted, true as is_custom
                    FROM granular_permissions
                    WHERE role_id = $1::uuid AND tenant_id = $2
                )
                SELECT 
                    COALESCE(tp.permission_code, sp.permission_code) as code,
                    COALESCE(tp.is_granted, sp.is_granted) as is_granted,
                    COALESCE(tp.is_custom, false) as is_custom
                FROM system_perms sp
                FULL OUTER JOIN tenant_perms tp ON sp.permission_code = tp.permission_code
                ORDER BY code
            """
            rows = await conn.fetch(query, role_id, tenant_id)

            permissions = [
                {
                    "code": row["code"],
                    "is_granted": row["is_granted"],
                    "is_custom": row["is_custom"]
                } for row in rows
            ]

            return {"success": True, "data": permissions}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting my permissions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get permissions: {str(e)}")
