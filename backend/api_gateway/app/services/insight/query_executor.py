import logging
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)

# Base URL for internal API calls (same process, but via HTTP for consistency)
INTERNAL_API_BASE = "http://localhost:8000"


class QueryExecutor:
    """
    Execute query templates by calling existing REST API endpoints.
    READ-ONLY — only GET requests allowed.
    """

    def __init__(self, base_url: str = INTERNAL_API_BASE):
        self.base_url = base_url.rstrip("/")

    async def execute(
        self,
        template_id: str,
        template: dict,
        params: dict,
        auth_header: str,
    ) -> Dict[str, Any]:
        """
        Call API endpoint and return processed result.

        Args:
            template_id: The matched template key
            template: The template definition dict
            params: Extracted params (e.g., search query)
            auth_header: The Authorization header value (Bearer token)
        """
        endpoint = template["api_endpoint"]
        url = f"{self.base_url}{endpoint}"

        # Build query params
        query_params = {}

        # Add fixed params from template (e.g., status=unpaid for bills)
        if "api_params" in template:
            query_params.update(template["api_params"])

        # Add search param if extracted
        if "search" in params:
            query_params["search"] = params["search"]

        headers = {
            "Authorization": auth_header,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=query_params, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"API error for {template_id}: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Query execution failed for {template_id}: {e}")
            raise

        return self._process_response(data, template)

    def _process_response(self, data: dict, template: dict) -> Dict[str, Any]:
        """Process API response based on template response_type."""
        response_type = template["response_type"]

        # All list endpoints return {items: [...], total: N, has_more: bool}
        # /api/items also has {success: true} but same structure
        items = []
        total = 0

        if isinstance(data, dict):
            items = data.get("items", [])
            total = data.get("total", len(items))
        elif isinstance(data, list):
            items = data
            total = len(data)

        if response_type == "count":
            return {"type": "count", "count": total, "entity_type": template.get("entity_type", "")}

        elif response_type == "list" or response_type == "search":
            return {
                "type": "list",
                "items": items,
                "count": total,
                "entity_type": template.get("entity_type", ""),
            }

        elif response_type == "sum":
            sum_field = template.get("sum_field", "amount")
            total_sum = 0
            for item in items:
                val = item.get(sum_field)
                if val is not None:
                    try:
                        total_sum += float(val)
                    except (ValueError, TypeError):
                        pass
            return {
                "type": "sum",
                "total": total_sum,
                "count": total,
                "entity_type": template.get("entity_type", ""),
            }


        elif response_type == "filter":
            # Client-side filter on a field
            filter_field = template.get("filter_field", "")
            filter_op = template.get("filter_op", "gt")
            filter_value = template.get("filter_value", 0)

            filtered = []
            for item in items:
                val = item.get(filter_field)
                if val is None:
                    continue
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue
                if filter_op == "gt" and val > filter_value:
                    filtered.append(item)
                elif filter_op == "gte" and val >= filter_value:
                    filtered.append(item)
                elif filter_op == "lt" and val < filter_value:
                    filtered.append(item)
                elif filter_op == "eq" and val == filter_value:
                    filtered.append(item)

            return {
                "type": "list",
                "items": filtered,
                "count": len(filtered),
                "entity_type": template.get("entity_type", ""),
            }

        elif response_type == "dashboard":
            # Dashboard endpoints return raw summary data (not items array)
            return {
                "type": "dashboard",
                "data": data,
                "entity_type": template.get("entity_type", ""),
            }

        return {"type": "raw", "data": data}
