"""
rule_engine/app/core/rule_cache.py

In-memory cache for tenant rules with TTL support

Author: MilkyHoop Team
Version: 1.0.0
"""

import time
import logging
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)


class RuleCache:
    """In-memory cache for parsed tenant rules with TTL"""

    def __init__(self, ttl_seconds: int = 300):
        """
        Initialize rule cache

        Args:
            ttl_seconds: Time-to-live for cached rules (default 5 minutes)
        """
        self._cache: Dict[str, tuple] = {}  # {tenant_id: (rules, timestamp)}
        self._ttl = ttl_seconds
        logger.info(f"RuleCache initialized with TTL={ttl_seconds}s")

    def get(self, tenant_id: str) -> Optional[List[dict]]:
        """
        Get cached rules for a tenant

        Args:
            tenant_id: Tenant identifier

        Returns:
            List of rule dictionaries if cached and valid, None otherwise
        """
        if tenant_id in self._cache:
            rules, timestamp = self._cache[tenant_id]
            age = time.time() - timestamp

            if age < self._ttl:
                logger.debug(f"Cache HIT for tenant={tenant_id}, age={age:.1f}s")
                return rules
            else:
                logger.debug(f"Cache EXPIRED for tenant={tenant_id}, age={age:.1f}s")
                del self._cache[tenant_id]

        logger.debug(f"Cache MISS for tenant={tenant_id}")
        return None

    def set(self, tenant_id: str, rules: List[dict]):
        """
        Cache rules for a tenant

        Args:
            tenant_id: Tenant identifier
            rules: List of rule dictionaries to cache
        """
        self._cache[tenant_id] = (rules, time.time())
        logger.debug(f"Cache SET for tenant={tenant_id}, count={len(rules)}")

    def invalidate(self, tenant_id: str):
        """
        Invalidate cached rules for a tenant

        Args:
            tenant_id: Tenant identifier
        """
        if tenant_id in self._cache:
            del self._cache[tenant_id]
            logger.info(f"Cache INVALIDATED for tenant={tenant_id}")

    def clear_all(self):
        """Clear entire cache"""
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Cache CLEARED (removed {count} entries)")

    def get_stats(self) -> dict:
        """Get cache statistics"""
        return {
            "entries": len(self._cache),
            "ttl_seconds": self._ttl
        }
