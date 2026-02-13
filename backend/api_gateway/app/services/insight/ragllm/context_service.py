"""
Context Service — Provides tenant business context to the LLM.

Fetches and caches basic tenant information so the LLM can
give contextually relevant answers (e.g., company name, currency).
"""

import logging
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Cache TTL: 5 minutes
CONTEXT_CACHE_TTL = 300
INTERNAL_API_BASE = "http://localhost:8000"


class ContextService:
    """Provides tenant context for LLM prompting."""

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_time: Dict[str, float] = {}

    async def get_context(
        self, tenant_id: str, auth_token: str
    ) -> Dict[str, Any]:
        """
        Get tenant context. Cached for 5 minutes.

        Returns: {"tenant_name": str, "currency": str, "today": str}
        """
        now = time.time()

        # Check cache
        if tenant_id in self._cache:
            age = now - self._cache_time.get(tenant_id, 0)
            if age < CONTEXT_CACHE_TTL:
                return self._cache[tenant_id]

        # Fetch fresh context
        context = await self._fetch_context(tenant_id, auth_token)
        self._cache[tenant_id] = context
        self._cache_time[tenant_id] = now
        return context

    async def _fetch_context(
        self, tenant_id: str, auth_token: str
    ) -> Dict[str, Any]:
        """Fetch tenant context from API."""
        from datetime import date

        context = {
            "tenant_id": tenant_id,
            "currency": "IDR",
            "today": date.today().isoformat(),
        }

        # Try to get tenant/company info
        try:
            headers = {
                "Authorization": f"Bearer {auth_token}",
                "X-Tenant-ID": tenant_id,
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Try dashboard summary for basic business context
                resp = await client.get(
                    f"{INTERNAL_API_BASE}/api/dashboard/summary",
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        summary = data.get("data", data)
                        context["has_dashboard"] = True
                        # Extract key financial indicators
                        if "total_revenue" in summary:
                            context["total_revenue"] = summary["total_revenue"]
                        if "total_expense" in summary:
                            context["total_expense"] = summary["total_expense"]
        except Exception as e:
            logger.debug(f"Could not fetch tenant context: {e}")

        return context

    def clear_cache(self, tenant_id: Optional[str] = None):
        """Clear context cache."""
        if tenant_id:
            self._cache.pop(tenant_id, None)
            self._cache_time.pop(tenant_id, None)
        else:
            self._cache.clear()
            self._cache_time.clear()
