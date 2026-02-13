"""
Tool Executor — Executes API tools requested by the LLM.

Iron Law 0/10: READ-ONLY. Only GET requests. Never writes data.
Uses httpx to call internal API endpoints (localhost:8000 inside container).
"""

import json
import logging
from typing import Any, Dict, Optional

import httpx

from .api_tools import get_endpoint_for_tool, TOOL_ENDPOINTS

logger = logging.getLogger(__name__)

# Internal API base URL (inside Docker container, uvicorn port)
INTERNAL_API_BASE = "http://localhost:8000"

# Safety: maximum number of tool calls per conversation turn
MAX_TOOL_CALLS_PER_TURN = 5

# Timeout for each API call
API_TIMEOUT = 15.0


class ToolExecutor:
    """Executes API tool calls requested by the LLM. READ-ONLY."""

    def __init__(self, auth_token: str, tenant_id: str):
        """
        Args:
            auth_token: Bearer token for API authentication
            tenant_id: Tenant slug for X-Tenant-ID header
        """
        self.auth_token = auth_token
        self.tenant_id = tenant_id
        self.call_count = 0

    async def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a single tool call.

        Returns: {"success": bool, "data": ..., "error": str|None}
        """
        # Safety guard: prevent excessive calls
        self.call_count += 1
        if self.call_count > MAX_TOOL_CALLS_PER_TURN:
            return {
                "success": False,
                "data": None,
                "error": f"Batas maksimum {MAX_TOOL_CALLS_PER_TURN} tool calls per pertanyaan tercapai.",
            }

        # Lookup endpoint
        endpoint_info = get_endpoint_for_tool(tool_name)
        if not endpoint_info:
            return {
                "success": False,
                "data": None,
                "error": f"Tool '{tool_name}' tidak ditemukan.",
            }

        # Iron Law 0/10: ONLY GET
        if endpoint_info["method"] != "GET":
            logger.error(f"BLOCKED: Non-GET tool call attempted: {tool_name} {endpoint_info['method']}")
            return {
                "success": False,
                "data": None,
                "error": "Hanya operasi baca (GET) yang diizinkan.",
            }

        path = endpoint_info["path"]
        url = f"{INTERNAL_API_BASE}{path}"

        # Build query params from arguments (skip None/empty)
        params = {}
        for k, v in arguments.items():
            if v is not None and v != "":
                params[k] = v

        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "X-Tenant-ID": self.tenant_id,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # Unwrap envelope if present: {"data": ..., "items": ...}
            result = data
            if isinstance(data, dict):
                # Many endpoints return {"data": actual_data} or {"items": [...]}
                if "data" in data and isinstance(data["data"], (dict, list)):
                    result = data["data"]
                elif "items" in data and isinstance(data["items"], list):
                    result = data

            # Truncate large results to prevent token overflow
            result_str = json.dumps(result, default=str, ensure_ascii=False)
            if len(result_str) > 8000:
                # Truncate and add indicator
                result = _truncate_result(result)

            logger.info(f"Tool {tool_name} executed: {len(result_str)} chars")
            return {
                "success": True,
                "data": result,
                "error": None,
            }

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            detail = ""
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            logger.warning(f"Tool {tool_name} HTTP {status}: {detail}")
            return {
                "success": False,
                "data": None,
                "error": f"API error {status}: {detail}",
            }
        except httpx.TimeoutException:
            logger.warning(f"Tool {tool_name} timeout")
            return {
                "success": False,
                "data": None,
                "error": "API timeout — coba lagi nanti.",
            }
        except Exception as e:
            logger.error(f"Tool {tool_name} error: {e}", exc_info=True)
            return {
                "success": False,
                "data": None,
                "error": f"Terjadi kesalahan: {str(e)}",
            }


def _truncate_result(data: Any, max_items: int = 15) -> Any:
    """Truncate large result sets to prevent token overflow."""
    if isinstance(data, list) and len(data) > max_items:
        truncated = data[:max_items]
        truncated.append({"_truncated": True, "_total": len(data), "_shown": max_items})
        return truncated
    if isinstance(data, dict):
        # Truncate nested lists
        for key, val in data.items():
            if isinstance(val, list) and len(val) > max_items:
                data[key] = val[:max_items]
                data[key].append({"_truncated": True, "_total": len(val), "_shown": max_items})
        # Handle items list specifically
        if "items" in data and isinstance(data["items"], list) and len(data["items"]) > max_items:
            data["items"] = data["items"][:max_items]
            data["items"].append({"_truncated": True, "_total": len(data["items"]), "_shown": max_items})
    return data
