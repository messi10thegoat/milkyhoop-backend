"""
Kernel Router

Routes validated action plans to the correct API Gateway endpoint.
The executor calls back into the API Gateway's existing REST endpoints
to perform the actual accounting operations.

IRON LAW 0: All writes go through the Kernel (API Gateway endpoints).
IRON LAW 10: AI never writes directly — executor calls established endpoints.
"""
import json
import logging
from typing import Any, Dict, Optional

import aiohttp

from ..config import settings

logger = logging.getLogger(__name__)

# Action type string → API endpoint mapping
ACTION_ROUTES: Dict[str, Dict[str, str]] = {
    "CREATE_PURCHASE_INVOICE": {
        "method": "POST",
        "path": "/api/bills/v2",
    },
    "CREATE_SALES_INVOICE": {
        "method": "POST",
        "path": "/api/invoices",
    },
    "CREATE_EXPENSE": {
        "method": "POST",
        "path": "/api/expenses",
    },
    "CREATE_CUSTOMER": {
        "method": "POST",
        "path": "/api/customers",
    },
    "CREATE_VENDOR": {
        "method": "POST",
        "path": "/api/vendors",
    },
    "CREATE_PRODUCT": {
        "method": "POST",
        "path": "/api/items",
    },
    "RECEIVE_PAYMENT": {
        "method": "POST",
        "path": "/api/payments/received",
    },
    "MAKE_PAYMENT": {
        "method": "POST",
        "path": "/api/payments/made",
    },
    "POST_GENERAL_JOURNAL": {
        "method": "POST",
        "path": "/api/journals",
    },
    "REVERSE_JOURNAL": {
        "method": "POST",
        "path": "/api/journals/{journal_id}/reverse",
    },
}


