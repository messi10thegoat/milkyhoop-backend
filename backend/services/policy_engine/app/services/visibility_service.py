"""
Visibility Service

Handles FCL (Financial Confidentiality Level) visibility controls.
IRON LAW 0: Separation of Concerns - fokus hanya pada visibility logic.
"""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..models.policy_models import User, Resource, VisibilityLevel, DecisionReason
from ..config import settings

logger = logging.getLogger(__name__)


# SQL Queries
GET_ROLE_VISIBILITY = """
    SELECT 
        rv.level,
        rv.allowed_modules,
        rv.excluded_fields
    FROM role_visibility rv
    WHERE rv.role_id = $1
    AND rv.is_active = TRUE
    ORDER BY rv.level
"""

GET_VISIBILITY_BY_ROLE_CODE = """
    SELECT 
        rv.level,
        rv.allowed_modules,
        rv.excluded_fields
    FROM role_visibility rv
    JOIN tenant_roles tr ON tr.id = rv.role_id
    WHERE tr.code = $1
    AND tr.tenant_id = $2
    AND rv.is_active = TRUE
    ORDER BY rv.level
"""

GET_ROLE_MAX_VISIBILITY = """
    SELECT 
        COALESCE(MAX(rp.max_confidentiality), 'L1') as max_level
    FROM role_permissions rp
    WHERE rp.role_id = $1
    AND rp.is_active = TRUE
"""


# Default visibility levels
DEFAULT_VISIBILITY_LEVELS: Dict[str, VisibilityLevel] = {
    "L1": VisibilityLevel(
        level="L1",
        name="Public",
        description="Information visible to all staff",
        min_role_hierarchy=10,
    ),
    "L2": VisibilityLevel(
        level="L2",
        name="Internal",
        description="Information visible to staff and above",
        min_role_hierarchy=30,
    ),
    "L3": VisibilityLevel(
        level="L3",
        name="Confidential",
        description="Information visible to supervisor and above",
        min_role_hierarchy=50,
    ),
    "L4": VisibilityLevel(
        level="L4",
        name="Restricted",
        description="Information visible to manager and above",
        min_role_hierarchy=70,
    ),
    "L5": VisibilityLevel(
        level="L5",
        name="Top Secret",
        description="Information visible to owner only",
        min_role_hierarchy=100,
    ),
}


