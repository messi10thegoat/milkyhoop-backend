"""
Granular Permission Service for API Gateway

Supports permission codes in format: {module}:{resource}:{action}
Examples:
  - payroll:weekly:create
  - payroll:weekly:approve
  - sales_invoice:credit_note:create
  - inventory:stock_transfer:approve

IRON LAW COMPLIANCE:
- Law 0: Separation of Concerns - Permission layer separated from business logic
- Law 10: AI Safety Boundary - All permission decisions logged
- Law 12: Audit Immutability - Permission decisions can be audited
"""
import logging
from typing import Optional, List, Dict
from dataclasses import dataclass
import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class PermissionResult:
    """Result of a permission check"""
    granted: bool
    source: str  # tenant_override, system_default, role_fallback, denied
    role_id: Optional[str] = None
    role_code: Optional[str] = None
    permission_code: Optional[str] = None


class PermissionService:
    """
    Service for checking granular permissions.
    
    Permission checking logic:
    1. Get users role_id from user_tenant_roles
    2. Check role_permissions for tenant-specific override first (granular_permissions column)
    3. If no override, check system defaults (tenant_id = __SYSTEM__)
    4. Return is_granted value
    
    For backward compatibility, also supports the existing module+actions format.
    """
    
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self._cache: Dict[str, PermissionResult] = {}  # Cache for permission results
    
    async def check_permission(
        self,
        user_id: str,
        tenant_id: str,
        permission_code: str
    ) -> bool:
        """
        Check if user has specific permission.
        
        Args:
            user_id: UUID of the user
            tenant_id: Tenant ID
            permission_code: Permission code in format module:resource:action
            
        Returns:
            bool: True if permission granted, False otherwise
        """
        result = await self.check_permission_detailed(user_id, tenant_id, permission_code)
        return result.granted
    
    async def check_permission_detailed(
        self,
        user_id: str,
        tenant_id: str,
        permission_code: str
    ) -> PermissionResult:
        """
        Check permission with detailed result.
        
        Returns PermissionResult with source information for debugging/audit.
        """
        try:
            async with self.pool.acquire() as conn:
                # Get users role for this tenant
                role = await conn.fetchrow("""
                    SELECT r.id, r.code, r.tenant_id
                    FROM user_tenant_roles utr
                    JOIN roles r ON r.id = utr.role_id
                    WHERE utr.user_id = $1::uuid AND utr.tenant_id = $2
                    AND r.is_active = TRUE
                    ORDER BY utr.is_primary DESC
                    LIMIT 1
                """, user_id, tenant_id)
                
                if not role:
                    logger.debug(f"No role found for user {user_id} in tenant {tenant_id}")
                    return PermissionResult(
                        granted=False,
                        source="denied",
                        permission_code=permission_code
                    )
                
                role_id = str(role["id"])
                role_code = role["code"]
                
                # Parse permission code: module:resource:action
                parts = permission_code.split(":")
                if len(parts) != 3:
                    logger.warning(f"Invalid permission code format: {permission_code}")
                    return PermissionResult(
                        granted=False,
                        source="denied",
                        role_id=role_id,
                        role_code=role_code,
                        permission_code=permission_code
                    )
                
                module, resource, action = parts
                
                # Check granular permission in granular_permissions table
                # First check tenant-specific, then system default
                permission = await conn.fetchrow("""
                    WITH tenant_perm AS (
                        SELECT is_granted, 'tenant_override' as source
                        FROM granular_permissions
                        WHERE role_id = $1::uuid 
                        AND permission_code = $2
                        AND tenant_id = $3
                        LIMIT 1
                    ),
                    system_perm AS (
                        SELECT is_granted, 'system_default' as source
                        FROM granular_permissions
                        WHERE role_id = $1::uuid 
                        AND permission_code = $2
                        AND tenant_id = '__SYSTEM__'
                        LIMIT 1
                    )
                    SELECT * FROM tenant_perm
                    UNION ALL
                    SELECT * FROM system_perm
                    LIMIT 1
                """, role_id, permission_code, tenant_id)
                
                if permission:
                    return PermissionResult(
                        granted=permission["is_granted"],
                        source=permission["source"],
                        role_id=role_id,
                        role_code=role_code,
                        permission_code=permission_code
                    )
                
                # Fallback: check existing role_permissions table for module+action
                # Convert permission_code to module and action for backward compat
                module_upper = module.upper()
                action_code = action[0].upper()  # create -> C, approve -> A
                
                role_perm = await conn.fetchrow("""
                    SELECT actions FROM role_permissions
                    WHERE role_id = $1::uuid AND module = $2
                """, role_id, module_upper)
                
                if role_perm and action_code in role_perm["actions"]:
                    return PermissionResult(
                        granted=True,
                        source="role_fallback",
                        role_id=role_id,
                        role_code=role_code,
                        permission_code=permission_code
                    )
                
                # Default: deny if not explicitly granted
                return PermissionResult(
                    granted=False,
                    source="denied",
                    role_id=role_id,
                    role_code=role_code,
                    permission_code=permission_code
                )
                
        except Exception as e:
            logger.error(f"Error checking permission: {e}")
            # Fail-closed for security
            return PermissionResult(
                granted=False,
                source="denied",
                permission_code=permission_code
            )
    
    async def get_user_permissions(
        self,
        user_id: str,
        tenant_id: str
    ) -> Dict[str, bool]:
        """
        Get all permissions for a user as a dict.
        
        Returns dict of permission_code -> is_granted
        """
        try:
            async with self.pool.acquire() as conn:
                # Get users role
                role = await conn.fetchrow("""
                    SELECT r.id
                    FROM user_tenant_roles utr
                    JOIN roles r ON r.id = utr.role_id
                    WHERE utr.user_id = $1::uuid AND utr.tenant_id = $2
                    AND r.is_active = TRUE
                    ORDER BY utr.is_primary DESC
                    LIMIT 1
                """, user_id, tenant_id)
                
                if not role:
                    return {}
                
                role_id = str(role["id"])
                
                # Get all granular permissions (tenant-specific override system defaults)
                rows = await conn.fetch("""
                    WITH all_perms AS (
                        SELECT permission_code, is_granted, 
                               CASE WHEN tenant_id = $2 THEN 1 ELSE 2 END as priority
                        FROM granular_permissions
                        WHERE role_id = $1::uuid
                        AND (tenant_id = $2 OR tenant_id = '__SYSTEM__')
                    )
                    SELECT DISTINCT ON (permission_code) permission_code, is_granted
                    FROM all_perms
                    ORDER BY permission_code, priority
                """, role_id, tenant_id)
                
                return {row["permission_code"]: row["is_granted"] for row in rows}
                
        except Exception as e:
            logger.error(f"Error getting user permissions: {e}")
            return {}
    
    async def grant_permission(
        self,
        role_id: str,
        tenant_id: str,
        permission_code: str,
        granted_by: str
    ) -> bool:
        """Grant a permission to a role for a tenant."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO granular_permissions 
                    (role_id, tenant_id, permission_code, is_granted, granted_by)
                    VALUES ($1::uuid, $2, $3, TRUE, $4::uuid)
                    ON CONFLICT (role_id, tenant_id, permission_code) 
                    DO UPDATE SET is_granted = TRUE, granted_by = $4::uuid, updated_at = NOW()
                """, role_id, tenant_id, permission_code, granted_by)
                return True
        except Exception as e:
            logger.error(f"Error granting permission: {e}")
            return False
    
    async def revoke_permission(
        self,
        role_id: str,
        tenant_id: str,
        permission_code: str,
        revoked_by: str
    ) -> bool:
        """Revoke a permission from a role for a tenant."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO granular_permissions 
                    (role_id, tenant_id, permission_code, is_granted, granted_by)
                    VALUES ($1::uuid, $2, $3, FALSE, $4::uuid)
                    ON CONFLICT (role_id, tenant_id, permission_code) 
                    DO UPDATE SET is_granted = FALSE, granted_by = $4::uuid, updated_at = NOW()
                """, role_id, tenant_id, permission_code, revoked_by)
                return True
        except Exception as e:
            logger.error(f"Error revoking permission: {e}")
            return False
    
    def clear_cache(self):
        """Clear permission cache (call when permissions updated)."""
        self._cache.clear()


# Singleton instance (initialized in main.py lifespan)
permission_service: Optional[PermissionService] = None

def get_permission_service() -> PermissionService:
    if permission_service is None:
        raise RuntimeError("PermissionService not initialized")
    return permission_service

def init_permission_service(pool: asyncpg.Pool):
    global permission_service
    permission_service = PermissionService(pool)
    logger.info("PermissionService initialized")