class KernelRouter:
    """Routes action execution to the API Gateway's REST endpoints."""

    def __init__(self, base_url: str = None):
        self.base_url = (base_url or settings.API_GATEWAY_URL).rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


    def _map_payload(self, action_type: str, draft_payload: dict) -> dict:
        """Map action plan field names to API endpoint field names."""
        payload = dict(draft_payload)  # shallow copy

        if action_type == "CREATE_VENDOR":
            # vendor_name → name
            if "vendor_name" in payload:
                payload["name"] = payload.pop("vendor_name")

        elif action_type == "CREATE_CUSTOMER":
            # customer_name → name
            if "customer_name" in payload:
                payload["name"] = payload.pop("customer_name")

        elif action_type == "CREATE_PRODUCT":
            # product_name → name, unit → base_unit, buy_price → purchase_price, sell_price → sales_price
            if "product_name" in payload:
                payload["name"] = payload.pop("product_name")
            if "unit" in payload:
                payload["base_unit"] = payload.pop("unit")
            elif "base_unit" not in payload:
                payload["base_unit"] = "Pcs"  # default
            if "buy_price" in payload:
                payload["purchase_price"] = payload.pop("buy_price")
            if "sell_price" in payload:
                payload["sales_price"] = payload.pop("sell_price")
            if "category" in payload:
                payload["kategori"] = payload.pop("category")

        # Remove None values and empty strings
        return {k: v for k, v in payload.items() if v is not None and v != ""}

    async def execute(
        self,
        action_type: str,
        tenant_id: str,
        user_id: str,
        payload: dict,
    ) -> Dict[str, Any]:
        """
        Execute an action by calling the appropriate API Gateway endpoint.

        Returns: {
            "success": bool,
            "entity_id": str,
            "entity_number": str,
            "entity_type": str,
            "journal_entry_id": str,
            "message": str,
            "raw_response": dict
        }
        """
        route = ACTION_ROUTES.get(action_type)
        if not route:
            return {
                "success": False,
                "entity_id": "",
                "entity_number": "",
                "entity_type": "",
                "journal_entry_id": "",
                "message": f"No route defined for action_type: {action_type}",
                "raw_response": {},
            }

        method = route["method"]
        path = route["path"]

        # Handle path params (e.g., journal_id for REVERSE_JOURNAL)
        if "{journal_id}" in path:
            journal_id = payload.get("journal_id", "")
            path = path.replace("{journal_id}", str(journal_id))

        # Map payload field names for the target API endpoint
        payload = self._map_payload(action_type, payload)

        url = f"{self.base_url}{path}"

        # Add tenant context to headers
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-ID": str(tenant_id),
            "X-User-ID": str(user_id),
            "X-Source": "action_executor",
        }

        logger.info(f"KernelRouter: {method} {url} for {action_type}")
        logger.debug(f"KernelRouter payload: {json.dumps(payload, default=str)[:500]}")

        try:
            session = await self._get_session()
            async with session.request(method, url, json=payload, headers=headers) as resp:
                status = resp.status
                body = await resp.json() if resp.content_type == "application/json" else {}

                if 200 <= status < 300:
                    return self._extract_result(action_type, body)
                else:
                    error_msg = body.get("detail", body.get("message", f"HTTP {status}"))
                    logger.error(f"KernelRouter error: {status} - {error_msg}")
                    return {
                        "success": False,
                        "entity_id": "",
                        "entity_number": "",
                        "entity_type": "",
                        "journal_entry_id": "",
                        "message": f"API error ({status}): {error_msg}",
                        "raw_response": body,
                    }

        except aiohttp.ClientError as e:
            logger.error(f"KernelRouter connection error: {e}")
            return {
                "success": False,
                "entity_id": "",
                "entity_number": "",
                "entity_type": "",
                "journal_entry_id": "",
                "message": f"Connection error: {str(e)}",
                "raw_response": {},
            }
        except Exception as e:
            logger.error(f"KernelRouter unexpected error: {e}", exc_info=True)
            return {
                "success": False,
                "entity_id": "",
                "entity_number": "",
                "entity_type": "",
                "journal_entry_id": "",
                "message": f"Unexpected error: {str(e)}",
                "raw_response": {},
            }

    def _extract_result(self, action_type: str, body: dict) -> Dict[str, Any]:
        """Extract standardized result from API response."""
        # MilkyHoop API responses use envelope: {"success": ..., "data": {...}}
        # Unwrap the data envelope if present
        data = body.get("data") if isinstance(body.get("data"), dict) else body

        # Common patterns from MilkyHoop API responses
        entity_id = str(
            data.get("id")
            or data.get("bill_id")
            or data.get("invoice_id")
            or data.get("payment_id")
            or data.get("journal_id")
            or data.get("customer_id")
            or data.get("vendor_id")
            or data.get("product_id")
            or data.get("item_id")
            or ""
        )

        entity_number = str(
            data.get("number")
            or data.get("bill_number")
            or data.get("invoice_number")
            or data.get("payment_number")
            or data.get("journal_number")
            or ""
        )

        # Map action type to entity type
        entity_type_map = {
            "CREATE_PURCHASE_INVOICE": "bill",
            "CREATE_SALES_INVOICE": "invoice",
            "CREATE_EXPENSE": "expense",
            "CREATE_CUSTOMER": "customer",
            "CREATE_VENDOR": "vendor",
            "CREATE_PRODUCT": "item",
            "RECEIVE_PAYMENT": "payment_received",
            "MAKE_PAYMENT": "payment_made",
            "POST_GENERAL_JOURNAL": "journal",
            "REVERSE_JOURNAL": "journal",
        }

        journal_entry_id = str(data.get("journal_entry_id") or data.get("journal_id") or "")

        message = body.get("message") or f"{action_type} completed successfully"

        return {
            "success": True,
            "entity_id": entity_id,
            "entity_number": entity_number,
            "entity_type": entity_type_map.get(action_type, "unknown"),
            "journal_entry_id": journal_entry_id,
            "message": message,
            "raw_response": body,
        }
