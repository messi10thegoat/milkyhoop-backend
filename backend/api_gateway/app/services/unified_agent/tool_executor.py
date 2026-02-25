"""
Unified Tool Executor for MilkyHoop Agent.

Handles two types of tools:
1. READ tools - httpx GET to kernel API endpoints
2. ACTION tools - gRPC to validator + executor

Pattern: extended from ragllm/tool_executor.py with action tool support.
"""

import asyncio
import json
import re
import hashlib
import time
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from decimal import Decimal
from uuid import uuid4

import httpx


def _to_amount(value) -> "Decimal":
    """Convert any amount value to Decimal for precision-safe comparison (Law 25)."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))

from .tool_registry import (
    get_endpoint_for_tool,
    is_action_tool,
    is_session_tool,
    is_valid_tool,
    get_action_type_enum,
    ACTION_TYPE_MAP,
    AUTO_EXECUTE_ACTIONS,
    is_direct_action_tool,
)
from .direct_action_registry import (
    get_direct_action, validate_payload, apply_defaults,
    build_confirmation_table,
    build_ux_metadata,
)
from .retry_controller import execute_with_retry, RetryController, ErrorCategory
from .tool_metadata import get_tool_metadata, get_action_metadata
from .correlation import TurnContext

logger = logging.getLogger("unified_agent.tool_executor")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)

# --- Constants ---

MAX_TOOL_CALLS_PER_REQUEST = 12
READ_TOOL_TIMEOUT = 5.0
ACTION_TOOL_TIMEOUT = 10.0
MAX_RESPONSE_SIZE = 8000
MAX_LIST_ITEMS = 15
MAX_STRING_LENGTH = 500
MAX_AMOUNT = 999_999_999_999
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

KERNEL_BASE_URL = "http://localhost:8000"

# Map action_type to category string (for gRPC clients)
ACTION_CATEGORY_MAP = {
    "CREATE_CUSTOMER": "MASTER_DATA",
    "CREATE_VENDOR": "MASTER_DATA",
    "CREATE_PRODUCT": "MASTER_DATA",
    "CREATE_SALES_INVOICE": "DOCUMENT",
    "CREATE_PURCHASE_INVOICE": "DOCUMENT",
    "CREATE_EXPENSE": "DOCUMENT",
    "CREATE_CREDIT_NOTE": "DOCUMENT",
    "CREATE_PURCHASE_ORDER": "DOCUMENT",
    "RECEIVE_PAYMENT": "PAYMENT",
    "MAKE_PAYMENT": "PAYMENT",
    "BANK_TRANSFER": "PAYMENT",
    "POST_GENERAL_JOURNAL": "ACCOUNTING",
    "REVERSE_JOURNAL": "ACCOUNTING",
    "CLOSE_PERIOD": "ACCOUNTING",
    "REOPEN_PERIOD": "ACCOUNTING",
}

# --- Enrichment Registry ---
# Maps action_type -> method name for enrichment.
# None = no enrichment needed (master data, simple actions).
ACTION_ENRICHMENT = {
    "CREATE_SALES_INVOICE": "_enrich_sales_invoice",
    "CREATE_PURCHASE_INVOICE": "_enrich_purchase_invoice",
    "CREATE_EXPENSE": "_enrich_expense",
    "CREATE_PURCHASE_ORDER": "_enrich_purchase_order",
    "CREATE_CREDIT_NOTE": "_enrich_credit_note",
    "RECEIVE_PAYMENT": "_enrich_receive_payment",
    "MAKE_PAYMENT": "_enrich_make_payment",
    "BANK_TRANSFER": "_enrich_transfer",
    "POST_GENERAL_JOURNAL": "_enrich_journal",
    # Master data / simple actions — no enrichment
    "CREATE_CUSTOMER": None,
    "CREATE_VENDOR": None,
    "CREATE_PRODUCT": None,
    "REVERSE_JOURNAL": None,
    "CLOSE_PERIOD": None,
    "REOPEN_PERIOD": None,
}


class TenantContext:
    """Tenant context passed to tool executor."""
    def __init__(self, tenant_id: str, user_id: str, auth_token: str, tenant_name: str = ""):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.auth_token = auth_token
        self.tenant_name = tenant_name


class ToolExecutor:
    """
    Executes tools called by the unified agent.
    Routes: read tools -> httpx, action tools -> gRPC.
    """

    def __init__(self, context: TenantContext, session_manager=None, session_id: str = None):
        self.context = context
        self.session_manager = session_manager
        self.session_id = session_id
        self.call_count = 0
        self.propose_count = 0
        self._validator_client = None
        self._executor_client = None

    @property
    def validator_client(self):
        if self._validator_client is None:
            from ..action_validator_client import get_action_validator_client
            self._validator_client = get_action_validator_client()
        return self._validator_client

    @property
    def executor_client(self):
        if self._executor_client is None:
            from ..action_executor_client import get_action_executor_client
            self._executor_client = get_action_executor_client()
        return self._executor_client

    async def execute(self, tool_name: str, params: Dict[str, Any], turn_ctx: "TurnContext" = None) -> Dict[str, Any]:
        """Execute a tool call with automatic retry handling (H4).

        Wraps _execute_once() with retry logic from RetryController.
        - Idempotent tools (reads): auto-retry up to max_retries
        - Non-idempotent tools (propose_action): retry with verify-first
        - Non-retryable errors (400, 401, 409): immediate abort
        """
        self.call_count += 1
        if self.call_count > MAX_TOOL_CALLS_PER_REQUEST:
            return _error("BUDGET_EXCEEDED", "Batas tool call tercapai.")

        if not is_valid_tool(tool_name):
            return _error("UNKNOWN_TOOL", f"Tool {tool_name!r} tidak ditemukan.")

        tool_meta = get_tool_metadata(tool_name)

        # --- Observability: create tool call context if turn_ctx provided ---
        tool_call_ctx = None
        try:
            if turn_ctx:
                tool_call_ctx = turn_ctx.new_tool_call(tool_name, retry_attempt=0)
        except Exception:
            pass

        # Determine action_type for propose_action (needed by retry controller)
        action_type = None
        if tool_name == "propose_action":
            action_type = params.get("action_type")

        # Wrap execution with retry logic
        result = await execute_with_retry(
            tool_name=tool_name,
            execute_fn=lambda **kw: self._execute_once(tool_name, kw),
            args=params,
            action_type=action_type,
        )

        # --- Observability: complete tool call context ---
        try:
            if tool_call_ctx:
                tc_status = "success" if result.get("success") else "failed"
                tc_error = result.get("error_type") if not result.get("success") else None
                tool_call_ctx.complete(tc_status, error_type=tc_error)
                logger.info(f"[TOOL_CALL] tool={tool_name} call_id={tool_call_ctx.tool_call_id} status={tc_status} latency={tool_call_ctx.latency_ms}ms")
        except Exception:
            pass

        return result

    async def _execute_once(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single tool call attempt. Routes to appropriate handler."""
        try:
            if is_session_tool(tool_name):
                return await self._execute_session_tool(tool_name, params)
            elif tool_name == "propose_direct_action":
                return await self._execute_propose_direct(params)
            elif tool_name == "propose_action":
                return await self._execute_propose(params)
            elif tool_name == "simulate_action":
                return await self._execute_simulate(params)
            else:
                return await self._execute_read(tool_name, params)
        except httpx.TimeoutException:
            return {"success": False, "error": f"Tool {tool_name!r} timeout.", "error_type": "timeout", "status_code": None}
        except httpx.ConnectError:
            return {"success": False, "error": f"Tool {tool_name!r} connection refused.", "error_type": "connection_refused", "status_code": None}
        except Exception as e:
            logger.exception(f"Tool execution error: {tool_name}")
            return _error("INTERNAL_ERROR", f"Error: {str(e)[:200]}")

    # --- Direct Action Execution ---

    async def _execute_propose_direct(self, params: dict) -> dict:
        """Execute a direct action proposal - validate, store pending, return preview."""
        import uuid
        from datetime import datetime, timedelta, timezone

        action_key = params.get("action_key", "")
        payload = params.get("payload", {})

        config = get_direct_action(action_key)
        if not config:
            return _error("UNKNOWN_ACTION", f"Action '{action_key}' tidak ditemukan di registry.")

        # Validate required fields
        is_valid, missing = validate_payload(action_key, payload)
        if not is_valid:
            return {
                "success": False,
                "error": f"Field berikut belum diisi: {', '.join(missing)}. Mohon lengkapi dulu.",
                "error_type": "VALIDATION_ERROR",
            }

        # Apply defaults
        payload = apply_defaults(action_key, payload)

        # Store pending action
        pending_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.ttl_seconds)

        try:
            from ..unified_agent.db_utils import get_session_db_pool
            pool = await get_session_db_pool()
            await pool.execute("""
                INSERT INTO pending_actions (
                    id, tenant_id, user_id, conversation_id,
                    action_id, action_type, action_category,
                    action_plan, status, is_direct, expires_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                uuid.UUID(pending_id),
                self.context.tenant_id,
                self.context.user_id,
                self.session_id or "",
                action_key,
                action_key.upper(),
                "MASTER_DATA",
                json.dumps(payload),
                "PENDING",
                True,
                expires_at,
            )
        except Exception as e:
            logger.error(f"[DirectAction] DB insert failed: {e}")
            return _error("DB_ERROR", f"Gagal menyimpan action: {str(e)[:200]}")

        # Build confirmation table
        confirmation_table = build_confirmation_table(action_key, payload)

        return {
            "success": True,
            "message_type": "DIRECT_ACTION_PREVIEW",
            "content": confirmation_table,
            "data": {
                "pending_action_id": pending_id,
                "action_key": action_key,
                "display_name": config.display_name,
                "payload": payload,
                "expires_at": expires_at.isoformat(),
                "risk_level": config.risk_level,
                "confirmation_table": confirmation_table,
                **build_ux_metadata(action_key, payload),
            },
        }

    # --- Session Tool Execution ---

    async def _execute_session_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a session tool (queries session-level data, not kernel API)."""
        if not self.session_manager or not self.session_id:
            return _error("NO_SESSION", "Session belum diinisialisasi.")

        if tool_name == "get_session_events":
            limit = min(params.get("limit", 10), 20)
            events = await self.session_manager.get_recent_events(self.session_id, limit=limit)
            return {"success": True, "data": events}

        elif tool_name == "search_chat_history":
            query = params.get("query", "")
            if not query:
                return _error("MISSING_QUERY", "Parameter 'query' wajib diisi.")
            days_back = min(params.get("days_back", 7), 30)
            results = await self.session_manager.search_chat_history(query, days_back=days_back)
            return {"success": True, "data": results}

        elif tool_name == "review_next_unmatched":
            return await self._execute_review_next_unmatched(params)

        elif tool_name == "import_bank_statement":
            return await self._execute_import_bank_statement(params)

        elif tool_name == "create_reconciliation_session":
            return await self._execute_create_recon_session(params)

        elif tool_name == "agentic_reconcile":
            return await self._execute_agentic_reconcile(params)

        elif tool_name == "start_workflow":
            return await self._execute_start_workflow(params)

        return _error("UNKNOWN_SESSION_TOOL", f"Session tool {tool_name!r} tidak dikenali.")

    # --- Start Workflow (Deterministic State Machine) ---

    async def _execute_start_workflow(self, params: dict) -> dict:
        """Execute start_workflow: advance the deterministic workflow engine."""
        from .workflow_engine import WorkflowEngine
        from .db_utils import get_session_db_pool

        pool = await get_session_db_pool()

        # Callback for complex tool operations (file import, etc.)
        async def execute_tool(tool_name, tool_params):
            return await self._execute_session_tool(tool_name, tool_params)

        engine = WorkflowEngine(
            db_pool=pool,
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            auth_token=self.context.auth_token,
            execute_tool=execute_tool,
        )

        workflow_type = params.get("workflow_type", "bank_reconciliation")
        user_data = params.get("user_data", {})

        # Use conversation session_id as chat_session_id
        chat_session_id = self.session_id or "unknown"

        result = await engine.process(
            chat_session_id=chat_session_id,
            workflow_type=workflow_type,
            user_data=user_data,
        )

        response = {
            "success": True,
            "advanced": result.advanced,
            "current_state": result.new_state,
            "completed": result.completed,
        }
        if result.llm_instruction:
            response["llm_instruction"] = result.llm_instruction
        if result.auto_results:
            response["auto_results"] = result.auto_results
        if result.direct_action:
            response["direct_action"] = result.direct_action
        return response

    # --- Review Next Unmatched (READ-ONLY — Law 0) ---


    async def _match_against_outstanding_bills(
        self, statement_line: dict, headers: dict
    ) -> list[dict]:
        """
        Cross-reference a DEBIT statement line against outstanding bills.
        Returns list of bill suggestions sorted by confidence (HIGH first).
        READ-ONLY — Law 0 compliant.
        Uses Decimal for precision-safe comparison (Law 25).
        """
        base_url = "http://localhost:8000"
        amount = abs(_to_amount(statement_line.get("amount", 0)))
        description = (statement_line.get("description") or "").upper()
        reference = (statement_line.get("reference") or "").upper()

        if amount <= 0:
            return []

        try:
            # Fetch outstanding bills (unpaid + partial) concurrently
            async def _fetch_bills(status: str):
                async with httpx.AsyncClient(timeout=10.0) as client:
                    return await client.get(
                        f"{base_url}/api/bills",
                        params={"status": status, "limit": 50},
                        headers=headers,
                    )

            resp_unpaid, resp_partial = await asyncio.gather(
                _fetch_bills("unpaid"),
                _fetch_bills("partial"),
            )

            unpaid_bills = []
            for resp in [resp_unpaid, resp_partial]:
                if resp.status_code == 200:
                    bill_data = resp.json()
                    unpaid_bills.extend(bill_data.get("data", bill_data.get("bills", [])))

            if not unpaid_bills:
                return []

            suggestions = []
            for bill in unpaid_bills:
                bill_number = (bill.get("invoice_number") or "").upper()
                vendor_name = (bill.get("vendor_name") or "").upper()
                bill_amount = _to_amount(bill.get("amount", 0))
                amount_due = _to_amount(bill.get("amount_due", 0))  # J-compliant from API
                amount_paid = _to_amount(bill.get("amount_paid", 0))  # display only

                confidence = None
                reason = ""

                # Match 1: Reference contains bill number → HIGH confidence
                if bill_number and (bill_number in reference or bill_number in description):
                    if amount == amount_due:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {bill_number} ditemukan di referensi + jumlah persis cocok"
                    elif amount == bill_amount:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {bill_number} ditemukan di referensi + jumlah total cocok"
                    else:
                        confidence = "MEDIUM"
                        reason = f"Nomor faktur {bill_number} ditemukan di referensi (jumlah berbeda)"

                # Match 2: Amount exact + vendor name in description → MEDIUM confidence
                elif vendor_name and len(vendor_name) > 2 and vendor_name in description:
                    if amount == amount_due:
                        confidence = "MEDIUM"
                        reason = f"Vendor {bill.get('vendor_name')} cocok + jumlah persis Rp {int(amount_due):,}".replace(",", ".")
                    elif amount == bill_amount:
                        confidence = "MEDIUM"
                        reason = f"Vendor {bill.get('vendor_name')} cocok + jumlah total Rp {int(bill_amount):,}".replace(",", ".")

                # Match 3: Amount exact match only → LOW confidence
                elif amount == amount_due and amount_due > 0:
                    confidence = "LOW"
                    reason = f"Jumlah Rp {int(amount_due):,} cocok dengan sisa tagihan {bill_number}".replace(",", ".")

                if confidence:
                    suggestions.append({
                        "bill_id": bill.get("id"),
                        "bill_number": bill.get("invoice_number"),
                        "vendor_id": bill.get("vendor_id"),
                        "vendor_name": bill.get("vendor_name"),
                        "bill_amount": int(bill_amount),
                        "amount_due": int(amount_due),
                        "amount_paid": int(amount_paid),
                        "due_date": bill.get("due_date"),
                        "confidence": confidence,
                        "reason": reason,
                    })

            # Sort: HIGH > MEDIUM > LOW
            priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            suggestions.sort(key=lambda s: priority.get(s["confidence"], 3))

            return suggestions

        except Exception as e:
            logger.warning(f"[BillMatch] Error matching bills: {e}")
            return []

    async def _match_against_outstanding_invoices(
        self, statement_line: dict, headers: dict
    ) -> list[dict]:
        """
        Cross-reference a CREDIT statement line against outstanding sales invoices.
        Returns list of invoice suggestions sorted by confidence (HIGH first).
        READ-ONLY — Law 0 compliant.
        """
        base_url = "http://localhost:8000"
        amount = abs(_to_amount(statement_line.get("amount", 0)))
        description = (statement_line.get("description") or "").upper()
        reference = (statement_line.get("reference") or "").upper()

        if amount <= 0:
            return []

        try:
            async def _fetch(params):
                async with httpx.AsyncClient(timeout=10.0) as client:
                    return await client.get(
                        f"{base_url}/api/sales-invoices",
                        params=params, headers=headers,
                    )

            resp_unpaid, resp_partial = await asyncio.gather(
                _fetch({"status": "unpaid", "limit": 50}),
                _fetch({"status": "partial", "limit": 50}),
            )

            unpaid_invoices = []
            for resp in [resp_unpaid, resp_partial]:
                if resp.status_code == 200:
                    inv_data = resp.json()
                    unpaid_invoices.extend(
                        inv_data.get("data", inv_data.get("invoices", []))
                    )

            if not unpaid_invoices:
                return []

            suggestions = []
            for inv in unpaid_invoices:
                inv_number = (inv.get("invoice_number") or "").upper()
                customer_name = (inv.get("customer_name") or "").upper()
                inv_amount = _to_amount(inv.get("amount", inv.get("total_amount", 0)))
                amount_due = _to_amount(inv.get("amount_due", 0))  # J-compliant

                confidence = None
                reason = ""

                # Match 1: Reference contains invoice number -> HIGH
                if inv_number and (inv_number in reference or inv_number in description):
                    if amount == amount_due:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {inv_number} ditemukan di referensi + jumlah persis cocok"
                    elif amount == inv_amount:
                        confidence = "HIGH"
                        reason = f"Nomor faktur {inv_number} ditemukan di referensi + jumlah total cocok"
                    else:
                        confidence = "MEDIUM"
                        reason = f"Nomor faktur {inv_number} ditemukan di referensi (jumlah berbeda)"

                # Match 2: Customer name in description + amount match -> MEDIUM
                elif customer_name and len(customer_name) > 2 and customer_name in description:
                    if amount == amount_due:
                        confidence = "MEDIUM"
                        reason = f"Pelanggan {inv.get('customer_name')} cocok + jumlah persis Rp {int(amount_due):,}".replace(",", ".")
                    elif amount == inv_amount:
                        confidence = "MEDIUM"
                        reason = f"Pelanggan {inv.get('customer_name')} cocok + jumlah total Rp {int(inv_amount):,}".replace(",", ".")

                # Match 3: Amount exact match only -> LOW
                elif amount == amount_due and amount_due > 0:
                    confidence = "LOW"
                    reason = f"Jumlah Rp {int(amount_due):,} cocok dengan sisa piutang {inv_number}".replace(",", ".")

                if confidence:
                    suggestions.append({
                        "invoice_id": inv.get("id"),
                        "invoice_number": inv.get("invoice_number"),
                        "customer_id": inv.get("customer_id"),
                        "customer_name": inv.get("customer_name"),
                        "invoice_amount": int(inv_amount),
                        "amount_due": int(amount_due),
                        "due_date": inv.get("due_date"),
                        "confidence": confidence,
                        "reason": reason,
                    })

            # Sort: HIGH > MEDIUM > LOW
            priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            suggestions.sort(key=lambda s: priority.get(s["confidence"], 3))
            return suggestions

        except Exception as e:
            logger.warning(f"[InvoiceMatch] Error matching invoices: {e}")
            return []


    async def _execute_review_next_unmatched(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch next unmatched statement line + best suggestion.
        READ-ONLY — does NOT modify any data (Law 0 compliant).
        """
        session_id = params.get("session_id")
        skip = params.get("skip", 0)

        if not session_id:
            return _error("MISSING_SESSION_ID", "Parameter 'session_id' wajib diisi.")

        try:
            base_url = "http://localhost:8000"
            headers = self._build_headers()

            # GET unmatched statement lines (read-only)
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{base_url}/api/bank-reconciliation/sessions/{session_id}/statements",
                    params={"match_status": "unmatched", "offset": skip, "limit": 1},
                    headers=headers,
                )

            if resp.status_code != 200:
                return _error("FETCH_FAILED", f"Gagal mengambil data: HTTP {resp.status_code}")

            data = resp.json()
            lines = data.get("data", data.get("lines", []))

            if not lines:
                # No more unmatched items
                return {
                    "success": True,
                    "data": {
                        "has_more": False,
                        "message": "Semua item sudah di-review. Tidak ada lagi yang belum cocok.",
                        "session_id": session_id,
                    },
                }

            line = lines[0]

            # Count total unmatched
            total_unmatched = data.get("total", data.get("count", len(lines)))

            # Try to get best suggestion from auto-match (read-only GET)
            suggestion = None
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    suggest_resp = await client.get(
                        f"{base_url}/api/bank-reconciliation/sessions/{session_id}/suggestions",
                        params={"statement_line_id": line["id"], "limit": 1},
                        headers=headers,
                    )
                if suggest_resp.status_code == 200:
                    suggest_data = suggest_resp.json()
                    suggestions = suggest_data.get("data", suggest_data.get("suggestions", []))
                    if suggestions:
                        suggestion = suggestions[0]
            except Exception:
                pass  # Suggestions are optional, don't fail

            # For DEBIT lines without a strong bank_tx suggestion: cross-reference outstanding bills
            bill_suggestion = None
            line_type = (line.get("type") or "").lower()
            if line_type == "debit":
                try:
                    bill_matches = await self._match_against_outstanding_bills(line, headers)
                    if bill_matches:
                        best = bill_matches[0]
                        # Prefer bill match over weak bank_tx suggestion
                        if not suggestion or best["confidence"] in ("HIGH", "MEDIUM"):
                            bill_suggestion = best
                except Exception as e:
                    logger.warning(f"[ReviewNext] Bill matching failed (non-fatal): {e}")

            # Invoice matching for CREDIT lines
            invoice_suggestion = None
            invoice_matches = []
            if line_type == "credit":
                try:
                    invoice_matches = await self._match_against_outstanding_invoices(line, headers)
                    if invoice_matches:
                        best_inv = invoice_matches[0]
                        # Prefer invoice match over weak bank_tx suggestion
                        if not suggestion or best_inv["confidence"] in ("HIGH", "MEDIUM"):
                            invoice_suggestion = best_inv
                except Exception as e:
                    logger.warning(f"[ReviewNext] Invoice matching failed (non-fatal): {e}")

            return {
                "success": True,
                "data": {
                    "has_more": True,
                    "remaining": total_unmatched,
                    "position": skip + 1,
                    "statement_line": {
                        "id": line.get("id"),
                        "date": line.get("date"),
                        "description": line.get("description"),
                        "reference": line.get("reference"),
                        "amount": line.get("amount"),
                        "type": line.get("type"),  # debit or credit
                    },
                    "suggestion": {
                        "transaction_id": suggestion.get("transaction_id") or suggestion.get("id"),
                        "description": suggestion.get("description"),
                        "amount": suggestion.get("amount"),
                        "date": suggestion.get("transaction_date") or suggestion.get("date"),
                        "confidence": suggestion.get("confidence") or suggestion.get("score"),
                        "match_reason": suggestion.get("match_reason") or suggestion.get("reason"),
                    } if suggestion else None,
                    "bill_suggestion": {
                        "type": "bill_payment",
                        "bill_id": bill_suggestion["bill_id"],
                        "bill_number": bill_suggestion["bill_number"],
                        "vendor_id": bill_suggestion["vendor_id"],
                        "vendor_name": bill_suggestion["vendor_name"],
                        "bill_amount": bill_suggestion["bill_amount"],
                        "amount_due": bill_suggestion["amount_due"],
                        "due_date": bill_suggestion["due_date"],
                        "confidence": bill_suggestion["confidence"],
                        "match_reason": bill_suggestion["reason"],
                    } if bill_suggestion else None,
                    "invoice_suggestion": {
                        "type": "receive_payment",
                        "invoice_id": invoice_suggestion["invoice_id"],
                        "invoice_number": invoice_suggestion["invoice_number"],
                        "customer_id": invoice_suggestion["customer_id"],
                        "customer_name": invoice_suggestion["customer_name"],
                        "invoice_amount": invoice_suggestion["invoice_amount"],
                        "amount_due": invoice_suggestion["amount_due"],
                        "due_date": invoice_suggestion["due_date"],
                        "confidence": invoice_suggestion["confidence"],
                        "match_reason": invoice_suggestion["reason"],
                        "all_matches": invoice_matches[:5] if invoice_matches else [],
                    } if invoice_suggestion else None,
                    "session_id": session_id,
                },
            }

        except httpx.TimeoutException:
            return _error("TIMEOUT", "Request timeout saat mengambil data rekonsiliasi.")
        except Exception as e:
            logger.exception(f"[ReviewNextUnmatched] Error: {e}")
            return _error("REVIEW_ERROR", f"Error: {str(e)[:200]}")


    # --- Agentic Reconcile (READ-ONLY — auto-match analysis) ---

    async def _execute_agentic_reconcile(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run automated matching analysis for a reconciliation session.
        READ-ONLY analysis — does NOT require user confirmation.
        Calls POST /api/bank-reconciliation/sessions/{session_id}/agentic-reconcile
        """
        session_id = params.get("session_id")

        if not session_id:
            return _error("MISSING_SESSION_ID", "Parameter 'session_id' wajib diisi.")

        try:
            base_url = "http://localhost:8000"
            headers = self._build_headers()

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/api/bank-reconciliation/sessions/{session_id}/agentic-reconcile",
                    headers=headers,
                    json={"max_actions": 50, "include_categorize": True, "include_exclude": True},
                )

            if resp.status_code != 200:
                return _error("AGENTIC_RECONCILE_FAILED", f"Gagal menjalankan automatch: HTTP {resp.status_code} - {resp.text[:200]}")

            data = resp.json()
            action_plan = data.get("action_plan", {})
            session_stats = data.get("session_stats", {})

            # Summarize results for the agent
            actions = action_plan.get("actions", [])
            match_count = sum(1 for a in actions if a.get("action_type") == "match")
            categorize_count = sum(1 for a in actions if a.get("action_type") == "categorize")
            exclude_count = sum(1 for a in actions if a.get("action_type") == "exclude")

            return {
                "success": True,
                "data": {
                    "session_id": session_id,
                    "summary": action_plan.get("summary", ""),
                    "total_actions": action_plan.get("total_actions", 0),
                    "matched_count": match_count,
                    "categorize_count": categorize_count,
                    "exclude_count": exclude_count,
                    "estimated_resolution": action_plan.get("estimated_resolution", 0),
                    "actions": actions,
                    "session_stats": session_stats,
                },
            }

        except httpx.TimeoutException:
            return _error("TIMEOUT", "Request timeout saat menjalankan automatch rekonsiliasi.")
        except Exception as e:
            logger.exception(f"[AgenticReconcile] Error: {e}")
            return _error("AGENTIC_RECONCILE_ERROR", f"Error: {str(e)[:200]}")

    # --- Bank Statement Import Execution ---

    async def _execute_create_recon_session(self, params: dict) -> dict:
        # Code-level enforcement: statement_ending_balance is REQUIRED
        if params.get("statement_ending_balance") is None:
            return {
                "success": False,
                "error": "statement_ending_balance wajib diisi. Tanyakan saldo akhir rekening koran ke user sebelum membuat session rekonsiliasi."
            }
        """Create or reuse a reconciliation session for a bank account."""
        from datetime import date as date_type
        import httpx

        account_id = params.get("account_id", "")
        if not account_id:
            return {"success": False, "error": "Parameter account_id wajib diisi."}

        base_url = "http://localhost:8000"
        headers = self._build_headers()

        # --- Step 1: Check for existing active session ---
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{base_url}/api/bank-reconciliation/sessions",
                    params={"account_id": account_id, "status": "in_progress"},
                    headers=headers,
                )
            if resp.status_code == 200:
                raw = resp.json()
                sessions = raw.get("data", raw) if isinstance(raw, dict) else raw
                if isinstance(sessions, list) and len(sessions) > 0:
                    existing = sessions[0]
                    session_id = existing.get("id", existing.get("session_id", ""))
                    return {
                        "success": True,
                        "data": {
                            "session_id": session_id,
                            "status": existing.get("status", "in_progress"),
                            "mode": existing.get("mode", "import"),
                            "message": f"Menggunakan session rekonsiliasi yang sudah ada (ID: {session_id}).",
                            "existing": True,
                        }
                    }
        except Exception as e:
            logger.warning(f"Check existing session failed (non-critical): {e}")

        # --- Step 2: Create new session ---
        today = date_type.today().isoformat()
        first_of_month = date_type.today().replace(day=1).isoformat()

        body = {
            "account_id": account_id,
            "statement_date": today,
            "statement_start_date": params.get("statement_start_date", first_of_month),
            "statement_end_date": params.get("statement_end_date", today),
            "statement_beginning_balance": params.get("statement_beginning_balance", 0),
            "statement_ending_balance": params.get("statement_ending_balance"),
            "mode": params.get("mode", "import"),
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{base_url}/api/bank-reconciliation/sessions",
                    json=body,
                    headers=headers,
                )

            if resp.status_code >= 400:
                error_text = resp.text[:300]
                if "sudah ada" in error_text.lower() or "already" in error_text.lower() or "in_progress" in error_text.lower():
                    return {"success": False, "error": "Sudah ada session rekonsiliasi aktif untuk akun ini. Gunakan session yang ada."}
                return {"success": False, "error": f"Gagal buat session rekonsiliasi: {error_text}"}

            data = resp.json()
            session_id = data.get("id", data.get("session_id", ""))
            return {
                "success": True,
                "data": {
                    "session_id": session_id,
                    "status": data.get("status", "in_progress"),
                    "mode": data.get("mode", "import"),
                    "message": f"Session rekonsiliasi berhasil dibuat (ID: {session_id}). Siap untuk import file.",
                }
            }
        except Exception as e:
            logger.error(f"Create recon session error: {e}")
            return {"success": False, "error": f"Gagal membuat session: {str(e)}"}

    async def _execute_import_bank_statement(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Import a bank statement file into a reconciliation session.
        Calls the existing /api/bank-reconciliation/sessions/{id}/import endpoint.
        When no column mapping is provided in config, auto-detects columns first.
        """
        session_id = params.get("session_id")
        file_path = params.get("file_path")
        config = params.get("config", {})


        # Resolve opaque file_ref if provided (preferred over raw file_path)
        file_ref = params.get("file_ref", "")
        if file_ref:
            from utils.file_ref import resolve_file_ref
            resolved = resolve_file_ref(file_ref, self.context.tenant_id)
            if resolved:
                file_path = resolved
                logger.info(f"Resolved file_ref '{file_ref}' -> '{file_path}'")
            else:
                return _error("INVALID_FILE_REF", f"File reference tidak valid atau file tidak ditemukan: {file_ref}")

        if not session_id:
            return _error("MISSING_SESSION_ID", "Parameter 'session_id' wajib diisi.")
        if not file_path:
            return _error("MISSING_FILE_PATH", "Parameter 'file_path' wajib diisi.")

        import os
        if not os.path.exists(file_path):
            return _error("FILE_NOT_FOUND", f"File tidak ditemukan: {file_path}")

        # Auto-detect format from extension
        ext = os.path.splitext(file_path)[1].lower()
        if not config.get("format"):
            format_map = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx", ".ofx": "ofx"}
            config["format"] = format_map.get(ext, "csv")

        try:
            # Read file content
            with open(file_path, "rb") as f:
                file_content = f.read()

            import httpx
            base_url = "http://localhost:8000"
            headers = self._build_headers()

            # ── Auto-detect columns when no mapping provided ──────────────
            has_column_mapping = any(
                config.get(k) for k in (
                    "date_column", "description_column", "amount_column",
                    "debit_column", "credit_column",
                )
            )
            # Only auto-detect for CSV/XLSX (not OFX which has fixed structure)
            if not has_column_mapping and config.get("format") in ("csv", "xlsx"):
                logger.info(f"[ImportBankStatement] No column mapping in config, auto-detecting...")
                try:
                    # Direct call to auto_detect_columns — bypasses WAF
                    import pandas as pd
                    import io as _io
                    filename_lower = os.path.basename(file_path).lower()
                    if filename_lower.endswith((".xlsx", ".xls")):
                        df = pd.read_excel(_io.BytesIO(file_content), nrows=20)
                    else:
                        df = pd.read_csv(_io.BytesIO(file_content), nrows=20)

                    columns = [str(c) for c in df.columns.tolist()]
                    sample_rows = []
                    for _, row in df.iterrows():
                        sample_rows.append([str(v) if pd.notna(v) else "" for v in row.tolist()])

                    from ..column_mapper import auto_detect_columns
                    from ..unified_agent.db_utils import get_session_db_pool
                    pool = await get_session_db_pool()
                    detect_result = await auto_detect_columns(
                        tenant_id=self.context.tenant_id,
                        columns=columns,
                        sample_rows=sample_rows,
                        pool=pool,
                    )
                    import_config = detect_result.to_import_config()
                    confidence = detect_result.overall_confidence
                    source = detect_result.source
                    logger.info(
                        f"[ImportBankStatement] Auto-detected columns "
                        f"(confidence={confidence}, source={source}): {import_config}"
                    )
                    # Merge detected config into user config (user values take precedence)
                    for key, value in import_config.items():
                        if not config.get(key):
                            config[key] = value
                except Exception as detect_err:
                    logger.warning(f"[ImportBankStatement] Column detection error: {detect_err}")
                    # Continue with import anyway — the import endpoint has its own defaults

            # ── Call import endpoint ──────────────────────────────────────
            # Remove Content-Type for multipart — let httpx set boundary automatically
            import_headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{base_url}/api/bank-reconciliation/sessions/{session_id}/import",
                    headers=import_headers,
                    files={"file": (os.path.basename(file_path), file_content, "application/octet-stream")},
                    data={"config": json.dumps(config)},
                )

            if response.status_code in (200, 201):
                result = response.json()
                data = result.get("data", result)
                return {
                    "success": True,
                    "data": {
                        "lines_imported": data.get("lines_imported", 0),
                        "lines_skipped": data.get("lines_skipped", 0),
                        "total_debits": data.get("total_debits", 0),
                        "total_credits": data.get("total_credits", 0),
                        "date_range": data.get("date_range"),
                        "errors": data.get("errors", []),
                        "session_id": session_id,
                    },
                    "message": (
                        f"Berhasil import {data.get('lines_imported', 0)} baris statement bank. "
                        f"Total debit: Rp {data.get('total_debits', 0):,.0f}, "
                        f"Total kredit: Rp {data.get('total_credits', 0):,.0f}."
                    ),
                }
            else:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("detail", error_json.get("message", response.text))
                except Exception:
                    pass
                return _error("IMPORT_FAILED", f"Import gagal: {error_detail}")

        except Exception as e:
            logger.exception(f"[ImportBankStatement] Error: {e}")
            return _error("IMPORT_ERROR", f"Error saat import: {str(e)[:200]}")

    async def _execute_read(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a read tool via httpx GET to kernel API."""
        endpoint = get_endpoint_for_tool(tool_name)
        if not endpoint:
            return _error("NO_ENDPOINT", f"No endpoint for {tool_name!r}.")

        if endpoint["method"] != "GET":
            logger.error(f"BLOCKED: Non-GET tool: {tool_name}")
            return _error("METHOD_BLOCKED", "Hanya GET yang diizinkan.")

        path = endpoint["path"]
        query_params = {}
        for key, value in params.items():
            if value is None:
                continue
            placeholder = "{" + key + "}"
            if placeholder in path:
                # Only validate UUID for ID-type params, not dates/periods
                if key.endswith("_id") or key == "id":
                    if not _is_valid_uuid(str(value)):
                        return _error("INVALID_UUID", f"Parameter {key!r} bukan UUID valid.")
                path = path.replace(placeholder, str(value))
            else:
                if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
                    value = value[:MAX_STRING_LENGTH]
                query_params[key] = value

        url = f"{KERNEL_BASE_URL}{path}"
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
            resp = await client.get(url, params=query_params, headers=headers)
            if resp.status_code >= 400:
                return {"success": False, "error": f"API returned {resp.status_code}: {resp.text[:200]}", "error_type": "API_ERROR", "status_code": resp.status_code}
            data = resp.json()

        result = data
        if isinstance(data, dict) and "data" in data:
            result = data["data"]

        result = _truncate_result(result)
        return {"success": True, "data": result}

    # =========================================================
    # ENRICHMENT LAYER
    #
    # Architecture (Law 0 compliant):
    #   LLM  = resolve intent (WHO, WHAT, HOW MUCH)
    #   HERE = translate to kernel schema (names, defaults, descriptions)
    #   Kernel = validate + execute
    #
    # 3 Enrichment Laws:
    #   1. Never override LLM intent (backfill only)
    #   2. Data completion only (no tax calc, no validation)
    #   3. Registry-based dispatch (no scattered if/else)
    # =========================================================

    def _build_headers(self) -> Dict[str, str]:
        """Build auth headers for kernel API calls."""
        return {
            "Authorization": f"Bearer {self.context.auth_token}",
            "X-Tenant-ID": self.context.tenant_id,
            "Content-Type": "application/json",
        }

    async def _fetch_entity(
        self, client: httpx.AsyncClient, path: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch a single entity from kernel API.
        Returns the entity dict or None on failure.
        """
        try:
            resp = await client.get(
                f"{KERNEL_BASE_URL}{path}",
                headers=self._build_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                # Handle wrapped response: {"data": {...}} or flat {...}
                if isinstance(data, dict):
                    return data.get("data", data)
        except Exception as e:
            logger.warning(f"Entity lookup failed for {path}: {e}")
        return None

    async def _enrich_payload(self, action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatch to action-specific enrichment.
        Registry-based: add new action types by adding a method + registry entry.
        Includes structured logging for observability (zero side effects).
        """
        import time as _time
        _t0 = _time.monotonic()

        # Snapshot before enrichment
        _before_keys = set(payload.keys())
        _before_vals = {k: repr(v)[:80] for k, v in payload.items()}
        _before_item_keys = []
        _before_item_vals = []
        for it in payload.get("items", []):
            if isinstance(it, dict):
                _before_item_keys.append(set(it.keys()))
                _before_item_vals.append({k: repr(v)[:80] for k, v in it.items()})

        enricher_name = ACTION_ENRICHMENT.get(action_type)

        if enricher_name is None:
            elapsed_ms = int((_time.monotonic() - _t0) * 1000)
            logger.info(
                "[ENRICH] %s | no enrichment needed (not in registry) | %dms",
                action_type, elapsed_ms,
            )
            return payload

        if not hasattr(self, enricher_name):
            logger.warning(
                "[ENRICH] WARNING: %s maps to %s but method not found",
                action_type, enricher_name,
            )
            return payload

        enricher = getattr(self, enricher_name)
        result = await enricher(payload)

        # --- Diff before vs after for structured log ---
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        _after_keys = set(result.keys())

        backfilled = []
        skipped = []
        renamed = []

        # Top-level: new keys = backfilled
        top_new = _after_keys - _before_keys
        top_gone = _before_keys - _after_keys
        for k in sorted(top_new):
            backfilled.append(k)
        # Top-level: removed keys = renamed (find target)
        for k in sorted(top_gone):
            old_val = _before_vals.get(k)
            found = False
            for nk in sorted(top_new):
                if repr(result.get(nk))[:80] == old_val:
                    renamed.append(k + " -> " + nk)
                    if nk in backfilled:
                        backfilled.remove(nk)
                    found = True
                    break
            if not found:
                renamed.append(k + " -> (removed)")
        # Top-level: unchanged keys = skipped (LLM provided)
        for k in sorted(_before_keys & _after_keys):
            if k != "items" and _before_vals.get(k) == repr(result.get(k))[:80]:
                skipped.append(k)

        # Item-level field changes (value-based rename detection)
        _after_item_keys = []
        _after_item_vals = []
        for it in result.get("items", []):
            if isinstance(it, dict):
                _after_item_keys.append(set(it.keys()))
                _after_item_vals.append({k: repr(v)[:80] for k, v in it.items()})
        for idx in range(min(len(_before_item_keys), len(_after_item_keys))):
            bef = _before_item_keys[idx]
            aft = _after_item_keys[idx]
            bef_v = _before_item_vals[idx] if idx < len(_before_item_vals) else {}
            aft_v = _after_item_vals[idx] if idx < len(_after_item_vals) else {}
            item_new = sorted(aft - bef)
            item_gone = sorted(bef - aft)
            # Pair renames by matching values (gone key value == new key value)
            used_new = set()
            for gk in item_gone:
                old_val = bef_v.get(gk)
                found_rename = False
                if old_val is not None:
                    for nk in item_new:
                        if nk not in used_new and aft_v.get(nk) == old_val:
                            renamed.append("items[%d].%s -> %s" % (idx, gk, nk))
                            used_new.add(nk)
                            found_rename = True
                            break
                if not found_rename:
                    renamed.append("items[%d].%s -> (removed)" % (idx, gk))
            # Remaining new keys = backfilled
            for nk in item_new:
                if nk not in used_new:
                    backfilled.append("items[%d].%s" % (idx, nk))

        bf_str = ", ".join(backfilled) if backfilled else "none"
        sk_str = ", ".join(skipped) if skipped else "none"
        rn_str = ", ".join(renamed) if renamed else "none"

        logger.info(
            "[ENRICH] %s | backfilled: %s | skipped: %s | renamed: %s | %dms",
            action_type, bf_str, sk_str, rn_str, elapsed_ms,
        )

        return result

    # --- Shared enrichment helpers ---

    async def _enrich_items(
        self, payload: Dict[str, Any], client: httpx.AsyncClient
    ) -> Dict[str, Any]:
        """Shared: enrich item descriptions from item master data."""
        items = payload.get("items", [])
        if not items or not isinstance(items, list):
            return payload

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("item_id")
            if not item_id:
                continue
            # Only fetch if we need description
            if "description" not in item:
                detail = await self._fetch_entity(client, f"/api/items/{item_id}")
                if detail:
                    item["description"] = detail.get("name", "Item")
                    # Backfill unit_price ONLY if LLM didn't provide (Law 1)
                    if "unit_price" not in item:
                        item["unit_price"] = detail.get("selling_price", 0)

        return payload

    def _add_due_date(self, payload: Dict[str, Any], days: int = 30) -> Dict[str, Any]:
        """Add due_date = invoice_date + N days if not already set."""
        if "due_date" not in payload and "invoice_date" in payload:
            try:
                inv_date = datetime.strptime(payload["invoice_date"], "%Y-%m-%d")
                payload["due_date"] = (inv_date + timedelta(days=days)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                payload["due_date"] = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        return payload

    # --- Per-action enrichment methods ---

    async def _enrich_sales_invoice(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_SALES_INVOICE: customer_name, due_date, item descriptions."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("invoice_date", today)
        self._add_due_date(payload)

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Customer name lookup
            cid = payload.get("customer_id")
            if cid and "customer_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/customers/{cid}")
                if entity:
                    payload["customer_name"] = entity.get("name", "")

            # Item descriptions + backfill unit_price
            payload = await self._enrich_items(payload, client)

        return payload

    async def _enrich_purchase_invoice(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich CREATE_PURCHASE_INVOICE: vendor_name, due_date, item fields.

        IMPORTANT: bills/v2 kernel API uses different field names than sales invoices:
          - product_name (not description)
          - qty (not quantity)
          - price (not unit_price)
        This method translates the LLM's generic field names to bills/v2 schema.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("invoice_date", today)
        self._add_due_date(payload)

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Vendor name lookup
            vid = payload.get("vendor_id")
            if vid and "vendor_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/vendors/{vid}")
                if entity:
                    payload["vendor_name"] = entity.get("name", "")

            # Enrich items + translate to bills/v2 field names
            items = payload.get("items", [])
            if items and isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_id = item.get("item_id")

                    # Lookup item name if needed
                    if item_id and "product_name" not in item and "description" not in item:
                        detail = await self._fetch_entity(client, f"/api/items/{item_id}")
                        if detail:
                            item["product_name"] = detail.get("name", "Item")
                            if "unit_price" not in item and "price" not in item:
                                item["price"] = detail.get("purchase_price", detail.get("selling_price", 0))

                    # Translate generic field names → bills/v2 schema
                    # description → product_name
                    if "description" in item and "product_name" not in item:
                        item["product_name"] = item.pop("description")
                    # quantity → qty
                    if "quantity" in item and "qty" not in item:
                        item["qty"] = item.pop("quantity")
                    # unit_price → price
                    if "unit_price" in item and "price" not in item:
                        item["price"] = item.pop("unit_price")
                    # item_id → product_id (bills/v2 uses product_id)
                    if "item_id" in item and "product_id" not in item:
                        item["product_id"] = item.pop("item_id")

        return payload

    async def _enrich_purchase_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_PURCHASE_ORDER: vendor_name, due_date, item descriptions."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("order_date", today)
        if "due_date" not in payload and "order_date" in payload:
            try:
                od = datetime.strptime(payload["order_date"], "%Y-%m-%d")
                payload["due_date"] = (od + timedelta(days=30)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                payload["due_date"] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=5.0) as client:
            vid = payload.get("vendor_id")
            if vid and "vendor_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/vendors/{vid}")
                if entity:
                    payload["vendor_name"] = entity.get("name", "")

            payload = await self._enrich_items(payload, client)

        return payload

    async def _enrich_expense(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_EXPENSE: field translation, CoA→bank_account lookup, date default."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("expense_date", today)
        # Translate LLM field names → kernel field names
        if "payment_account_id" in payload and "paid_through_id" not in payload:
            payload["paid_through_id"] = payload.pop("payment_account_id")
        if "deposit_account_id" in payload and "paid_through_id" not in payload:
            payload["paid_through_id"] = payload.pop("deposit_account_id")
        if "bank_account_id" in payload and "paid_through_id" not in payload:
            payload["paid_through_id"] = payload.pop("bank_account_id")

        # paid_through_id MUST be a bank_accounts.id, not a CoA ID.
        # If LLM gave a CoA ID (from search_accounts), resolve to bank_account_id.
        pt_id = payload.get("paid_through_id")
        if pt_id:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Try to find bank account matching this ID or coa_id
                banks_resp = await self._fetch_entity(client, "/api/bank-accounts")
                banks = banks_resp if isinstance(banks_resp, list) else (banks_resp.get("items") or banks_resp.get("data") or []) if isinstance(banks_resp, dict) else []
                if banks:
                    # Direct match (already a bank account ID)
                    direct = next((b for b in banks if str(b.get("id")) == str(pt_id)), None)
                    if direct:
                        pass  # Already correct bank_account_id
                    else:
                        # Try matching coa_id (LLM gave CoA ID instead of bank account ID)
                        coa_match = next((b for b in banks if str(b.get("coa_id")) == str(pt_id)), None)
                        if coa_match:
                            payload["paid_through_id"] = str(coa_match["id"])
                            logger.info(f"Expense enrich: CoA {pt_id} -> bank_account {coa_match['id']}")
                        else:
                            # No match — use first bank account as fallback
                            if banks:
                                payload["paid_through_id"] = str(banks[0]["id"])
                                logger.warning(f"Expense enrich: No bank match for {pt_id}, using default {banks[0]['id']}")
        return payload

    async def _enrich_credit_note(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich CREATE_CREDIT_NOTE: customer_name, date, item descriptions."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("credit_note_date", today)

        async with httpx.AsyncClient(timeout=5.0) as client:
            cid = payload.get("customer_id")
            if cid and "customer_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/customers/{cid}")
                if entity:
                    payload["customer_name"] = entity.get("name", "")

            payload = await self._enrich_items(payload, client)

        return payload

    async def _enrich_receive_payment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich RECEIVE_PAYMENT: customer lookup from invoice, field translations, defaults."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("payment_date", today)
        payload.setdefault("payment_method", "bank_transfer")

        # Translate LLM field names → kernel field names
        if "deposit_account_id" in payload and "bank_account_id" not in payload:
            payload["bank_account_id"] = payload.pop("deposit_account_id")
        if "payment_account_id" in payload and "bank_account_id" not in payload:
            payload["bank_account_id"] = payload.pop("payment_account_id")

        async with httpx.AsyncClient(timeout=5.0) as client:
            # If invoice_id provided but no customer_id, look up from invoice
            inv_id = payload.get("invoice_id")
            if inv_id and "customer_id" not in payload:
                inv = await self._fetch_entity(client, f"/api/sales-invoices/{inv_id}")
                if inv:
                    payload["customer_id"] = inv.get("customer_id", "")
                    if "customer_name" not in payload:
                        payload["customer_name"] = inv.get("customer_name", "")

            # If customer_id provided, look up name
            cid = payload.get("customer_id")
            if cid and "customer_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/customers/{cid}")
                if entity:
                    payload["customer_name"] = entity.get("name", "")

        return payload

    async def _enrich_make_payment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich MAKE_PAYMENT: payment_date default, vendor_name."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("payment_date", today)

        async with httpx.AsyncClient(timeout=5.0) as client:
            vid = payload.get("vendor_id")
            if vid and "vendor_name" not in payload:
                entity = await self._fetch_entity(client, f"/api/vendors/{vid}")
                if entity:
                    payload["vendor_name"] = entity.get("name", "")

        return payload

    async def _enrich_transfer(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich BANK_TRANSFER: transfer_date default."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("transfer_date", today)
        # from_bank_id, to_bank_id, amount must come from LLM
        return payload

    async def _enrich_journal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich POST_GENERAL_JOURNAL: posting_date default."""
        today = datetime.now().strftime("%Y-%m-%d")
        payload.setdefault("posting_date", today)
        return payload

    # --- Propose Action Execution ---

    async def _execute_propose(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate -> prepare pending action. Does NOT execute."""
        action_type = params.get("action_type")
        assumptions = params.get("assumptions", [])

        # Collect all non-meta fields as payload (flat schema design)
        _meta_keys = {"action_type", "assumptions"}
        payload = {k: v for k, v in params.items() if k not in _meta_keys and v is not None}

        # === ENRICHMENT STEP ===
        # LLM provides intent (IDs, qty, price).
        # Enrichment adds kernel-required fields (names, defaults, descriptions).
        payload = await self._enrich_payload(action_type, payload)
        logger.info(f"propose_action: type={action_type}, payload_keys={list(payload.keys())}")

        if action_type not in ACTION_TYPE_MAP:
            return _error("INVALID_ACTION_TYPE", f"Action type {action_type!r} tidak valid.")

        amount_error = _validate_amounts(payload)
        if amount_error:
            return _error("INVALID_AMOUNT", amount_error)

        category = ACTION_CATEGORY_MAP.get(action_type, "DOCUMENT")
        idempotency_key = _generate_idempotency_key(
            self.context.tenant_id, action_type, payload
        )

        # Step 1: Validate via gRPC (individual params)
        validation = await self.validator_client.validate_action(
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            action_id=action_type,
            action_type=action_type,
            category=category,
            draft_payload=payload,
            idempotency_key=idempotency_key,
            confidence=0.9,
        )

        if not validation.get("valid"):
            errors = validation.get("errors", [])
            return {
                "success": False,
                "data": {
                    "status": "VALIDATION_FAILED",
                    "errors": [
                        {"layer": e.get("layer", ""), "code": e.get("code", ""), "message": e.get("message", "")}
                        for e in errors
                    ],
                },
            }

        # Step 2: Prepare pending action via gRPC
        prepare_result = await self.executor_client.prepare_action(
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            action_type=action_type,
            category=category,
            draft_payload=payload,
            idempotency_key=idempotency_key,
            confidence=0.9,
            assumptions=assumptions,
        )

        if not prepare_result.get("success", False) and not prepare_result.get("pending_action_id"):
            return _error("PREPARE_FAILED", "Gagal membuat pending action.")

        dry_run = validation.get("dry_run", {})
        journal_lines = dry_run.get("journal_entries", [])

        return {
            "success": True,
            "data": {
                "status": "ACTION_PREVIEW",
                "pending_action_id": prepare_result.get("pending_action_id"),
                "confirmation_token": prepare_result.get("confirmation_token"),
                "preview": {
                    "action_type": action_type,
                    "payload": payload,
                    "assumptions": assumptions,
                    "journal_lines": [
                        {
                            "account": l.get("account_name", l.get("account_code", "")),
                            "debit": l.get("debit", 0),
                            "credit": l.get("credit", 0),
                            "description": l.get("description", ""),
                        }
                        for l in journal_lines
                    ],
                    "total_debit": dry_run.get("total_debit", 0),
                    "total_credit": dry_run.get("total_credit", 0),
                    "balanced": dry_run.get("balanced", False),
                    "impact_summary": dry_run.get("impact_summary", ""),
                    "confirmation_message": validation.get("confirmation_message", ""),
                    "risk_level": validation.get("risk_level", 0),
                },
                "expires_at": prepare_result.get("expires_at"),
            },
        }

    # --- Simulate Action Execution ---

    async def _execute_simulate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Validate only, no pending action. For what-if analysis."""
        action_type = params.get("action_type")

        # Flat payload extraction (same as propose)
        _meta_keys = {"action_type", "assumptions"}
        payload = {k: v for k, v in params.items() if k not in _meta_keys and v is not None}

        if action_type not in ACTION_TYPE_MAP:
            return _error("INVALID_ACTION_TYPE", f"Action type {action_type!r} tidak valid.")

        # === SAME ENRICHMENT ===
        payload = await self._enrich_payload(action_type, payload)

        # dry_run_action expects individual params
        dry_run_result = await self.validator_client.dry_run_action(
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            action_type=action_type,
            draft_payload=payload,
        )

        if dry_run_result is None:
            return {
                "success": False,
                "data": {
                    "status": "SIMULATION_FAILED",
                    "errors": [{"layer": "DRY_RUN", "code": "FAILED", "message": "Simulasi gagal"}],
                },
            }

        result = dry_run_result
        return {
            "success": True,
            "data": {
                "status": "SIMULATION_OK",
                "journal_lines": [
                    {"account": l.get("account_name", ""), "debit": l.get("debit", 0), "credit": l.get("credit", 0)}
                    for l in result.get("journal_entries", [])
                ],
                "total_debit": result.get("total_debit", 0),
                "total_credit": result.get("total_credit", 0),
                "balanced": result.get("balanced", False),
            },
        }


# --- Helpers ---

def _error(code: str, message: str) -> Dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}

def _is_valid_uuid(value: str) -> bool:
    return bool(UUID_PATTERN.match(value))

def _validate_amounts(payload: Dict[str, Any]) -> Optional[str]:
    amount_fields = ["amount", "unit_price", "total"]
    for key, value in payload.items():
        if key in amount_fields:
            if not isinstance(value, (int, float)) or value < 0:
                return f"Field {key!r} harus bilangan positif."
            if value > MAX_AMOUNT:
                return f"Field {key!r} melebihi batas maksimum."
        if key == "items" and isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    for f in amount_fields:
                        if f in item:
                            v = item[f]
                            if not isinstance(v, (int, float)) or v < 0:
                                return f"Item [{i}].{f} harus bilangan positif."
                            if v > MAX_AMOUNT:
                                return f"Item [{i}].{f} melebihi batas maksimum."
                    if "quantity" in item:
                        q = item["quantity"]
                        if not isinstance(q, (int, float)) or q <= 0:
                            return f"Item [{i}].quantity harus > 0."
    return None

def _generate_idempotency_key(tenant_id: str, action_type: str, payload: Dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, default=str)
    # 10-second window: prevents double-click, allows re-creation after
    # Actual execution idempotency is enforced by pending_action_id + confirm flow
    time_window = str(int(time.time()) // 10)
    raw = f"{tenant_id}:{action_type}:{normalized}:{time_window}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _truncate_result(data: Any) -> Any:
    serialized = json.dumps(data, default=str)
    if len(serialized) <= MAX_RESPONSE_SIZE:
        return data
    if isinstance(data, list) and len(data) > MAX_LIST_ITEMS:
        return data[:MAX_LIST_ITEMS] + [{"_truncated": True, "_total": len(data), "_showing": MAX_LIST_ITEMS}]
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and len(value) > MAX_LIST_ITEMS:
                data[key] = value[:MAX_LIST_ITEMS] + [{"_truncated": True, "_total": len(value), "_showing": MAX_LIST_ITEMS}]
    return data
