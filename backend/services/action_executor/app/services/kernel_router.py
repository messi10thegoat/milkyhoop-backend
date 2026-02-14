from datetime import datetime, date, timedelta
"""
Kernel Router

Routes validated action plans to the correct API Gateway endpoint.
The executor calls back into the API Gateway's existing REST endpoints
to perform the actual accounting operations.

IRON LAW 0: All writes go through the Kernel (API Gateway endpoints).
IRON LAW 10: AI never writes directly — executor calls established endpoints.
"""
import json
import re
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
        "path": "/api/sales-invoices",
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
        "path": "/api/receive-payments",
    },
    "MAKE_PAYMENT": {
        "method": "POST",
        "path": "/api/bill-payments",
    },
    "POST_GENERAL_JOURNAL": {
        "method": "POST",
        "path": "/api/journals",
    },
    "REVERSE_JOURNAL": {
        "method": "POST",
        "path": "/api/journals/{journal_id}/reverse",
    },
    "CREATE_CREDIT_NOTE": {
        "method": "POST",
        "path": "/api/credit-notes",
    },
    "BANK_TRANSFER": {
        "method": "POST",
        "path": "/api/bank-transfers",
    },
    "CLOSE_PERIOD": {
        "method": "POST",
        "path": "/api/periods/{period_id}/close",
    },
    "REOPEN_PERIOD": {
        "method": "POST",
        "path": "/api/periods/{period_id}/reopen",
    },
        "CREATE_PURCHASE_ORDER": {
        "method": "POST",
        "path": "/api/purchase-orders",
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

    @staticmethod
    def _is_account_code(value: str) -> bool:
        """Check if a value looks like an account code (e.g. 5-20100) rather than a UUID."""
        if not isinstance(value, str):
            return False
        # Account codes match pattern: digit-digits (e.g. "5-20100", "6-10100")
        # UUIDs match pattern: 8-4-4-4-12 hex chars
        if re.match(r"^\d+-\d+$", value):
            return True
        return False

    async def _resolve_account_code(
        self, account_code: str, tenant_id: str, user_id: str
    ) -> str:
        """Resolve an account code (e.g. 5-20100) to its UUID via the API Gateway.
        
        Strategy:
        1. Exact match by code search
        2. If not found, infer account_type from code prefix and pick a sensible default
        3. If all fails, raise ValueError so the saga can abort gracefully
        """
        url = f"{self.base_url}/api/accounts/dropdown"
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-ID": str(tenant_id),
            "X-User-ID": str(user_id),
            "X-Source": "action_executor",
        }

        # --- Step 1: exact search ---
        params = {"search": account_code}
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    items = body.get("data") or body.get("items") or []
                    if isinstance(body, list):
                        items = body
                    # Find exact match by code
                    for item in items:
                        if item.get("code") == account_code:
                            resolved_id = str(item.get("id", ""))
                            logger.info(
                                f"Resolved account code {account_code} -> {resolved_id}"
                            )
                            return resolved_id
                    # If no exact match but we have results, use first
                    if items:
                        resolved_id = str(items[0].get("id", ""))
                        logger.warning(
                            f"No exact match for {account_code}, using first result: {resolved_id}"
                        )
                        return resolved_id
                else:
                    logger.warning(
                        f"Account dropdown search failed for {account_code}: HTTP {resp.status}"
                    )
        except Exception as e:
            logger.warning(f"Error resolving account code {account_code}: {e}")

        # --- Step 2: fallback - infer account type from code prefix and pick default ---
        # Code prefixes: 1=ASSET, 2=LIABILITY, 3=EQUITY, 4=REVENUE, 5/6=EXPENSE
        prefix = account_code.split("-")[0] if "-" in account_code else ""
        type_map = {"1": "ASSET", "2": "LIABILITY", "3": "EQUITY", "4": "REVENUE", "5": "EXPENSE", "6": "EXPENSE"}
        inferred_type = type_map.get(prefix)

        if inferred_type:
            logger.info(
                f"Account code {account_code} not found, trying fallback with type={inferred_type}"
            )
            fallback_params = {"type": inferred_type}
            try:
                async with session.get(url, headers=headers, params=fallback_params) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        items = body.get("data") or body.get("items") or []
                        if isinstance(body, list):
                            items = body
                        if items:
                            # Prefer "Lain-lain" (miscellaneous) account as safest default
                            for item in items:
                                name_lower = (item.get("name") or "").lower()
                                if "lain" in name_lower:
                                    resolved_id = str(item.get("id", ""))
                                    logger.warning(
                                        f"Fallback: resolved {account_code} -> {resolved_id} "
                                        f"({item.get('code')} - {item.get('name')})"
                                    )
                                    return resolved_id
                            # Otherwise use first non-header account
                            resolved_id = str(items[0].get("id", ""))
                            logger.warning(
                                f"Fallback: resolved {account_code} -> {resolved_id} "
                                f"({items[0].get('code')} - {items[0].get('name')})"
                            )
                            return resolved_id
            except Exception as e:
                logger.warning(f"Error in fallback resolution for {account_code}: {e}")

        # --- Step 3: complete failure ---
        error_msg = (
            f"Cannot resolve account code '{account_code}' to a UUID. "
            f"This code does not exist in the tenant's chart of accounts."
        )
        logger.error(error_msg)
        raise ValueError(error_msg)




    async def _resolve_references(
        self, action_type: str, tenant_id: str, user_id: str, payload: dict
    ) -> dict:
        """Resolve human-readable codes (like account codes) to UUIDs."""
        payload = dict(payload)  # shallow copy

        if action_type == "CREATE_EXPENSE":
            # Resolve account_id if it looks like an account code
            account_id = payload.get("account_id") or payload.get("expense_account_id")
            if account_id and self._is_account_code(str(account_id)):
                resolved = await self._resolve_account_code(
                    str(account_id), tenant_id, user_id
                )
                if "account_id" in payload:
                    payload["account_id"] = resolved
                if "expense_account_id" in payload:
                    payload["expense_account_id"] = resolved

        if action_type == "POST_GENERAL_JOURNAL":
            # Resolve account codes in journal lines
            lines = payload.get("lines") or payload.get("entries") or []
            for line in lines:
                # Try multiple field names for account reference
                account_ref = line.get("account_id") or line.get("account_name_or_code") or line.get("account_code")
                if account_ref:
                    # Resolve if it looks like a code OR if account_id is missing
                    if not line.get("account_id") or self._is_account_code(str(account_ref)):
                        resolved = await self._resolve_account_code(
                            str(account_ref), tenant_id, user_id
                        )
                        line["account_id"] = resolved

        return payload

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

        elif action_type == "CREATE_SALES_INVOICE":
            # Field mapping: issue_date -> invoice_date
            if "issue_date" in payload and "invoice_date" not in payload:
                payload["invoice_date"] = payload.pop("issue_date")
            
            # Default dates if missing (business rule: today + NET 30)
            # Note: These are sensible defaults for the document layer, not accounting mutations
            # The actual journal entry will record these explicit dates (IronLaw 6: Source Traceability)
            if not payload.get("invoice_date"):
                payload["invoice_date"] = date.today().isoformat()
            if not payload.get("due_date"):
                invoice_dt = date.fromisoformat(payload["invoice_date"])
                payload["due_date"] = (invoice_dt + timedelta(days=30)).isoformat()
            
            # Frontend uses "name" but API uses "description" for items
            items = payload.get("items") or payload.get("line_items") or []
            mapped_items = []
            for item in items:
                mapped_item = dict(item)
                # Map "name" to "description" if needed
                if "name" in mapped_item and "description" not in mapped_item:
                    mapped_item["description"] = mapped_item.pop("name")
                # Map "qty" to "quantity" if needed
                if "qty" in mapped_item and "quantity" not in mapped_item:
                    mapped_item["quantity"] = mapped_item.pop("qty")
                # Map "price" to "unit_price" if needed
                if "price" in mapped_item and "unit_price" not in mapped_item:
                    mapped_item["unit_price"] = mapped_item.pop("price")
                mapped_items.append(mapped_item)
            payload["items"] = mapped_items


        elif action_type == "RECEIVE_PAYMENT":
            # Map "amount" to "total_amount" if needed
            if "amount" in payload and "total_amount" not in payload:
                payload["total_amount"] = payload.pop("amount")
            # Ensure allocations exist
            if "allocations" not in payload and "invoice_id" in payload:
                # Auto-create single allocation
                alloc_amount = payload.get("total_amount") or payload.get("amount", 0)
                payload["allocations"] = [{"invoice_id": payload["invoice_id"], "amount_applied": alloc_amount}]

        elif action_type == "CREATE_CREDIT_NOTE":
            # Map items similar to sales invoice
            items = payload.get("items") or payload.get("line_items") or []
            mapped_items = []
            for item in items:
                mapped_item = dict(item)
                if "name" in mapped_item and "description" not in mapped_item:
                    mapped_item["description"] = mapped_item.pop("name")
                if "qty" in mapped_item and "quantity" not in mapped_item:
                    mapped_item["quantity"] = mapped_item.pop("qty")
                if "price" in mapped_item and "unit_price" not in mapped_item:
                    mapped_item["unit_price"] = mapped_item.pop("price")
                mapped_items.append(mapped_item)
            payload["items"] = mapped_items
            # Map "reason_text" to "reason" if needed
            if "reason_text" in payload and "reason" not in payload:
                payload["reason"] = payload.pop("reason_text")
            # Default reason if not provided
            if "reason" not in payload:
                payload["reason"] = "return"

        elif action_type == "MAKE_PAYMENT":
            # Map to bill-payments API schema
            mapped = {
                "vendor_id": payload.get("vendor_id"),
                "bank_account_id": payload.get("bank_account_id"),
                "total_amount": payload.get("amount") or payload.get("total_amount"),
                "payment_date": payload.get("date") or payload.get("payment_date"),
                "payment_method": payload.get("payment_method", "bank_transfer"),
                "reference_number": payload.get("reference", ""),
                "notes": payload.get("notes", ""),
                "save_as_draft": False,
            }
            # Add allocations if bill_id provided
            bill_id = payload.get("bill_id")
            if bill_id:
                amount = mapped["total_amount"] or 0
                mapped["allocations"] = [{"bill_id": bill_id, "amount_applied": amount}]
            return {k: v for k, v in mapped.items() if v is not None}

        elif action_type == "CREATE_EXPENSE":
            # Map to expenses API schema
            mapped = {
                "expense_date": payload.get("date") or payload.get("expense_date"),
                "paid_through_id": payload.get("paid_through_id") or payload.get("bank_account_id"),
                "account_id": payload.get("account_id") or payload.get("expense_account_id"),
                "amount": payload.get("amount"),
                "vendor_id": payload.get("vendor_id"),
                "vendor_name": payload.get("vendor_name"),
                "tax_rate": payload.get("tax_rate", 0),
                "reference": payload.get("reference", ""),
                "notes": payload.get("notes") or payload.get("description", ""),
            }
            return {k: v for k, v in mapped.items() if v is not None}

        elif action_type == "BANK_TRANSFER":
            # Map to bank-transfers API schema
            mapped = {
                "from_bank_id": payload.get("from_bank_id") or payload.get("source_account_id"),
                "to_bank_id": payload.get("to_bank_id") or payload.get("destination_account_id"),
                "amount": payload.get("amount"),
                "transfer_date": payload.get("date") or payload.get("transfer_date"),
                "ref_no": payload.get("reference", ""),
                "notes": payload.get("notes") or payload.get("description", ""),
                "auto_post": True,
            }
            return {k: v for k, v in mapped.items() if v is not None}


        elif action_type == "POST_GENERAL_JOURNAL":
            # Map journal entry fields
            lines = payload.get("lines") or payload.get("entries") or []
            mapped_lines = []
            for line in lines:
                mapped_lines.append({
                    "account_id": line.get("account_id"),
                    "debit": float(line.get("debit", 0)),
                    "credit": float(line.get("credit", 0)),
                    "description": line.get("description", ""),
                })
            mapped = {
                "entry_date": payload.get("entry_date") or payload.get("date") or datetime.now().strftime("%Y-%m-%d"),
                "description": payload.get("description"),
                "reference": payload.get("reference", ""),
                "lines": mapped_lines,
                "save_as_draft": False,
            }
            return {k: v for k, v in mapped.items() if v is not None}

        elif action_type == "REVERSE_JOURNAL":
            # Map reversal fields
            mapped = {
                "reversal_date": payload.get("reversal_date") or payload.get("date"),
                "reason": payload.get("reason") or "Reversal via chat",
            }
            return {k: v for k, v in mapped.items() if v is not None}

        elif action_type == "CLOSE_PERIOD":
            # Map period close fields
            mapped = {
                "closing_notes": payload.get("closing_notes") or payload.get("notes", ""),
                "force": payload.get("force", False),
            }
            return {k: v for k, v in mapped.items() if v is not None}

        elif action_type == "REOPEN_PERIOD":
            # Map period reopen fields
            mapped = {
                "reason": payload.get("reason") or "Reopen via chat",
            }
            return {k: v for k, v in mapped.items() if v is not None}

        elif action_type == "CREATE_PURCHASE_ORDER":
            # Map to purchase-orders API schema
            items = []
            for item in payload.get("items", []):
                items.append({
                    "description": item.get("description") or item.get("name") or item.get("product_name", ""),
                    "quantity": item.get("quantity") or item.get("qty", 1),
                    "unit": item.get("unit"),
                    "unit_price": item.get("unit_price") or item.get("price", 0),
                })
            mapped = {
                "vendor_id": payload.get("vendor_id"),
                "vendor_name": payload.get("vendor_name"),
                "po_date": payload.get("date") or payload.get("po_date"),
                "expected_date": payload.get("expected_delivery_date") or payload.get("expected_date"),
                "items": items,
                "notes": payload.get("notes", ""),
            }
            return {k: v for k, v in mapped.items() if v is not None}

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
        if "{period_id}" in path:
            period_id = payload.get("period_id", "")
            path = path.replace("{period_id}", str(period_id))


        # Resolve account codes to UUIDs before mapping
        payload = await self._resolve_references(action_type, tenant_id, user_id, payload)

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
            or data.get("credit_note_id")
            or data.get("journal_id")
            or data.get("customer_id")
            or data.get("vendor_id")
            or data.get("product_id")
            or data.get("item_id")
            or data.get("transfer_id")
            or data.get("po_id")
            or ""
        )

        entity_number = str(
            data.get("number")
            or data.get("bill_number")
            or data.get("invoice_number")
            or data.get("payment_number")
            or data.get("credit_note_number")
            or data.get("journal_number")
            or data.get("transfer_number")
            or data.get("po_number")
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
            "MAKE_PAYMENT": "bill_payment",
            "POST_GENERAL_JOURNAL": "journal",
            "REVERSE_JOURNAL": "journal",
            "CREATE_CREDIT_NOTE": "credit_note",
            "BANK_TRANSFER": "bank_transfer",
            "CREATE_PURCHASE_ORDER": "purchase_order",
            "CLOSE_PERIOD": "period",
            "REOPEN_PERIOD": "period",
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