class VisibilityService:
    """
    Service untuk visibility/confidentiality checks (FCL).
    
    Responsibilities:
    - Determine what confidentiality levels user can access
    - Apply visibility filters to queries
    - Handle field-level visibility
    """
    
    def __init__(self, pool):
        self.pool = pool
        self._visibility_cache: Dict[str, List[str]] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
    
    async def get_visibility_levels(self, user: User) -> List[str]:
        """
        Get confidentiality levels user can access.
        
        Returns list of level codes: ['L1', 'L2', 'L3']
        """
        cache_key = f"{user.tenant_id}:{user.role_id}"
        
        # Check cache
        if cache_key in self._visibility_cache:
            cache_time = self._cache_timestamps.get(cache_key)
            if cache_time and (datetime.utcnow() - cache_time).seconds < settings.cache.visibility_cache_ttl:
                logger.debug(f"Visibility cache hit for {cache_key}")
                return self._visibility_cache[cache_key]
        
        # Fetch from database
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(GET_ROLE_VISIBILITY, user.role_id)
                
                if rows:
                    levels = [row["level"] for row in rows]
                else:
                    # Default to L1 if no explicit visibility configured
                    levels = ["L1"]
                
                # Cache result
                self._visibility_cache[cache_key] = levels
                self._cache_timestamps[cache_key] = datetime.utcnow()
                
                logger.debug(f"Loaded visibility for {cache_key}: {levels}")
                return levels
                
        except Exception as e:
            logger.error(f"Error fetching visibility: {e}")
            # Return minimal visibility on error
            return ["L1"]
    
    async def get_max_visibility_level(self, user: User) -> str:
        """Get the maximum visibility level for user"""
        levels = await self.get_visibility_levels(user)
        
        # Sort by level number and return highest
        sorted_levels = sorted(levels, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0, reverse=True)
        return sorted_levels[0] if sorted_levels else "L1"
    
    async def can_access_level(self, user: User, confidentiality_level: str) -> bool:
        """Check if user can access a specific confidentiality level"""
        allowed_levels = await self.get_visibility_levels(user)
        return confidentiality_level in allowed_levels
    
    async def check_visibility(
        self, 
        user: User, 
        resource: Resource
    ) -> tuple[bool, DecisionReason]:
        """
        Check if user can see resource based on confidentiality level.
        
        Args:
            user: User requesting access
            resource: Resource with confidentiality_level
            
        Returns:
            Tuple of (allowed: bool, reason: DecisionReason)
        """
        # If no confidentiality level on resource, allow access
        if not resource.confidentiality_level:
            return True, DecisionReason.ALLOWED
        
        allowed_levels = await self.get_visibility_levels(user)
        
        if resource.confidentiality_level in allowed_levels:
            logger.debug(f"Visibility allowed: {user.role_code} can see {resource.confidentiality_level}")
            return True, DecisionReason.ALLOWED
        
        logger.info(
            f"Visibility denied: {user.role_code} cannot see {resource.confidentiality_level}. "
            f"Allowed: {allowed_levels}"
        )
        return False, DecisionReason.DENIED_VISIBILITY
    
    def apply_visibility_filter(self, base_query: str, user: User, visibility_levels: List[str]) -> str:
        """
        Inject visibility filter into SQL query.
        
        Adds: AND confidentiality_level = ANY($visibility_levels)
        
        Note: This is a string manipulation - ensure proper SQL injection protection
        in the actual query execution.
        """
        if not visibility_levels:
            visibility_levels = ["L1"]
        
        # Convert levels to SQL array format
        levels_str = ", ".join([f"'{level}'" for level in visibility_levels])
        
        # Add WHERE clause or AND clause
        if "WHERE" in base_query.upper():
            filter_clause = f" AND confidentiality_level IN ({levels_str})"
        else:
            filter_clause = f" WHERE confidentiality_level IN ({levels_str})"
        
        # Find position to insert (before ORDER BY, GROUP BY, LIMIT, or end)
        keywords = ["ORDER BY", "GROUP BY", "LIMIT", "OFFSET", ";"]
        insert_pos = len(base_query)
        
        for keyword in keywords:
            pos = base_query.upper().find(keyword)
            if pos != -1 and pos < insert_pos:
                insert_pos = pos
        
        filtered_query = base_query[:insert_pos] + filter_clause + base_query[insert_pos:]
        
        logger.debug(f"Applied visibility filter: {visibility_levels}")
        return filtered_query
    
    def get_visibility_clause(self, visibility_levels: List[str], column_name: str = "confidentiality_level") -> str:
        """
        Get SQL WHERE clause for visibility filtering.
        
        Returns: "column_name IN ('L1', 'L2', 'L3')"
        """
        if not visibility_levels:
            visibility_levels = ["L1"]
        
        levels_str = ", ".join([f"'{level}'" for level in visibility_levels])
        return f"{column_name} IN ({levels_str})"
    
    def get_visibility_params(self, visibility_levels: List[str]) -> List[str]:
        """
        Get visibility levels as list for parameterized queries.
        
        Use with: WHERE confidentiality_level = ANY($1::text[])
        """
        if not visibility_levels:
            return ["L1"]
        return visibility_levels
    
    async def get_excluded_fields(self, user: User, module: str) -> List[str]:
        """
        Get fields that should be excluded/masked for user.
        
        Returns list of field names that should not be shown or should be masked.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(GET_ROLE_VISIBILITY, user.role_id)
                
                excluded = set()
                for row in rows:
                    if row["excluded_fields"]:
                        # Check if module matches
                        allowed_modules = row["allowed_modules"] or []
                        if not allowed_modules or module in allowed_modules:
                            excluded.update(row["excluded_fields"])
                
                return list(excluded)
                
        except Exception as e:
            logger.error(f"Error fetching excluded fields: {e}")
            return []
    
    def clear_cache(self, user_id: Optional[str] = None, tenant_id: Optional[str] = None):
        """Clear visibility cache"""
        if tenant_id:
            keys_to_remove = [
                k for k in self._visibility_cache.keys() 
                if k.startswith(f"{tenant_id}:")
            ]
            for key in keys_to_remove:
                self._visibility_cache.pop(key, None)
                self._cache_timestamps.pop(key, None)
        else:
            self._visibility_cache.clear()
            self._cache_timestamps.clear()
        
        logger.info("Visibility cache cleared")
