"""
Permission Service

Handles permission checks against role_permissions table.
IRON LAW 0: Separation of Concerns - fokus hanya pada permission logic.
"""
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from ..models.policy_models import User, Resource, DecisionReason
from ..config import settings

logger = logging.getLogger(__name__)


# SQL Queries
GET_ROLE_PERMISSIONS = """
    SELECT 
        rp.module,
        rp.actions,
        rp.max_confidentiality,
        rp.entity_types,
        rp.restrictions
    FROM role_permissions rp
    WHERE rp.role_id = $1
    AND rp.is_active = TRUE
"""

GET_USER_ROLE = """
    SELECT 
        tr.id as role_id,
        tr.code as role_code,
        tr.name as role_name,
        tr.hierarchy_level,
        tr.is_system_role
    FROM tenant_roles tr
    JOIN tenant_members tm ON tm.role_id = tr.id
    WHERE tm.user_id = $1
    AND tm.tenant_id = $2
    AND tm.is_active = TRUE
"""

GET_ROLE_BY_ID = """
    SELECT 
        id,
        code,
        name,
        hierarchy_level,
        is_system_role,
        permissions_json
    FROM tenant_roles
    WHERE id = $1
"""

CHECK_PERMISSION_DIRECT = """
    SELECT EXISTS (
        SELECT 1 
        FROM role_permissions rp
        WHERE rp.role_id = $1
        AND rp.module = $2
        AND $3 = ANY(rp.actions)
        AND rp.is_active = TRUE
    ) as has_permission
"""


class PermissionService:
    """
    Service untuk permission checks.
    
    Responsibilities:
    - Load user permissions from database
    - Check if user has permission for action on resource
    - Handle wildcard permissions
    - Cache permissions for performance
    """
    
    def __init__(self, pool):
        self.pool = pool
        self._permission_cache: Dict[str, Dict[str, List[str]]] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
    
    async def get_user_permissions(self, user: User) -> Dict[str, List[str]]:
        """
        Get all permissions for a user.
        
        Returns dict mapping module -> list of allowed actions.
        Example: {"sales": ["C", "R", "U"], "inventory": ["R"]}
        """
        cache_key = f"{user.tenant_id}:{user.role_id}"
        
        # Check cache
        if cache_key in self._permission_cache:
            cache_time = self._cache_timestamps.get(cache_key)
            if cache_time and (datetime.utcnow() - cache_time).seconds < settings.cache.permission_cache_ttl:
                logger.debug(f"Permission cache hit for {cache_key}")
                return self._permission_cache[cache_key]
        
        # Fetch from database
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(GET_ROLE_PERMISSIONS, user.role_id)
                
                permissions: Dict[str, List[str]] = {}
                for row in rows:
                    module = row["module"]
                    actions = row["actions"] or []
                    permissions[module] = actions
                
                # Cache result
                self._permission_cache[cache_key] = permissions
                self._cache_timestamps[cache_key] = datetime.utcnow()
                
                logger.debug(f"Loaded permissions for {cache_key}: {permissions}")
                return permissions
                
        except Exception as e:
            logger.error(f"Error fetching permissions: {e}")
            return {}
    
    async def has_permission(
        self, 
        user: User, 
        action: str, 
        resource: Resource
    ) -> tuple[bool, DecisionReason]:
        """
        Check if user has permission for action on resource.
        
        Args:
            user: User performing the action
            action: Action code (C, R, U, D, V, A, P, E)
            resource: Resource being accessed
            
        Returns:
            Tuple of (allowed: bool, reason: DecisionReason)
        """
        # Validate action
        if action not in settings.policy.ACTIONS:
            logger.warning(f"Invalid action: {action}")
            return False, DecisionReason.DENIED_NO_PERMISSION
        
        # Check if user is active
        if not user.is_active:
            logger.info(f"Denied: User {user.id} is inactive")
            return False, DecisionReason.DENIED_INACTIVE_USER
        
        # Check tenant match
        if resource.tenant_id and resource.tenant_id != user.tenant_id:
            logger.warning(f"Tenant mismatch: user={user.tenant_id}, resource={resource.tenant_id}")
            return False, DecisionReason.DENIED_TENANT_MISMATCH
        
        # Get permissions
        permissions = await self.get_user_permissions(user)
        
        # Check wildcard permission (super admin)
        if "*" in permissions:
            wildcard_actions = permissions["*"]
            if "*" in wildcard_actions or action in wildcard_actions:
                logger.debug(f"Allowed via wildcard: {user.role_code} -> *:{action}")
                return True, DecisionReason.ALLOWED
        
        # Check module permission
        module = resource.module
        if module not in permissions:
            logger.info(f"Denied: No permission for module {module}")
            return False, DecisionReason.DENIED_NO_PERMISSION
        
        module_actions = permissions[module]
        
        # Check wildcard action for module
        if "*" in module_actions:
            logger.debug(f"Allowed via module wildcard: {module}:*")
            return True, DecisionReason.ALLOWED
        
        # Check specific action
        if action in module_actions:
            logger.debug(f"Allowed: {user.role_code} -> {module}:{action}")
            return True, DecisionReason.ALLOWED
        
        logger.info(f"Denied: {user.role_code} has no {action} permission on {module}")
        return False, DecisionReason.DENIED_NO_PERMISSION
    
    async def get_allowed_actions(self, user: User, module: str) -> List[str]:
        """Get list of allowed actions for a user on a module"""
        permissions = await self.get_user_permissions(user)
        
        # Check wildcard
        if "*" in permissions:
            wildcard_actions = permissions["*"]
            if "*" in wildcard_actions:
                return list(settings.policy.ACTIONS.keys())
            return wildcard_actions
        
        return permissions.get(module, [])
    
    async def get_allowed_modules(self, user: User) -> List[str]:
        """Get list of modules user has any access to"""
        permissions = await self.get_user_permissions(user)
        
        if "*" in permissions:
            return settings.policy.MODULES
        
        return list(permissions.keys())
    
    def clear_cache(self, user_id: Optional[str] = None, tenant_id: Optional[str] = None):
        """Clear permission cache"""
        if user_id and tenant_id:
            # Clear specific user cache - need to find matching keys
            keys_to_remove = [
                k for k in self._permission_cache.keys() 
                if k.startswith(f"{tenant_id}:")
            ]
            for key in keys_to_remove:
                self._permission_cache.pop(key, None)
                self._cache_timestamps.pop(key, None)
        else:
            # Clear all cache
            self._permission_cache.clear()
            self._cache_timestamps.clear()
        
        logger.info("Permission cache cleared")
    
    async def check_ai_boundary(self, user: User, action: str, resource: Resource) -> tuple[bool, DecisionReason]:
        """
        Check AI Safety Boundary (IRON LAW 10).
        
        AI agents have restricted permissions for write operations.
        """
        if not user.is_ai_agent:
            return True, DecisionReason.ALLOWED
        
        # AI read is always allowed if user has permission
        if action == "R":
            return True, DecisionReason.ALLOWED
        
        # Check if AI write is enabled
        if not settings.policy.ai_write_enabled:
            logger.warning(f"AI write denied: AI writes are disabled")
            return False, DecisionReason.DENIED_AI_BOUNDARY
        
        # Check amount threshold
        if resource.amount and resource.amount > settings.policy.ai_max_amount_threshold:
            logger.warning(
                f"AI write denied: Amount {resource.amount} exceeds threshold {settings.policy.ai_max_amount_threshold}"
            )
            return False, DecisionReason.DENIED_AI_BOUNDARY
        
        return True, DecisionReason.ALLOWED
