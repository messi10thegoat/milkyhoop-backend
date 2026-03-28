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
from itertools import combinations

import httpx


def _to_amount(value) -> "Decimal":
    """Convert any amount value to Decimal for precision-safe comparison (Law 25)."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        logger.warning(
            f"[MATCH] Invalid amount value: {value!r} ({type(value).__name__})"
        )
        return Decimal("0")


def _safe_get_name(entity: dict, entity_type: str) -> str:
    """
    Safely extract vendor/customer name from bill/invoice entity.
    Handles nested (entity.vendor.name) and flat (entity.vendor_name) formats.
    Returns empty string on failure — never crashes.
    """
    if not isinstance(entity, dict):
        return ""

    if entity_type == "bill":
        search_keys = [
            ("vendor", "name"),
            ("vendor_name", None),
            ("vendor", "display_name"),
        ]
    elif entity_type == "invoice":
        search_keys = [
            ("customer", "name"),
            ("customer_name", None),
            ("customer", "display_name"),
        ]
    else:
        return ""

    for outer_key, inner_key in search_keys:
        val = entity.get(outer_key)
        if val is None:
            continue
        if inner_key is None:
            if isinstance(val, str) and val.strip():
                return val.strip()
        elif isinstance(val, dict):
            name = val.get(inner_key, "")
            if isinstance(name, str) and name.strip():
                return name.strip()
        elif isinstance(val, str) and val.strip():
            return val.strip()

    entity_keys = list(entity.keys())[:10]
    logger.warning(f"[MATCH] Could not extract {entity_type} name. Keys: {entity_keys}")
    return ""


def _safe_get_id(entity: dict, entity_type: str) -> str:
    """
    Safely extract vendor/customer ID from bill/invoice entity.
    Handles nested (entity.vendor.id) and flat (entity.vendor_id) formats.
    """
    if not isinstance(entity, dict):
        return ""

    if entity_type == "bill":
        search_keys = [("vendor", "id"), ("vendor_id", None)]
    elif entity_type == "invoice":
        search_keys = [("customer", "id"), ("customer_id", None)]
    else:
        return ""

    for outer_key, inner_key in search_keys:
        val = entity.get(outer_key)
        if val is None:
            continue
        if inner_key is None:
            return str(val) if val else ""
        elif isinstance(val, dict):
            inner_val = val.get(inner_key)
            if inner_val:
                return str(inner_val)

    return ""


def find_allocation_options(matches: list[dict], transfer_amount: "Decimal") -> dict:
    """
    Find how to allocate transfer_amount across matching invoices/bills.
    Capped at 5 items to avoid exponential blowup.
    Pure function, no side effects.
    """
    # Exact match on 1 item -> simple
    for m in matches:
        if _to_amount(m.get("amount_due", 0)) == transfer_amount:
            return {"type": "single", "allocation": [m]}

    # Combo match (capped at 5 items max)
    capped = matches[:5]
    for r in range(2, len(capped) + 1):
        for combo in combinations(capped, r):
            if (
                sum(_to_amount(m.get("amount_due", 0)) for m in combo)
                == transfer_amount
            ):
                return {"type": "multi", "allocation": list(combo)}

    # No exact combo -> return options for user to pick
    return {"type": "needs_user_input", "options": capped}


from .tool_registry import (  # noqa: E402
    get_endpoint_for_tool,
    is_session_tool,
    is_valid_tool,
    ACTION_TYPE_MAP,
    is_tutorial_tool,
)
from .direct_action_registry import (  # noqa: E402
    get_direct_action,
    validate_payload,
    apply_defaults,
    build_confirmation_table,
    build_review_card_payload,
    build_ux_metadata,
    get_query_action,
    QueryActionConfig,
    ChartQueryConfig,
)
from .retry_controller import execute_with_retry  # noqa: E402
from .tool_metadata import get_tool_metadata  # noqa: E402
from .correlation import TurnContext  # noqa: E402
from .tutorial_registry import (  # noqa: E402
    get_tutorial,
    get_tutorial_step,
    list_available_tutorials,
)
from .tutorial_progress import (  # noqa: E402
    get_progress,
    upsert_progress,
    advance_tutorial as advance_tutorial_step,
    dismiss_tutorial as dismiss_tutorial_progress,
)

logger = logging.getLogger("unified_agent.tool_executor")
# ─── Phase 2C: Tool Response Cache ───────────────────────────────────────────
import time as _cache_time

TOOL_CACHE_TTL = 300  # 5 minutes
_cache_logger = __import__('logging').getLogger('unified_agent.cache')
CACHEABLE_TOOLS = frozenset({
    "get_chart_of_accounts",
    "get_bank_accounts",
    "get_accounting_periods",
    "search_customers",
    "search_vendors",
    "search_items",
})

# Per-request in-memory cache (lives for duration of one agent turn)
# This is sufficient because:
# 1. Within a single turn, the same tool may be called multiple times
# 2. Between turns, data may change so we shouldn't cache
# 3. Avoids DB complexity of session-backed cache
_turn_cache: dict = {}

def _cache_key(tool_name: str, params: dict) -> str:
    """Generate cache key from tool name + sorted params."""
    import json as _cj
    return f"{tool_name}:{_cj.dumps(params, sort_keys=True, default=str)}"

def get_from_cache(tool_name: str, params: dict) -> dict | None:
    """Check turn cache for cached result."""
    if tool_name not in CACHEABLE_TOOLS:
        return None
    key = _cache_key(tool_name, params)
    entry = _turn_cache.get(key)
    if entry and (_cache_time.time() - entry["ts"]) < TOOL_CACHE_TTL:
        _cache_logger.warning("[Phase3-Cache] HIT tool=%s", tool_name)
        return entry["result"]
    return None

def set_in_cache(tool_name: str, params: dict, result: dict):
    """Store result in turn cache."""
    if tool_name not in CACHEABLE_TOOLS:
        return
    key = _cache_key(tool_name, params)
    _turn_cache[key] = {"ts": _cache_time.time(), "result": result}
    _cache_logger.warning("[Phase3-Cache] SET tool=%s key=%s", tool_name, key[:60])

def clear_turn_cache():
    """Clear the per-turn cache. Call at start of each new turn."""
    _turn_cache.clear()


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
    "CREATE_BILL": "_enrich_purchase_invoice",  # alias — registry uses CREATE_BILL
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

    def __init__(
        self, tenant_id: str, user_id: str, auth_token: str, tenant_name: str = ""
    ):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.auth_token = auth_token
        self.tenant_name = tenant_name


class ToolExecutor:
    """
    Executes tools called by the unified agent.
    Routes: read tools -> httpx, action tools -> gRPC.
    """

    def __init__(
        self,
        context: TenantContext,
        session_manager=None,
        session_id: str = None,
        user_text: str = "",
    ):
        self.context = context
        self.session_manager = session_manager
        self.session_id = session_id
        self.user_text = user_text  # Original user message (may contain file_ref)
        self.call_count = 0
        self.propose_count = 0
        self._validator_client = None
        self._executor_client = None

    @staticmethod
    def _truncate(text: str, max_len: int = 15) -> str:
        """Truncate text for chart labels."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    @property
    def validator_client(self):
        if self._validator_client is None:
            from ..action_validator_client import get_action_validator_client  # noqa: E402

            self._validator_client = get_action_validator_client()
        return self._validator_client

    @property
    def executor_client(self):
        if self._executor_client is None:
            from ..action_executor_client import get_action_executor_client  # noqa: E402

            self._executor_client = get_action_executor_client()
        return self._executor_client

    async def execute(
        self, tool_name: str, params: Dict[str, Any], turn_ctx: "TurnContext" = None
    ) -> Dict[str, Any]:
        """Execute a tool call with automatic retry handling (H4).

        Wraps _execute_once() with retry logic from RetryController.
        - Idempotent tools (reads): auto-retry up to max_retries
        - Non-idempotent tools (propose_action): retry with verify-first
        - Non-retryable errors (400, 401, 409): immediate abort
        """
        # Phase 2C: Check tool cache first
        _cached = get_from_cache(tool_name, params)
        if _cached is not None:
            logger.info(f"[TOOL_CACHE] HIT tool={tool_name}")
            return _cached

        self.call_count += 1
        if self.call_count > MAX_TOOL_CALLS_PER_REQUEST:
            return _error("BUDGET_EXCEEDED", "Batas tool call tercapai.")

        if not is_valid_tool(tool_name):
            return _error("UNKNOWN_TOOL", f"Tool {tool_name!r} tidak ditemukan.")

        _tool_meta = get_tool_metadata(tool_name)

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
                tc_error = (
                    result.get("error_type") if not result.get("success") else None
                )
                tool_call_ctx.complete(tc_status, error_type=tc_error)
                logger.info(
                    f"[TOOL_CALL] tool={tool_name} call_id={tool_call_ctx.tool_call_id} status={tc_status} latency={tool_call_ctx.latency_ms}ms"
                )
        except Exception:
            pass

        # Phase 2C: Cache successful results
        if result.get("success"):
            set_in_cache(tool_name, params, result)

        return result

    async def _execute_once(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a single tool call attempt. Routes to appropriate handler."""
        try:
            if is_session_tool(tool_name):
                return await self._execute_session_tool(tool_name, params)
            elif tool_name == "propose_direct_action":
                return await self._execute_propose_direct(params)
            elif tool_name == "update_document_context":
                return await self._execute_update_document_context(params)
            elif tool_name == "execute_query":
                return await self._execute_query(params)
            elif tool_name == "propose_action":
                return await self._execute_propose(params)
            elif tool_name == "simulate_action":
                return await self._execute_simulate(params)
            elif tool_name == "get_customer_invoices":
                return await self._execute_get_customer_invoices(params)
            elif tool_name == "get_vendor_bills":
                return await self._execute_get_vendor_bills(params)
            elif is_tutorial_tool(tool_name):
                return await self._execute_tutorial_tool(tool_name, params)
            else:
                return await self._execute_read(tool_name, params)
        except httpx.TimeoutException:
            return {
                "success": False,
                "error": f"Tool {tool_name!r} timeout.",
                "error_type": "timeout",
                "status_code": None,
            }
        except httpx.ConnectError:
            return {
                "success": False,
                "error": f"Tool {tool_name!r} connection refused.",
                "error_type": "connection_refused",
                "status_code": None,
            }
        except Exception as e:
            logger.exception(f"Tool execution error: {tool_name}")
            return _error("INTERNAL_ERROR", f"Error: {str(e)[:200]}")

    # --- Direct Action Execution ---

    async def _resolve_entity_names(self, action_key: str, payload: dict):
        """Resolve display names (vendor_name, customer_name etc.) from IDs when LLM omits them."""
        try:
            from .db_utils import get_session_db_pool
            pool = await get_session_db_pool()
            tenant_id = self.context.tenant_id
        except Exception as e:
            logger.warning(f"[resolve_entity_names] pool init failed: {e}")
            return

        # Vendor name (vendors.id = uuid)
        if action_key == "create_bill_payment" and not payload.get("vendor_name") and payload.get("vendor_id"):
            try:
                row = await pool.fetchrow(
                    "SELECT name FROM vendors WHERE id = $1::uuid AND tenant_id = $2",
                    str(payload["vendor_id"]), tenant_id
                )
                if row:
                    payload["vendor_name"] = row["name"]
            except Exception as e:
                logger.warning(f"[resolve_entity_names] vendor lookup: {e}")
            # Also resolve bill_number
            if not payload.get("bill_number") and payload.get("bill_id"):
                try:
                    brow = await pool.fetchrow(
                        "SELECT invoice_number, vendor_name FROM bills WHERE id = $1::uuid AND tenant_id = $2",
                        str(payload["bill_id"]), tenant_id
                    )
                    if brow:
                        payload.setdefault("bill_number", brow["invoice_number"])
                        payload.setdefault("vendor_name", brow["vendor_name"])
                except Exception as e:
                    logger.warning(f"[resolve_entity_names] bill lookup: {e}")

        # Customer name (customers.id = varchar, NOT uuid)
        if action_key == "create_receive_payment" and not payload.get("customer_name") and payload.get("customer_id"):
            try:
                row = await pool.fetchrow(
                    "SELECT nama FROM customers WHERE id = $1 AND tenant_id = $2",
                    str(payload["customer_id"]), tenant_id
                )
                if row:
                    payload["customer_name"] = row["nama"]
            except Exception as e:
                logger.warning(f"[resolve_entity_names] customer lookup: {e}")

        # Bank account name (bank_accounts.id = uuid)
        if not payload.get("bank_account_name") and payload.get("bank_account_id"):
            try:
                row = await pool.fetchrow(
                    "SELECT account_name FROM bank_accounts WHERE id = $1::uuid AND tenant_id = $2",
                    str(payload["bank_account_id"]), tenant_id
                )
                if row:
                    payload["bank_account_name"] = row["account_name"]
            except Exception as e:
                logger.warning(f"[resolve_entity_names] bank lookup: {e}")

    # --- Tutorial Tool Execution ---

    async def _execute_tutorial_tool(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a tutorial tool (DB-backed, no session_manager needed)."""
        from .db_utils import get_session_db_pool  # noqa: E402

        pool = await get_session_db_pool()
        user_id = self.context.user_id
        tenant_id = self.context.tenant_id

        async with pool.acquire() as conn:
            if tool_name == "list_tutorials":
                tutorials = list_available_tutorials()
                return {"success": True, "tutorials": tutorials}

            elif tool_name == "get_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                config = get_tutorial(tutorial_key)
                if not config:
                    return _error("NOT_FOUND", f"Tutorial '{tutorial_key}' not found")
                progress = await get_progress(conn, user_id, tutorial_key)
                current = progress.current_step if progress else 0
                steps_data = []
                for step in config.steps:
                    steps_data.append(
                        {
                            "step_key": step.step_key,
                            "step_index": step.step_index,
                            "linked_action": step.linked_action,
                            "completion_trigger": step.completion_trigger,
                            "skippable": step.skippable,
                        }
                    )
                return {
                    "success": True,
                    "tutorial_key": config.tutorial_key,
                    "display_key": config.display_key,
                    "total_steps": config.total_steps,
                    "current_step": current,
                    "status": progress.status if progress else "not_started",
                    "steps": steps_data,
                }

            elif tool_name == "start_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                config = get_tutorial(tutorial_key)
                if not config:
                    return _error("NOT_FOUND", f"Tutorial '{tutorial_key}' not found")
                progress = await upsert_progress(
                    conn,
                    user_id,
                    tenant_id,
                    tutorial_key,
                    current_step=1,
                    status="active",
                )
                first_step = config.steps[0] if config.steps else None
                return {
                    "success": True,
                    "message_type": "TUTORIAL_STEP",
                    "status": "started",
                    "tutorial_key": tutorial_key,
                    "current_step": 1,
                    "total_steps": config.total_steps,
                    "step": {
                        "step_key": first_step.step_key,
                        "linked_action": first_step.linked_action,
                        "completion_trigger": first_step.completion_trigger,
                        "skippable": first_step.skippable,
                    }
                    if first_step
                    else None,
                }

            elif tool_name == "advance_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                next_step = await advance_tutorial_step(
                    conn, user_id, tenant_id, tutorial_key
                )
                if next_step is None:
                    return {
                        "success": True,
                        "status": "completed",
                        "tutorial_key": tutorial_key,
                    }
                config = get_tutorial(tutorial_key)
                step = get_tutorial_step(tutorial_key, next_step)
                return {
                    "success": True,
                    "message_type": "TUTORIAL_STEP",
                    "status": "advanced",
                    "tutorial_key": tutorial_key,
                    "current_step": next_step,
                    "total_steps": config.total_steps if config else 0,
                    "step": {
                        "step_key": step.step_key,
                        "linked_action": step.linked_action,
                        "completion_trigger": step.completion_trigger,
                    }
                    if step
                    else None,
                }

            elif tool_name == "dismiss_tutorial":
                tutorial_key = params.get("tutorial_key", "")
                await dismiss_tutorial_progress(conn, user_id, tenant_id, tutorial_key)
                return {
                    "success": True,
                    "status": "dismissed",
                    "tutorial_key": tutorial_key,
                }

            return _error(
                "UNKNOWN_TUTORIAL_TOOL", f"Tutorial tool {tool_name!r} tidak dikenali."
            )

    async def _run_pre_flight_checks(self, config, payload: dict) -> dict:
        """Run pre-flight checks before proposing action to user.

        Returns: {"blocked": False} if all pass or no checks.
                 {"blocked": True, "message": "...", "alternatives": [...]} if fail.
        """
        if not hasattr(config, "pre_flight_checks") or not config.pre_flight_checks:
            return {"blocked": False}

        for check in config.pre_flight_checks:
            try:
                endpoint = check.endpoint.format(**payload)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.context.auth_token}",
                    }
                    resp = await client.get(
                        f"http://localhost:8000{endpoint}",
                        headers=headers,
                    )
                    if resp.status_code >= 400:
                        continue  # skip failed checks
                    result = resp.json()

                if not result.get("can_proceed", True):
                    msg = check.fail_message_template
                    try:
                        msg = msg.format(**result)
                    except (KeyError, IndexError):
                        pass

                    if check.fail_action == "reject":
                        return {"blocked": True, "message": msg}
                    elif check.fail_action == "suggest_alternative":
                        return {
                            "blocked": True,
                            "message": msg,
                            "alternatives": check.alternatives,
                        }
                    elif check.fail_action == "warn":
                        return {"blocked": False, "warning": msg}
            except Exception as e:
                logger.warning(f"Pre-flight check failed for {config.action_key}: {e}")

        return {"blocked": False}

    async def _get_journal_preview(self, config, payload: dict) -> dict | None:
        """Hit preview endpoint to get journal impact without posting.
        Returns None if config has no journal_preview_endpoint.
        Non-fatal: preview failure → continue without preview.
        """
        if (
            not hasattr(config, "journal_preview_endpoint")
            or not config.journal_preview_endpoint
        ):
            return None

        try:
            endpoint = config.journal_preview_endpoint.format(**payload)
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.context.auth_token}",
                }
                resp = await client.post(
                    f"http://localhost:8000{endpoint}",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code >= 400:
                    logger.warning(
                        f"Journal preview HTTP {resp.status_code} for {config.action_key}"
                    )
                    return None
                data = resp.json()
                # Extract journal_lines list from response dict
                if isinstance(data, dict) and "journal_lines" in data:
                    return data["journal_lines"]
                return data
        except Exception as e:
            logger.warning(f"Journal preview failed for {config.action_key}: {e}")
            return None

    def _normalize_payload(self, action_key: str, payload: dict) -> dict:
        """
        Generic field normalization based on FieldSpec.aliases.
        
        GPT-4o-mini sends "logical" but wrong field names. Instead of
        manual if-blocks per action_key, aliases are declared in FieldSpec.
        
        Supported patterns:
        - Simple rename: aliases=["payment_account_id"] -> name="bank_account_id"  
        - Array extraction: aliases=["EXTRACT:allocations.bill_id"] -> name="bill_id"
        
        To add normalization for new module: just add aliases to FieldSpec in registry.
        No need to edit this file.
        """
        from .direct_action_registry import DIRECT_ACTIONS
        config = DIRECT_ACTIONS.get(action_key)
        if not config or not config.fields:
            return payload
        
        for field_spec in config.fields:
            if not hasattr(field_spec, 'aliases') or not field_spec.aliases:
                continue
            # Skip if canonical name already present
            if field_spec.name in payload:
                continue
            
            for alias in field_spec.aliases:
                if alias.startswith("EXTRACT:"):
                    # Array extraction: "EXTRACT:allocations.bill_id"
                    parts = alias[len("EXTRACT:"):].split(".", 1)
                    if len(parts) == 2:
                        array_field, nested_key = parts
                        items = payload.get(array_field, [])
                        if items and isinstance(items, list) and len(items) > 0:
                            first = items[0] if isinstance(items[0], dict) else {}
                            if nested_key in first:
                                payload[field_spec.name] = first[nested_key]
                                break
                elif alias in payload:
                    # Simple rename
                    payload[field_spec.name] = payload.pop(alias)
                    break
        
        # Indonesian label -> API value mapping (for user-facing options in registry)
        _LABEL_TO_API = {
            "item_type": {"persediaan": "goods", "jasa": "service", "non-persediaan": "non_inventory",
                          "barang": "goods", "goods": "goods", "service": "service", "non_inventory": "non_inventory"},
        }
        for field_name, mapping in _LABEL_TO_API.items():
            if field_name in payload and isinstance(payload[field_name], str):
                mapped = mapping.get(payload[field_name].lower().strip())
                if mapped:
                    payload[field_name] = mapped

        return payload

    async def _execute_propose_direct(self, params: dict) -> dict:
        """Execute a direct action proposal - validate, store pending, return preview."""
        import uuid  # noqa: E402
        from datetime import datetime, timedelta, timezone

        action_key = params.get("action_key", "")
        payload = params.get("payload", {})

        # Fallback: if LLM puts fields at top level instead of under payload,
        # extract them automatically
        if not payload:
            payload = {k: v for k, v in params.items() if k != "action_key"}

        config = get_direct_action(action_key)
        if not config:
            return _error(
                "UNKNOWN_ACTION", f"Action '{action_key}' tidak ditemukan di registry."
            )

        # === PRE-FLIGHT CHECKS ===
        pre_flight = await self._run_pre_flight_checks(config, payload)
        if pre_flight.get("blocked"):
            msg = pre_flight["message"]
            if pre_flight.get("alternatives"):
                msg += "\n\nAlternatif: " + ", ".join(pre_flight["alternatives"])
            return {"message_type": "TEXT", "text": msg}

        # === JOURNAL PREVIEW ===
        # === GENERIC NORMALIZATION (replaces all manual if-blocks) ===
        payload = self._normalize_payload(action_key, payload)

        # === POST-NORMALIZATION: domain-specific ID resolution ===
        # Auto-resolve vendor_id from bill_id when LLM sends non-UUID vendor_id
        if action_key == "create_bill_payment" and payload.get("bill_id"):
            vid = str(payload.get("vendor_id", ""))
            if not vid or len(vid) < 30:  # not a valid UUID
                try:
                    from .db_utils import get_session_db_pool
                    pool = await get_session_db_pool()
                    bill_row = await pool.fetchrow(
                        "SELECT vendor_id, vendor_name FROM bills WHERE id = $1::uuid AND tenant_id = $2",
                        str(payload["bill_id"]), self.context.tenant_id
                    )
                    if bill_row:
                        payload["vendor_id"] = str(bill_row["vendor_id"])
                        payload.setdefault("vendor_name", bill_row["vendor_name"])
                except Exception as e:
                    logger.warning(f"[create_bill_payment] vendor_id resolve from bill: {e}")

        # Auto-extract customer_id from allocations for receive_payment
        if action_key == "create_receive_payment":
            if "invoice_id" in payload and "allocations" not in payload:
                payload["allocations"] = [{"invoice_id": payload["invoice_id"], "amount_applied": payload.get("total_amount", payload.get("amount", 0))}]
            if "allocations" in payload and "customer_id" not in payload:
                allocs = payload.get("allocations", [])
                if allocs and isinstance(allocs, list) and len(allocs) > 0:
                    first = allocs[0] if isinstance(allocs[0], dict) else {}
                    if "customer_id" in first:
                        payload["customer_id"] = first["customer_id"]

        # === RESOLVE ENTITY NAMES (for success/loading messages) ===
        await self._resolve_entity_names(action_key, payload)

        # Validate required fields
        is_valid, missing = validate_payload(action_key, payload)
        if not is_valid:
            # Build helpful message with field descriptions
            from .direct_action_registry import DIRECT_ACTIONS
            field_hints = []
            da_config = DIRECT_ACTIONS.get(action_key)
            if da_config:
                field_map = {f.name: f for f in da_config.fields}
                for m in missing:
                    f = field_map.get(m)
                    if f and f.options:
                        opts = ", ".join(f.options)
                        field_hints.append(f"**{f.label}** ({opts})")
                    elif f and f.description:
                        field_hints.append(f"**{f.label}** ({f.description})")
                    elif f:
                        field_hints.append(f"**{f.label}**")
                    else:
                        field_hints.append(f"**{m}**")
            hint_str = ", ".join(field_hints) if field_hints else ", ".join(missing)
            return {
                "success": False,
                "error": f"Saya perlu info tambahan untuk melanjutkan: {hint_str}. Bisa tolong lengkapi? 😊",
                "error_type": "VALIDATION_ERROR",
                "missing_fields": missing,
            }

        # Apply defaults
        payload = apply_defaults(action_key, payload)

        # === JOURNAL PREVIEW (after normalization + validation + defaults) ===
        journal_preview = None
        if config.creates_journal and config.journal_preview_endpoint:
            journal_preview = await self._get_journal_preview(config, payload)

        # Store pending action
        pending_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.ttl_seconds)

        try:
            from ..unified_agent.db_utils import get_session_db_pool  # noqa: E402

            pool = await get_session_db_pool()
            await pool.execute(
                """
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

        # Build confirmation table + structured review card
        confirmation_table = build_confirmation_table(action_key, payload, journal_preview)
        review_card = build_review_card_payload(action_key, payload, journal_preview)

        # Build detection reason from payload fields
        _det_parts = []
        if payload.get("amount") or payload.get("total_amount"):
            _amt = payload.get("amount") or payload.get("total_amount")
            try:
                _det_parts.append("nominal Rp {:,.0f}".format(float(_amt)).replace(",", "."))
            except (ValueError, TypeError):
                pass
        for _det_key, _det_label in [("vendor_name", "vendor"), ("customer_name", "pelanggan"), ("item_name", "barang"), ("account_name", "akun"), ("name", "nama")]:
            if payload.get(_det_key):
                _det_parts.append(_det_label + " '" + str(payload[_det_key]) + "'")
                break
        _detection_reason = "Terdeteksi dari: " + ", ".join(_det_parts) if _det_parts else ""

        response_data = {
            "success": True,
            "message_type": "DIRECT_ACTION_PREVIEW",
            "content": confirmation_table,
            "data": {
                "pending_action_id": pending_id,
                "action_key": action_key,
                "detection_reason": _detection_reason,
                "display_name": f"{config.display_name}: {payload.get('name') or payload.get('entity_name') or ''}" .strip(': ') if action_key.startswith("update_") and (payload.get('name') or payload.get('entity_name')) else config.display_name,
                "payload": payload,
                "expires_at": expires_at.isoformat(),
                "risk_level": config.risk_level,
                "confirmation_table": confirmation_table,
                "review_card": review_card,
                **build_ux_metadata(action_key, payload),
            },
        }

        if journal_preview:
            response_data["journal_preview"] = journal_preview

        return response_data

    # --- Query Execution Engine ---

    async def _execute_query(self, params: dict) -> dict:
        """Execute a read-only financial query from the query registry."""
        query_key = params.get("query_key", "")
        query_params = params.get("params") or {}

        config = get_query_action(query_key)
        if not config:
            return _error("UNKNOWN_QUERY", f"Query '{query_key}' tidak terdaftar.")

        # Build request path + query params
        path = config.rest_endpoint
        req_params = {}

        for key, value in query_params.items():
            if value is None:
                continue
            placeholder = "{" + key + "}"
            if placeholder in path:
                val = str(value)
                if (key.endswith("_id") or key == "id") and val and len(val) < 10:
                    return {"error": f"Parameter {key} bukan UUID valid: {val}"}
                path = path.replace(placeholder, val)
            else:
                req_params[key] = value

        # Auto-fill date defaults for current month if not provided
        import datetime  # noqa: E402

        today = datetime.date.today()
        if any(qp.param_type == "date" for qp in config.query_params):
            if "start_date" not in req_params and "start_date" in [
                qp.name for qp in config.query_params
            ]:
                req_params["start_date"] = today.replace(day=1).isoformat()
            if "end_date" not in req_params and "end_date" in [
                qp.name for qp in config.query_params
            ]:
                req_params["end_date"] = today.isoformat()

        # Fill path params with defaults if still have placeholders
        for qp in config.query_params:
            placeholder = "{" + qp.name + "}"
            if placeholder in path:
                default = qp.default or today.strftime("%Y-%m")
                path = path.replace(placeholder, default)

        url = f"{KERNEL_BASE_URL}{path}"
        headers = self._build_headers()

        try:
            async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
                resp = await client.get(url, params=req_params, headers=headers)
                if resp.status_code >= 400:
                    return _error("API_ERROR", f"Query gagal: HTTP {resp.status_code}")
                raw_data = resp.json()
        except httpx.TimeoutException:
            return _error("TIMEOUT", f"Query '{query_key}' timeout.")
        except Exception as e:
            return _error("INTERNAL_ERROR", f"Query error: {str(e)[:200]}")

        # Unwrap common response patterns
        data = raw_data
        if isinstance(data, dict) and "data" in data:
            data = data["data"]

        # ─── CHART QUERY: return CHART message_type directly ───
        if isinstance(config, ChartQueryConfig):
            chart_spec = self._build_chart_spec(config, data, query_params)
            return {
                "message_type": "CHART",
                "content": f"Berikut grafik {config.display_name}:",
                "data": chart_spec,
            }

        # Format response based on response_format
        formatted = self._format_query_result(config, data, raw_data)

        return {
            "success": True,
            "query_key": query_key,
            "display_name": config.display_name,
            "response_format": config.response_format,
            "data": formatted,
        }

    def _format_query_result(self, config: QueryActionConfig, data, raw_data) -> dict:
        """Format query result based on response_format type."""
        fmt = config.response_format

        if fmt == "single_value":
            return self._format_single_value(data, raw_data)
        elif fmt == "summary":
            return self._format_summary(data, raw_data)
        elif fmt == "table":
            return self._format_table(data, raw_data)
        elif fmt == "list":
            return self._format_list(data, raw_data)
        else:
            return {"raw": data}

    def _format_single_value(self, data, raw_data) -> dict:
        """Format single_value: extract key metrics."""
        if isinstance(raw_data, dict):
            d = raw_data
        elif isinstance(data, dict):
            d = data
        else:
            return {"raw": data}

        result = {}
        # Pick known financial fields
        for key in [
            "total_balance",
            "cash_balance",
            "bank_balance",
            "account_count",
            "today_inflows",
            "today_outflows",
            "today_net",
        ]:
            if key in d:
                result[key] = d[key]
        if not result:
            result = d
        return result

    def _format_summary(self, data, raw_data) -> dict:
        """Format summary: return structured data as-is (LLM will narrate)."""
        if isinstance(data, dict):
            return data
        elif isinstance(raw_data, dict):
            # Strip non-data keys
            return {
                k: v
                for k, v in raw_data.items()
                if k not in ("success", "status", "message")
            }
        return {"raw": data}

    def _format_table(self, data, raw_data) -> dict:
        """Format table: truncate to max 20 rows for LLM context."""
        MAX_ROWS = 20
        if isinstance(data, list):
            truncated = len(data) > MAX_ROWS
            rows = data[:MAX_ROWS]
            result = {"rows": rows, "total_count": len(data)}
            if truncated:
                result["truncated"] = True
                result["showing"] = MAX_ROWS
            return result
        elif isinstance(data, dict):
            # Trial balance etc might have accounts list
            for key in ["accounts", "items", "rows", "entries", "data"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    truncated = len(items) > MAX_ROWS
                    result = {**{k: v for k, v in data.items() if k != key}}
                    result["rows"] = items[:MAX_ROWS]
                    result["total_count"] = len(items)
                    if truncated:
                        result["truncated"] = True
                        result["showing"] = MAX_ROWS
                    return result
            return data
        return {"raw": data}

    def _format_list(self, data, raw_data) -> dict:
        """Format list: return items with count."""
        MAX_ITEMS = 20
        if isinstance(data, list):
            truncated = len(data) > MAX_ITEMS
            result = {"items": data[:MAX_ITEMS], "total_count": len(data)}
            if truncated:
                result["truncated"] = True
            return result
        elif isinstance(data, dict) and any(
            k in data for k in ["items", "data", "periods"]
        ):
            for key in ["items", "data", "periods"]:
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    return {
                        "items": items[:MAX_ITEMS],
                        "total_count": len(items),
                        "truncated": len(items) > MAX_ITEMS,
                    }
        return {"items": [data] if data else [], "total_count": 1 if data else 0}

    # --- Transaction Lookup Tools ---

    async def _execute_get_customer_invoices(self, params: dict) -> dict:
        """Get outstanding invoices for a customer via compute_customer_ar()."""
        customer_id = params.get("customer_id", "")
        status = params.get("status", "outstanding")

        # Guard: customer_id MUST be a valid UUID
        if not customer_id or len(customer_id) < 10:
            logger.warning("get_customer_invoices called without valid customer_id: %s", customer_id)
            return {"results": [], "error": "customer_id wajib diisi. Panggil search_customers dulu untuk mendapatkan UUID pelanggan."}

        # ARAP Rule 5/6: Use /customers/{id}/open-invoices which wraps
        # compute_customer_ar() — single source of truth from journal_lines
        if status == "outstanding":
            url = f"http://localhost:8000/api/customers/{customer_id}/open-invoices"
        else:
            url = f"http://localhost:8000/api/sales-invoices"

        try:
            async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.context.auth_token}",
                }
                api_params = {} if status == "outstanding" else {"customer_id": customer_id, "limit": "20"}
                resp = await client.get(url, headers=headers, params=api_params)
                if resp.status_code >= 400:
                    return {"results": [], "error": f"HTTP {resp.status_code}"}
                data = resp.json()
        except Exception as e:
            return {"results": [], "error": str(e)}

        if status == "outstanding":
            # Response: {"invoices": [...], "summary": {...}}
            invoices = data.get("invoices", [])
            summary = data.get("summary", {})
            return {
                "results": [
                    {
                        "id": inv.get("id", ""),
                        "number": inv.get("invoice_number", ""),
                        "date": inv.get("invoice_date", ""),
                        "due_date": inv.get("due_date", ""),
                        "total": str(inv.get("total_amount", 0)),
                        "amount_paid": str(inv.get("paid_amount", 0)),
                        "amount_due": str(inv.get("remaining_amount", 0)),
                        "is_overdue": inv.get("is_overdue", False),
                        "overdue_days": inv.get("overdue_days", 0),
                    }
                    for inv in invoices
                ],
                "total_outstanding": str(summary.get("total_outstanding", 0)),
            }
        else:
            invoices = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(invoices, list):
                invoices = []
            return {
                "results": [
                    {
                        "id": inv.get("id", ""),
                        "number": inv.get("invoice_number", ""),
                        "date": inv.get("invoice_date", ""),
                        "total": str(inv.get("total_amount", inv.get("total", inv.get("amount", 0)))),
                        "amount_paid": str(inv.get("amount_paid", 0)),
                        "amount_due": str(inv.get("amount_due", inv.get("total_amount", inv.get("total", 0)))),
                        "status": inv.get("status", ""),
                    }
                    for inv in invoices
                ]
            }

    async def _execute_get_vendor_bills(self, params: dict) -> dict:
        """Get outstanding bills for a vendor via compute_vendor_ap()."""
        vendor_id = params.get("vendor_id", "")
        status = params.get("status", "outstanding")

        # Guard: vendor_id MUST be a valid UUID
        if not vendor_id or len(vendor_id) < 10:
            logger.warning("get_vendor_bills called without valid vendor_id: %s", vendor_id)
            return {"results": [], "error": "vendor_id wajib diisi. Panggil search_vendors dulu untuk mendapatkan UUID vendor."}

        # ARAP Rule 5/6: Use /vendors/{id}/open-bills which wraps
        # compute_vendor_ap() — single source of truth from journal_lines
        if status == "outstanding":
            url = f"http://localhost:8000/api/vendors/{vendor_id}/open-bills"
        else:
            url = "http://localhost:8000/api/bills"

        try:
            async with httpx.AsyncClient(timeout=READ_TOOL_TIMEOUT) as client:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.context.auth_token}",
                }
                api_params = {} if status == "outstanding" else {"vendor_id": vendor_id, "limit": "20"}
                resp = await client.get(url, headers=headers, params=api_params)
                if resp.status_code >= 400:
                    logger.warning("get_vendor_bills HTTP %s for vendor %s: %s", resp.status_code, vendor_id, resp.text[:200])
                    return {"results": [], "error": f"HTTP {resp.status_code}"}
                data = resp.json()
        except Exception as e:
            logger.error("get_vendor_bills exception for vendor %s: %s", vendor_id, e)
            return {"results": [], "error": str(e)}

        if status == "outstanding":
            # Response: {"bills": [...], "total_outstanding": X}
            bills = data.get("bills", [])
            logger.info("get_vendor_bills: vendor=%s, bills_count=%d, total=%s", vendor_id, len(bills), data.get("total_outstanding", 0))
            return {
                "results": [
                    {
                        "id": b.get("id", ""),
                        "number": b.get("bill_number", ""),
                        "date": b.get("bill_date", ""),
                        "due_date": b.get("due_date", ""),
                        "total": str(b.get("total_amount", 0)),
                        "amount_paid": str(b.get("paid_amount", 0)),
                        "amount_due": str(b.get("remaining_amount", 0)),
                        "is_overdue": b.get("is_overdue", False),
                    }
                    for b in bills
                ],
                "total_outstanding": str(data.get("total_outstanding", 0)),
            }
        else:
            bills = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(bills, list):
                bills = []
            return {
                "results": [
                    {
                        "id": b.get("id", ""),
                        "number": b.get("bill_number", b.get("invoice_number", "")),
                        "date": b.get("bill_date", b.get("issue_date", "")),
                        "vendor_name": b.get("vendor_name", ""),
                        "total": str(b.get("total_amount", b.get("total", b.get("amount", 0)))),
                        "amount_paid": str(b.get("amount_paid", 0)),
                        "amount_due": str(b.get("amount_due", b.get("total_amount", b.get("total", 0)))),
                        "status": b.get("status", ""),
                    }
                    for b in bills
                ]
            }

    # --- Session Tool Execution ---

    async def _execute_session_tool(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a session tool (queries session-level data, not kernel API)."""
        if not self.session_manager or not self.session_id:
            return _error("NO_SESSION", "Session belum diinisialisasi.")

        if tool_name == "get_session_events":
            limit = min(params.get("limit", 10), 20)
            events = await self.session_manager.get_recent_events(
                self.session_id, limit=limit
            )
            return {"success": True, "data": events}

        elif tool_name == "search_chat_history":
            query = params.get("query", "")
            if not query:
                return _error("MISSING_QUERY", "Parameter 'query' wajib diisi.")
            days_back = min(params.get("days_back", 7), 30)
            results = await self.session_manager.search_chat_history(
                query, days_back=days_back
            )
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

        elif tool_name == "cancel_workflow":
            return await self._execute_cancel_workflow(params)

        return _error(
            "UNKNOWN_SESSION_TOOL", f"Session tool {tool_name!r} tidak dikenali."
        )

    # --- Start Workflow (Deterministic State Machine) ---

    async def _execute_start_workflow(self, params: dict) -> dict:
        """Execute start_workflow: advance the deterministic workflow engine."""
        from .workflow_engine import WorkflowEngine  # noqa: E402
        from .db_utils import get_session_db_pool  # noqa: E402

        pool = await get_session_db_pool()

        # Callback for complex tool operations (file import, etc.)
        async def execute_tool(tool_name, tool_params):
            return await self._execute_session_tool(tool_name, tool_params)

        engine = WorkflowEngine(  # noqa: F821
            db_pool=pool,
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            auth_token=self.context.auth_token,
            execute_tool=execute_tool,
        )

        workflow_type = params.get("workflow_type", "bank_reconciliation")
        user_data = params.get("user_data", {})

        # Auto-inject file_ref from user message if LLM didn't include it
        if not user_data.get("file_ref") and self.user_text:
            import re as _re  # noqa: E402

            m = _re.search(r"file_ref=(chat_upload:[^\]\s]+)", self.user_text)
            if m:
                user_data["file_ref"] = m.group(1)
                logger.info(
                    f"[Workflow] Auto-injected file_ref={m.group(1)} from user message"
                )

        # Task E: Auto-inject balance from user message text (robust parser)
        if not user_data.get("statement_ending_balance") and self.user_text:
            from .balance_parser import parse_balance  # noqa: E402

            _bal_val, _bal_found = parse_balance(self.user_text)
            if _bal_found and _bal_val is not None:
                user_data["statement_ending_balance"] = int(_bal_val)
                logger.info(
                    f"[Workflow] Auto-injected balance={_bal_val} from parse_balance"
                )

        # Use conversation session_id as chat_session_id
        chat_session_id = self.session_id or "unknown"

        # Detect REVIEWING state → use resume() to increment reviewed_count
        # Also detect AWAITING_DECISION for document review resume
        existing_state = await engine.get_state(chat_session_id, workflow_type)
        if existing_state and existing_state.current_state == "AWAITING_DECISION":
            logger.info(
                "[Workflow] State=AWAITING_DECISION → calling resume() (doc review decision)"
            )
            result = await engine.resume(
                chat_session_id=chat_session_id,
                workflow_type=workflow_type,
                confirmed_data=user_data,
            )
        elif existing_state and existing_state.current_state == "REVIEWING":
            logger.info(
                "[Workflow] State=REVIEWING → calling resume() (reviewed_count will increment)"
            )
            result = await engine.resume(
                chat_session_id=chat_session_id,
                workflow_type=workflow_type,
                confirmed_data=user_data,
            )
        else:
            result = await engine.process(
                chat_session_id=chat_session_id,
                workflow_type=workflow_type,
                user_data=user_data,
            )

        # Auto-propose: if engine is in REVIEWING with a suggestion, bypass LLM
        logger.info(
            f"[Workflow] Engine result: state={result.new_state}, auto_results type={type(result.auto_results).__name__}, keys={list(result.auto_results.keys()) if isinstance(result.auto_results, dict) else 'N/A'}"
        )
        if (
            result.new_state == "REVIEWING"
            and result.auto_results
            and isinstance(result.auto_results, dict)
        ):
            ar = result.auto_results
            line = (ar.get("review_item") or {}).get("statement_line", {})
            line_id = line.get("id", "")
            line_desc = line.get("description", "")
            line_date = line.get("date", "")
            line_amount = line.get("amount", 0)
            sid = ar.get("session_id", "")
            ba_id = ar.get("bank_account_id", "")
            ba_name = ar.get("bank_account_name", "")

            propose_params = None

            if ar.get("bill_suggestion"):
                bs = ar["bill_suggestion"]
                propose_params = {
                    "action_key": "create_bill_payment",
                    "payload": {
                        "vendor_id": bs.get("vendor_id", ""),
                        "bill_id": bs.get("bill_id", ""),
                        "vendor_name": bs.get("vendor_name", ""),
                        "bill_number": bs.get("bill_number", ""),
                        "bill_amount": bs.get("bill_amount", 0),
                        "amount_due": bs.get("amount_due", 0),
                        "total_amount": min(abs(line_amount), bs.get("amount_due", 0))
                        if line_amount
                        else bs.get("amount_due", 0),
                        "bank_account_id": ba_id,
                        "bank_account_name": ba_name,
                        "session_id": sid,
                        "statement_line_id": line_id,
                        "statement_description": line_desc,
                        "payment_date": line_date,
                    },
                }
            elif ar.get("invoice_suggestion"):
                ivs = ar["invoice_suggestion"]
                alloc_type = ivs.get("allocation_type", "single")
                if alloc_type != "needs_user_input":
                    allocations = ivs.get("allocations") or [
                        {
                            "invoice_id": ivs.get("invoice_id", ""),
                            "amount_applied": ivs.get("amount_due", line_amount),
                        }
                    ]
                    propose_params = {
                        "action_key": "create_receive_payment",
                        "payload": {
                            "customer_id": ivs.get("customer_id", ""),
                            "customer_name": ivs.get("customer_name", ""),
                            "invoice_numbers": ivs.get("invoice_number", ""),
                            "total_amount": ivs.get("amount_due", line_amount),
                            "allocations": allocations,
                            "bank_account_id": ba_id,
                            "bank_account_name": ba_name,
                            "session_id": sid,
                            "statement_line_id": line_id,
                            "statement_description": line_desc,
                            "payment_date": line_date,
                        },
                    }
            elif ar.get("category_suggestion"):
                cs = ar["category_suggestion"]
                propose_params = {
                    "action_key": "categorize_statement",
                    "payload": {
                        "account_id": cs.get("account_id", ""),
                        "account_name": cs.get("account_name", ""),
                        "session_id": sid,
                        "statement_line_id": line_id,
                        "statement_description": line_desc,
                        "statement_date": line_date,
                        "amount": line_amount,
                        "description": line_desc,
                    },
                }
            elif line_id:
                # Fallback: no suggestion — propose categorize without account_id
                # User sees the line details and picks the account themselves
                logger.info(
                    f"[Workflow] No suggestion for line {line_id}, fallback categorize_statement"
                )
                propose_params = {
                    "action_key": "categorize_statement",
                    "payload": {
                        "account_id": "",
                        "session_id": sid,
                        "statement_line_id": line_id,
                        "statement_description": line_desc,
                        "statement_date": line_date,
                        "amount": line_amount,
                        "description": line_desc,
                    },
                }

            if propose_params:
                logger.info(
                    f"[Workflow] Auto-propose {propose_params['action_key']} for line {line_id}"
                )
                propose_result = await self._execute_propose_direct(propose_params)
                logger.info(
                    f"[Workflow] Propose result: success={propose_result.get('success')}, msg_type={propose_result.get('message_type')}"
                )
                if not propose_result.get("success"):
                    logger.warning(
                        f"[Workflow] Auto-propose FAILED: {propose_result.get('error', 'unknown')}"
                    )
                if propose_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
                    # Enrich with narrative text + progress (in data for passthrough)
                    ri = ar.get("review_item") or {}
                    position = ri.get("position", 1)
                    remaining = ri.get("remaining", 0)
                    total = position + remaining
                    _item_line = f"{line_desc} — Rp {int(abs(line_amount)):,}".replace(
                        ",", "."
                    )
                    # Task A: On first item only, prepend import summary + breakdown
                    reviewed_count = ar.get("reviewed_count", 0)
                    _item_counter = ar.get("item_counter", "")
                    if reviewed_count == 0 and result.auto_results:
                        ar_all = result.auto_results
                        matched = ar_all.get(
                            "matched_count", ar_all.get("auto_matched", 0)
                        )
                        summary_data = (
                            ar_all.get("summary") or ar_all.get("session_stats") or {}
                        )
                        total_imported = summary_data.get(
                            "total_statement_lines",
                            summary_data.get("total_lines", total + matched),
                        )
                        account_name = ar_all.get(
                            "account_name",
                            ar_all.get("bank_account_name", "rekening bank"),
                        )
                        # Conversational narrative with breakdown
                        parts = [
                            f"Oke, rekening koran sudah diproses. Ada {total_imported} transaksi di {account_name}."
                        ]
                        if matched > 0:
                            parts.append(
                                f"{matched} transaksi langsung cocok dengan data di sistem."
                            )
                        if total == 1:
                            parts.append(
                                "Masih ada 1 transaksi yang perlu ditinjau manual."
                            )
                        else:
                            parts.append(
                                f"Masih ada {total} transaksi yang perlu ditinjau manual."
                            )
                        # Pre-scan breakdown from review_preview
                        rp = ar_all.get("review_preview", {})
                        breakdown_lines = []
                        bill_count = rp.get("bill_match", 0)
                        invoice_count = rp.get("invoice_match", 0)
                        no_match_count = rp.get("no_match", 0)
                        if bill_count > 0:
                            breakdown_lines.append(
                                f"• {bill_count} kemungkinan cocok dengan tagihan vendor."
                            )
                        if invoice_count > 0:
                            breakdown_lines.append(
                                f"• {invoice_count} kemungkinan cocok dengan invoice pelanggan."
                            )
                        if no_match_count > 0:
                            breakdown_lines.append(
                                f"• {no_match_count} perlu kategorisasi manual."
                            )
                        if breakdown_lines:
                            parts.append(
                                "Penilaian awal:\n" + "\n".join(breakdown_lines)
                            )
                        parts.append("Mari kita review satu per satu.")
                        narrative = "\n\n".join(parts)
                    else:
                        narrative = "Lanjut ke transaksi berikutnya."
                    propose_result["content"] = narrative
                    if "data" in propose_result and isinstance(
                        propose_result["data"], dict
                    ):
                        propose_result["data"]["progress"] = {
                            "current": position,
                            "total": total,
                        }
                        # Bridge: inject review_card for frontend InlineCard rendering
                        try:
                            _rc = _build_review_card(
                                line,
                                ar.get("bill_suggestion"),
                                ar.get("invoice_suggestion"),
                                ar.get("category_suggestion"),
                                ba_name,
                                position,
                                total,
                            )
                            propose_result["data"]["review_card"] = _rc
                            logger.info(
                                f"[Workflow] review_card injected: title={_rc.get('title_label')}, match={_rc.get('match', {}).get('type') if _rc.get('match') else 'none'}"
                            )
                        except Exception as e:
                            logger.warning(
                                f"[Workflow] review_card build failed: {e}",
                                exc_info=True,
                            )
                    return propose_result

        # ============ DOCUMENT REVIEW AUTO-PROPOSE ============
        if (
            workflow_type == "document_review"
            and result.auto_results
            and isinstance(result.auto_results, dict)
            and result.auto_results.get("confirm_suggestion")
        ):
            suggestion = result.auto_results["confirm_suggestion"]
            logger.info(
                f"[Workflow] Document review auto-propose: {suggestion.get('action_key')}"
            )
            propose_result = await self._execute_propose_direct(
                {
                    "action_key": suggestion["action_key"],
                    "payload": suggestion["payload"],
                }
            )
            if propose_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
                # Build narrative from instruction
                narrative = result.llm_instruction or result.auto_results.get(
                    "instruction", ""
                )
                propose_result["content"] = narrative
                propose_result["workflow_type"] = "document_review"
                propose_result["workflow_state"] = result.new_state
                return propose_result

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

    async def _execute_cancel_workflow(self, params: dict) -> dict:
        """Cancel an active workflow."""
        from .db_utils import get_session_db_pool  # noqa: E402

        workflow_type = params.get("workflow_type", "bank_reconciliation")
        chat_session_id = self.session_id or "unknown"

        pool = await get_session_db_pool()

        async def execute_tool(tool_name, tool_params):
            return await self._execute_session_tool(tool_name, tool_params)

        engine = WorkflowEngine(  # noqa: F821
            db_pool=pool,
            tenant_id=self.context.tenant_id,
            user_id=self.context.user_id,
            auth_token=self.context.auth_token,
            execute_tool=execute_tool,
        )

        existing_state = await engine.get_state(chat_session_id, workflow_type)
        if not existing_state:
            return {"text": "Tidak ada workflow aktif untuk dibatalkan."}

        # Set state to cancelled
        await engine.cancel(chat_session_id, workflow_type)
        return {"text": "Rekonsiliasi dibatalkan.", "cancelled": True}

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
                    unpaid_bills.extend(
                        bill_data.get(
                            "items", bill_data.get("data", bill_data.get("bills", []))
                        )
                    )

            if not unpaid_bills:
                return []

            suggestions = []
            for bill in unpaid_bills:
                bill_number = (bill.get("invoice_number") or "").upper()
                vendor_name = _safe_get_name(bill, "bill").upper()
                bill_amount = _to_amount(bill.get("total_amount", bill.get("amount", 0)))
                amount_due = bill_amount - _to_amount(bill.get("amount_paid", 0))
                amount_paid = _to_amount(bill.get("amount_paid", 0))  # display only

                confidence = None
                reason = ""

                # Match 1: Reference contains bill number → HIGH confidence
                if bill_number and (
                    bill_number in reference or bill_number in description
                ):
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
                elif (
                    vendor_name and len(vendor_name) > 2 and vendor_name in description
                ):
                    _vn_display = _safe_get_name(bill, "bill") or vendor_name
                    if amount == amount_due:
                        confidence = "MEDIUM"
                        reason = f"Vendor {_vn_display} cocok + jumlah persis Rp {int(amount_due):,}".replace(
                            ",", "."
                        )
                    elif amount == bill_amount:
                        confidence = "MEDIUM"
                        reason = f"Vendor {_vn_display} cocok + jumlah total Rp {int(bill_amount):,}".replace(
                            ",", "."
                        )
                    elif 0 < amount < amount_due:
                        confidence = "LOW"
                        reason = f"Vendor {_vn_display} cocok, kemungkinan pembayaran sebagian (Rp {int(amount):,} dari Rp {int(amount_due):,})".replace(
                            ",", "."
                        )

                # Match 3: Amount exact match only → LOW confidence
                elif amount == amount_due and amount_due > 0:
                    confidence = "LOW"
                    reason = f"Jumlah Rp {int(amount_due):,} cocok dengan sisa tagihan {bill_number}".replace(
                        ",", "."
                    )

                if confidence:
                    suggestions.append(
                        {
                            "bill_id": bill.get("id"),
                            "bill_number": bill.get("invoice_number"),
                            "vendor_id": _safe_get_id(bill, "bill"),
                            "vendor_name": _safe_get_name(bill, "bill"),
                            "bill_amount": int(bill_amount),
                            "amount_due": int(amount_due),
                            "amount_paid": int(amount_paid),
                            "due_date": bill.get("due_date"),
                            "confidence": confidence,
                            "reason": reason,
                        }
                    )

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
                        params=params,
                        headers=headers,
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
                        inv_data.get(
                            "items", inv_data.get("data", inv_data.get("invoices", []))
                        )
                    )

            if not unpaid_invoices:
                return []

            suggestions = []
            for inv in unpaid_invoices:
                inv_number = (inv.get("invoice_number") or "").upper()
                customer_name = _safe_get_name(inv, "invoice").upper()
                inv_amount = _to_amount(inv.get("amount", inv.get("total_amount", 0)))
                amount_due = _to_amount(inv.get("total_amount", 0)) - _to_amount(inv.get("amount_paid", 0))  # J-compliant: compute from journal-derived fields

                confidence = None
                reason = ""

                # Match 1: Reference contains invoice number -> HIGH
                if inv_number and (
                    inv_number in reference or inv_number in description
                ):
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
                elif (
                    customer_name
                    and len(customer_name) > 2
                    and customer_name in description
                ):
                    _cn_display = _safe_get_name(inv, "invoice") or customer_name
                    if amount == amount_due:
                        confidence = "MEDIUM"
                        reason = f"Pelanggan {_cn_display} cocok + jumlah persis Rp {int(amount_due):,}".replace(
                            ",", "."
                        )
                    elif amount == inv_amount:
                        confidence = "MEDIUM"
                        reason = f"Pelanggan {_cn_display} cocok + jumlah total Rp {int(inv_amount):,}".replace(
                            ",", "."
                        )

                # Match 3: Amount exact match only -> LOW
                elif amount == amount_due and amount_due > 0:
                    confidence = "LOW"
                    reason = f"Jumlah Rp {int(amount_due):,} cocok dengan sisa piutang {inv_number}".replace(
                        ",", "."
                    )

                if confidence:
                    suggestions.append(
                        {
                            "invoice_id": inv.get("id"),
                            "invoice_number": inv.get("invoice_number"),
                            "customer_id": _safe_get_id(inv, "invoice"),
                            "customer_name": _safe_get_name(inv, "invoice"),
                            "invoice_amount": int(inv_amount),
                            "amount_due": int(amount_due),
                            "due_date": inv.get("due_date"),
                            "confidence": confidence,
                            "reason": reason,
                        }
                    )

            # Sort: HIGH > MEDIUM > LOW
            priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            suggestions.sort(key=lambda s: priority.get(s["confidence"], 3))
            return suggestions

        except Exception as e:
            logger.warning(f"[InvoiceMatch] Error matching invoices: {e}")
            return []

    async def _resolve_coa_id(self, account_code: str, headers: dict) -> str | None:
        """Resolve account_code -> account_id via CoA API (Law 27 - no hardcoded IDs)."""
        base_url = "http://localhost:8000"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{base_url}/api/accounts",
                    params={"search": account_code, "limit": 5},
                    headers=headers,
                )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                # Exact code match (search is ILIKE, so verify exact)
                for item in items:
                    if item.get("code") == account_code:
                        return item.get("id")
                # Fallback: first result if only one
                if len(items) == 1:
                    return items[0].get("id")
            logger.warning(
                f"[AutoCat] CoA code '{account_code}' not found for tenant - "
                "pattern will be skipped."
            )
            return None
        except Exception as e:
            logger.warning(f"[AutoCat] CoA resolution error for '{account_code}': {e}")
            return None

    async def _auto_categorize(
        self, statement_line: dict, headers: dict
    ) -> dict | None:
        """
        Match statement line description against recon_category_patterns.
        Returns category suggestion or None.
        Queries: tenant-specific + system defaults (Law 24 RLS handles filtering).
        """
        description = (statement_line.get("description") or "").upper()
        if not description:
            return None

        try:
            from .db_utils import get_session_db_pool  # noqa: E402

            pool = await get_session_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    self.context.tenant_id,
                )
                patterns = await conn.fetch(
                    """
                    SELECT pattern_regex, account_code, description
                    FROM recon_category_patterns
                    WHERE (tenant_id = $1 OR (tenant_id IS NULL AND is_system_default = true))
                    ORDER BY priority DESC
                """,
                    self.context.tenant_id,
                )

            for row in patterns:
                try:
                    if re.search(row["pattern_regex"], description, re.IGNORECASE):
                        # Resolve account_code -> account_id via CoA (Law 27)
                        account_id = await self._resolve_coa_id(
                            row["account_code"], headers
                        )
                        if account_id:
                            return {
                                "account_code": row["account_code"],
                                "account_name": row["description"],
                                "account_id": account_id,
                                "pattern_matched": row["pattern_regex"],
                                "confidence": "PATTERN",
                            }
                        # account_id is None -> CoA code doesn't exist for this tenant
                except re.error:
                    logger.warning(
                        f"[AutoCat] Bad regex pattern: {row['pattern_regex']}"
                    )
                    continue

            return None

        except Exception as e:
            logger.warning(f"[AutoCat] Error: {e}")
            return None

    async def _execute_review_next_unmatched(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
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
                return _error(
                    "FETCH_FAILED", f"Gagal mengambil data: HTTP {resp.status_code}"
                )

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
                    suggestions = suggest_data.get(
                        "data", suggest_data.get("suggestions", [])
                    )
                    if suggestions:
                        suggestion = suggestions[0]
            except Exception:
                pass  # Suggestions are optional, don't fail

            # For DEBIT lines without a strong bank_tx suggestion: cross-reference outstanding bills
            bill_suggestion = None
            is_credit = line.get("is_credit", False)
            if not is_credit:  # debit = money out → potential bill payment
                try:
                    bill_matches = await self._match_against_outstanding_bills(
                        line, headers
                    )
                    if bill_matches:
                        best = bill_matches[0]
                        # Prefer bill match over weak bank_tx suggestion
                        if not suggestion or best["confidence"] in ("HIGH", "MEDIUM"):
                            bill_suggestion = best
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Bill matching failed (non-fatal): {e}"
                    )

            # Invoice matching for CREDIT lines
            invoice_suggestion = None
            invoice_matches = []
            if is_credit:  # credit = money in → potential invoice payment
                try:
                    invoice_matches = await self._match_against_outstanding_invoices(
                        line, headers
                    )
                    if invoice_matches:
                        best_inv = invoice_matches[0]
                        # Prefer invoice match over weak bank_tx suggestion
                        if not suggestion or best_inv["confidence"] in (
                            "HIGH",
                            "MEDIUM",
                        ):
                            invoice_suggestion = best_inv
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Invoice matching failed (non-fatal): {e}"
                    )

            # Auto-categorize: only if no bill/invoice match found
            category_suggestion = None
            if not bill_suggestion and not invoice_suggestion:
                try:
                    category_suggestion = await self._auto_categorize(line, headers)
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Auto-categorize failed (non-fatal): {e}"
                    )

            # Multi-invoice allocation
            if invoice_matches and invoice_suggestion:
                try:
                    line_amount = abs(_to_amount(line.get("amount", 0)))
                    allocation = find_allocation_options(invoice_matches, line_amount)
                    if allocation["type"] == "multi":
                        invoice_suggestion = {
                            **invoice_suggestion,
                            "allocation_type": "multi",
                            "allocations": [
                                {
                                    "invoice_id": m["invoice_id"],
                                    "amount_applied": int(_to_amount(m["amount_due"])),
                                }
                                for m in allocation["allocation"]
                            ],
                        }
                    elif allocation["type"] == "needs_user_input":
                        invoice_suggestion = {
                            **invoice_suggestion,
                            "allocation_type": "needs_user_input",
                            "options": allocation["options"],
                        }
                except Exception as e:
                    logger.warning(
                        f"[ReviewNext] Allocation logic failed (non-fatal): {e}"
                    )

            return {
                "success": True,
                "data": {
                    "has_more": True,
                    "remaining": max(total_unmatched - 1, 0),
                    "position": skip + 1,
                    "statement_line": {
                        "id": line.get("id"),
                        "date": line.get("transaction_date") or line.get("date"),
                        "description": line.get("description"),
                        "reference": line.get("reference"),
                        "amount": line.get("amount"),
                        "type": "credit" if line.get("is_credit") else "debit",
                    },
                    "suggestion": {
                        "transaction_id": suggestion.get("transaction_id")
                        or suggestion.get("id"),
                        "description": suggestion.get("description"),
                        "amount": suggestion.get("amount"),
                        "date": suggestion.get("transaction_date")
                        or suggestion.get("date"),
                        "confidence": suggestion.get("confidence")
                        or suggestion.get("score"),
                        "match_reason": suggestion.get("match_reason")
                        or suggestion.get("reason"),
                    }
                    if suggestion
                    else None,
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
                    }
                    if bill_suggestion
                    else None,
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
                    }
                    if invoice_suggestion
                    else None,
                    "category_suggestion": category_suggestion,
                    "session_id": session_id,
                },
            }

        except httpx.TimeoutException:
            return _error(
                "TIMEOUT", "Request timeout saat mengambil data rekonsiliasi."
            )
        except Exception as e:
            logger.exception(f"[ReviewNextUnmatched] Error: {e}")
            return _error("REVIEW_ERROR", f"Error: {str(e)[:200]}")

    # --- Agentic Reconcile (READ-ONLY — auto-match analysis) ---

    async def _execute_agentic_reconcile(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
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
                    json={
                        "max_actions": 50,
                        "include_categorize": True,
                        "include_exclude": True,
                    },
                )

            if resp.status_code != 200:
                return _error(
                    "AGENTIC_RECONCILE_FAILED",
                    f"Gagal menjalankan automatch: HTTP {resp.status_code} - {resp.text[:200]}",
                )

            data = resp.json()
            action_plan = data.get("action_plan", {})
            session_stats = data.get("session_stats", {})

            # Summarize results for the agent
            actions = action_plan.get("actions", [])
            match_count = sum(1 for a in actions if a.get("action_type") == "match")
            categorize_count = sum(
                1 for a in actions if a.get("action_type") == "categorize"
            )
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
            return _error(
                "TIMEOUT", "Request timeout saat menjalankan automatch rekonsiliasi."
            )
        except Exception as e:
            logger.exception(f"[AgenticReconcile] Error: {e}")
            return _error("AGENTIC_RECONCILE_ERROR", f"Error: {str(e)[:200]}")

    # --- Bank Statement Import Execution ---

    async def _execute_create_recon_session(self, params: dict) -> dict:
        # Code-level enforcement: statement_ending_balance is REQUIRED
        if params.get("statement_ending_balance") is None:
            return {
                "success": False,
                "error": "statement_ending_balance wajib diisi. Tanyakan saldo akhir rekening koran ke user sebelum membuat session rekonsiliasi.",
            }
        """Create or reuse a reconciliation session for a bank account."""
        from datetime import date as date_type
        import httpx  # noqa: E402

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
                        },
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
                if (
                    "sudah ada" in error_text.lower()
                    or "already" in error_text.lower()
                    or "in_progress" in error_text.lower()
                ):
                    return {
                        "success": False,
                        "error": "Sudah ada session rekonsiliasi aktif untuk akun ini. Gunakan session yang ada.",
                    }
                return {
                    "success": False,
                    "error": f"Gagal buat session rekonsiliasi: {error_text}",
                }

            data = resp.json()
            session_id = data.get("id", data.get("session_id", ""))
            return {
                "success": True,
                "data": {
                    "session_id": session_id,
                    "status": data.get("status", "in_progress"),
                    "mode": data.get("mode", "import"),
                    "message": f"Session rekonsiliasi berhasil dibuat (ID: {session_id}). Siap untuk import file.",
                },
            }
        except Exception as e:
            logger.error(f"Create recon session error: {e}")
            return {"success": False, "error": f"Gagal membuat session: {str(e)}"}

    async def _execute_import_bank_statement(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
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
                return _error(
                    "INVALID_FILE_REF",
                    f"File reference tidak valid atau file tidak ditemukan: {file_ref}",
                )

        if not session_id:
            return _error("MISSING_SESSION_ID", "Parameter 'session_id' wajib diisi.")
        if not file_path:
            return _error("MISSING_FILE_PATH", "Parameter 'file_path' wajib diisi.")

        import os  # noqa: E402

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

            import httpx  # noqa: E402

            base_url = "http://localhost:8000"
            headers = self._build_headers()

            # ── Auto-detect columns when no mapping provided ──────────────
            has_column_mapping = any(
                config.get(k)
                for k in (
                    "date_column",
                    "description_column",
                    "amount_column",
                    "debit_column",
                    "credit_column",
                )
            )
            # Only auto-detect for CSV/XLSX (not OFX which has fixed structure)
            if not has_column_mapping and config.get("format") in ("csv", "xlsx"):
                logger.info(
                    "[ImportBankStatement] No column mapping in config, auto-detecting..."
                )
                try:
                    # Direct call to auto_detect_columns — bypasses WAF
                    import pandas as pd  # noqa: E402
                    import io as _io  # noqa: E402

                    filename_lower = os.path.basename(file_path).lower()
                    if filename_lower.endswith((".xlsx", ".xls")):
                        df = pd.read_excel(_io.BytesIO(file_content), nrows=20)
                    else:
                        df = pd.read_csv(_io.BytesIO(file_content), nrows=20)

                    columns = [str(c) for c in df.columns.tolist()]
                    sample_rows = []
                    for _, row in df.iterrows():
                        sample_rows.append(
                            [str(v) if pd.notna(v) else "" for v in row.tolist()]
                        )

                    from ..column_mapper import auto_detect_columns  # noqa: E402
                    from ..unified_agent.db_utils import get_session_db_pool  # noqa: E402

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
                    logger.warning(
                        f"[ImportBankStatement] Column detection error: {detect_err}"
                    )
                    # Continue with import anyway — the import endpoint has its own defaults

            # ── Call import endpoint ──────────────────────────────────────
            # Remove Content-Type for multipart — let httpx set boundary automatically
            import_headers = {
                k: v for k, v in headers.items() if k.lower() != "content-type"
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{base_url}/api/bank-reconciliation/sessions/{session_id}/import",
                    headers=import_headers,
                    files={
                        "file": (
                            os.path.basename(file_path),
                            file_content,
                            "application/octet-stream",
                        )
                    },
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
                    error_detail = error_json.get(
                        "detail", error_json.get("message", response.text)
                    )
                except Exception:
                    pass
                return _error("IMPORT_FAILED", f"Import gagal: {error_detail}")

        except Exception as e:
            logger.exception(f"[ImportBankStatement] Error: {e}")
            return _error("IMPORT_ERROR", f"Error saat import: {str(e)[:200]}")

    async def _execute_read(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
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
                        return _error(
                            "INVALID_UUID", f"Parameter {key!r} bukan UUID valid."
                        )
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
                return {
                    "success": False,
                    "error": f"API returned {resp.status_code}: {resp.text[:200]}",
                    "error_type": "API_ERROR",
                    "status_code": resp.status_code,
                }
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

    async def _enrich_payload(
        self, action_type: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dispatch to action-specific enrichment.
        Registry-based: add new action types by adding a method + registry entry.
        Includes structured logging for observability (zero side effects).
        """
        import time as _time  # noqa: E402

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
                action_type,
                elapsed_ms,
            )
            return payload

        if not hasattr(self, enricher_name):
            logger.warning(
                "[ENRICH] WARNING: %s maps to %s but method not found",
                action_type,
                enricher_name,
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
            action_type,
            bf_str,
            sk_str,
            rn_str,
            elapsed_ms,
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
        """Add due_date = invoice_date + N days if not already set.

        BUG-05 fix: Also handles case where invoice_date is missing —
        falls back to today + N days.
        """
        if "due_date" not in payload or not payload["due_date"]:
            base_date_str = payload.get("invoice_date") or payload.get("issue_date")
            if base_date_str:
                try:
                    base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
                    payload["due_date"] = (base_date + timedelta(days=days)).strftime(
                        "%Y-%m-%d"
                    )
                except (ValueError, TypeError):
                    payload["due_date"] = (
                        datetime.now() + timedelta(days=days)
                    ).strftime("%Y-%m-%d")
            else:
                payload["due_date"] = (datetime.now() + timedelta(days=days)).strftime(
                    "%Y-%m-%d"
                )
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

            # BUG-02 fix: Reverse lookup — resolve customer_id from customer_name
            if not payload.get("customer_id") and payload.get("customer_name"):
                cust_name = payload["customer_name"]
                # Search customers by name via the list endpoint
                search_resp = await self._fetch_entity(
                    client, f"/api/customers?search={cust_name}&limit=5"
                )
                if search_resp:
                    items = (
                        search_resp
                        if isinstance(search_resp, list)
                        else search_resp.get("items", [])
                    )
                    if items:
                        # Exact match first (case-insensitive)
                        exact = next(
                            (
                                c
                                for c in items
                                if c.get("name", "").strip().lower()
                                == cust_name.strip().lower()
                            ),
                            None,
                        )
                        resolved = exact or items[0]
                        payload["customer_id"] = resolved.get("id", "")
                        # Also update customer_name to the canonical DB name
                        if resolved.get("name"):
                            payload["customer_name"] = resolved["name"]
                        logger.info(
                            f"BUG-02: Resolved customer_id={payload['customer_id']} from name={cust_name}"
                        )

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
                    if (
                        item_id
                        and "product_name" not in item
                        and "description" not in item
                    ):
                        detail = await self._fetch_entity(
                            client, f"/api/items/{item_id}"
                        )
                        if detail:
                            item["product_name"] = detail.get("name", "Item")
                            if "unit_price" not in item and "price" not in item:
                                item["price"] = detail.get(
                                    "purchase_price", detail.get("selling_price", 0)
                                )

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
                payload["due_date"] = (datetime.now() + timedelta(days=30)).strftime(
                    "%Y-%m-%d"
                )

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
                banks = (
                    banks_resp
                    if isinstance(banks_resp, list)
                    else (banks_resp.get("items") or banks_resp.get("data") or [])
                    if isinstance(banks_resp, dict)
                    else []
                )
                if banks:
                    # Direct match (already a bank account ID)
                    direct = next(
                        (b for b in banks if str(b.get("id")) == str(pt_id)), None
                    )
                    if direct:
                        pass  # Already correct bank_account_id
                    else:
                        # Try matching coa_id (LLM gave CoA ID instead of bank account ID)
                        coa_match = next(
                            (b for b in banks if str(b.get("coa_id")) == str(pt_id)),
                            None,
                        )
                        if coa_match:
                            payload["paid_through_id"] = str(coa_match["id"])
                            logger.info(
                                f"Expense enrich: CoA {pt_id} -> bank_account {coa_match['id']}"
                            )
                        else:
                            # No match — use first bank account as fallback
                            if banks:
                                payload["paid_through_id"] = str(banks[0]["id"])
                                logger.warning(
                                    f"Expense enrich: No bank match for {pt_id}, using default {banks[0]['id']}"
                                )
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
        payload = {
            k: v for k, v in params.items() if k not in _meta_keys and v is not None
        }

        # === ENRICHMENT STEP ===
        # LLM provides intent (IDs, qty, price).
        # Enrichment adds kernel-required fields (names, defaults, descriptions).
        payload = await self._enrich_payload(action_type, payload)
        logger.info(
            f"propose_action: type={action_type}, payload_keys={list(payload.keys())}"
        )

        if action_type not in ACTION_TYPE_MAP:
            return _error(
                "INVALID_ACTION_TYPE", f"Action type {action_type!r} tidak valid."
            )

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
                        {
                            "layer": e.get("layer", ""),
                            "code": e.get("code", ""),
                            "message": e.get("message", ""),
                        }
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

        if not prepare_result.get("success", False) and not prepare_result.get(
            "pending_action_id"
        ):
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
                        for l in journal_lines  # noqa: E741
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
        payload = {
            k: v for k, v in params.items() if k not in _meta_keys and v is not None
        }

        if action_type not in ACTION_TYPE_MAP:
            return _error(
                "INVALID_ACTION_TYPE", f"Action type {action_type!r} tidak valid."
            )

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
                    "errors": [
                        {
                            "layer": "DRY_RUN",
                            "code": "FAILED",
                            "message": "Simulasi gagal",
                        }
                    ],
                },
            }

        result = dry_run_result
        return {
            "success": True,
            "data": {
                "status": "SIMULATION_OK",
                "journal_lines": [
                    {
                        "account": l.get("account_name", ""),
                        "debit": l.get("debit", 0),
                        "credit": l.get("credit", 0),
                    }
                    for l in result.get("journal_entries", [])  # noqa: E741
                ],
                "total_debit": result.get("total_debit", 0),
                "total_credit": result.get("total_credit", 0),
                "balanced": result.get("balanced", False),
            },
        }

    # ═══════════════ CHART SPEC BUILDERS ═══════════════

    def _build_chart_spec(self, config: "ChartQueryConfig", data, params: dict) -> dict:
        """Build a ChartSpec from API response data."""
        import datetime  # noqa: E402

        spec = {
            "chart_type": config.chart_type,
            "render_target": "artifact"
            if config.complexity_hint == "complex"
            else "inline",
            "title": config.display_name,
            "subtitle": params.get("periode", datetime.date.today().strftime("%Y-%m")),
            "datasets": [],
            "labels": [],
            "slices": [],
            "features": config.chart_features or {},
            "summary": "",
            "highlights": [],
            "value_format": "currency",
        }

        try:
            transformers = {
                "chart_revenue_expense": self._chart_revenue_expense,
                "chart_cash_flow": self._chart_cash_flow,
                "chart_expense_breakdown": self._chart_expense_breakdown,
                "chart_top_customers": self._chart_top_customers,
                "chart_ar_aging": self._chart_ar_aging,
                # BATCH 1: Dashboard & KPI
                "chart_kas_composition": self._chart_kas_composition,
                "chart_cash_projection": self._chart_cash_projection,
                "chart_overdue_invoices": self._chart_overdue_invoices,
                "chart_overdue_bills": self._chart_overdue_bills,
                "chart_cash_flow_trends": self._chart_cash_flow_trends,
                "chart_dashboard_kpi": self._chart_dashboard_kpi,
                # BATCH 2: Laporan Keuangan
                "chart_neraca": self._chart_neraca,
                "chart_neraca_composition": self._chart_neraca_composition,
                "chart_profit_trend": self._chart_profit_trend,
                "chart_profit_comparison": self._chart_profit_comparison,
                "chart_gross_margin": self._chart_gross_margin,
                "chart_monthly_cashflow": self._chart_monthly_cashflow,
                # BATCH 3: AR/AP
                "chart_ap_aging": self._chart_ap_aging,
                "chart_ar_summary": self._chart_ar_summary,
                "chart_ap_summary": self._chart_ap_summary,
                "chart_invoice_status": self._chart_invoice_status,
                "chart_bill_status": self._chart_bill_status,
                "chart_payment_trends": self._chart_payment_trends,
                # BATCH 4: Inventory & Products
                "chart_top_products": self._chart_top_products,
                "chart_product_margins": self._chart_product_margins,
                "chart_slow_moving": self._chart_slow_moving,
                "chart_sales_trend": self._chart_sales_trend,
                "chart_top_vendors": self._chart_top_vendors,
                # BATCH 5: Financial Ratios
                "chart_profitability_ratios": self._chart_profitability_ratios,
                "chart_liquidity_ratios": self._chart_liquidity_ratios,
                "chart_leverage_ratios": self._chart_leverage_ratios,
                "chart_ratio_dashboard": self._chart_ratio_dashboard,
                # BATCH 6: Budget & Production
                "chart_budget_vs_actual": self._chart_budget_vs_actual,
                "chart_variance_alerts": self._chart_variance_alerts,
                "chart_production_costs": self._chart_production_costs,
            }
            transformer = transformers.get(config.action_key)
            if transformer:
                spec = transformer(data, spec)
        except Exception as e:
            logger.warning(f"Chart transform error for {config.action_key}: {e}")
            spec["summary"] = f"Data tersedia tapi gagal membuat grafik: {str(e)[:100]}"

        return spec

    def _chart_revenue_expense(self, data: dict, spec: dict) -> dict:
        """Transform laba-rugi into bar chart."""
        pendapatan = data.get("total_pendapatan", 0)
        beban = data.get("total_beban", 0)
        laba = data.get("laba_bersih", 0)

        # Try monthly breakdown first
        monthly = data.get("monthly_breakdown", data.get("details", []))
        if isinstance(monthly, list) and monthly:
            spec["labels"] = [
                str(m.get("bulan", m.get("month", m.get("name", "")))) for m in monthly
            ]
            spec["datasets"] = [
                {
                    "key": "pendapatan",
                    "label": "Pendapatan",
                    "values": [
                        float(m.get("pendapatan", m.get("total_pendapatan", 0)))
                        for m in monthly
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "beban",
                    "label": "Beban",
                    "values": [
                        float(m.get("beban", m.get("total_beban", 0))) for m in monthly
                    ],
                    "color": "#C45C4B",
                },
            ]
        else:
            spec["labels"] = ["Pendapatan", "Beban"]
            spec["datasets"] = [
                {
                    "key": "amount",
                    "label": "Jumlah",
                    "values": [float(pendapatan), float(beban)],
                }
            ]

        spec["highlights"] = [
            {
                "label": "Laba Bersih",
                "value": f"Rp {float(laba):,.0f}".replace(",", "."),
                "color": "success" if float(laba) >= 0 else "danger",
            }
        ]
        return spec

    def _chart_cash_flow(self, data: dict, spec: dict) -> dict:
        """Transform arus-kas into area chart."""
        # Endpoint returns nested: operasi.net_arus_kas_operasi, investasi.net_arus_kas_investasi, etc.
        operasi = data.get("operasi", data.get("operasional", {}))
        investasi = data.get("investasi", {})
        pendanaan = data.get("pendanaan", {})

        op = float(operasi.get("net_arus_kas_operasi", operasi.get("total", 0)))
        inv = float(investasi.get("net_arus_kas_investasi", investasi.get("total", 0)))
        fin = float(pendanaan.get("net_arus_kas_pendanaan", pendanaan.get("total", 0)))
        net = float(
            data.get("kenaikan_bersih_kas", data.get("net_cash_flow", op + inv + fin))
        )

        spec["labels"] = ["Operasional", "Investasi", "Pendanaan"]
        spec["datasets"] = [
            {
                "key": "amount",
                "label": "Arus Kas",
                "values": [op, inv, fin],
                "color": "#3B9FE8",
            }
        ]
        spec["highlights"] = [
            {
                "label": "Net Cash Flow",
                "value": f"Rp {net:,.0f}".replace(",", "."),
                "color": "success" if net >= 0 else "danger",
            }
        ]
        return spec

    def _chart_expense_breakdown(self, data, spec: dict) -> dict:
        """Transform top-expenses into donut."""
        items = (
            data
            if isinstance(data, list)
            else data.get("items", data.get("expenses", data.get("data", [])))
        )
        if not isinstance(items, list):
            items = []
        spec["slices"] = [
            {
                "label": str(
                    e.get("name", e.get("category", e.get("account_name", "Unknown")))
                ),
                "value": float(e.get("amount", e.get("total", 0))),
            }
            for e in items[:8]
        ]
        total = sum(s["value"] for s in spec["slices"])
        spec["summary"] = f"Total beban: Rp {total:,.0f}".replace(",", ".")
        return spec

    def _chart_top_customers(self, data, spec: dict) -> dict:
        """Transform pendapatan into horizontal bar."""
        items = (
            data
            if isinstance(data, list)
            else data.get("items", data.get("customers", data.get("data", [])))
        )
        if not isinstance(items, list):
            items = []
        top = items[:5]
        spec["labels"] = [
            self._truncate(str(c.get("name", c.get("customer_name", "")))) for c in top
        ]
        spec["datasets"] = [
            {
                "key": "revenue",
                "label": "Revenue",
                "values": [float(c.get("total", c.get("amount", 0))) for c in top],
                "color": "#5B8C51",
            }
        ]
        return spec

    def _chart_ar_aging(self, data, spec: dict) -> dict:
        """Transform aging-trend into line chart."""
        items = (
            data
            if isinstance(data, list)
            else data.get("trend", data.get("items", data.get("data", [])))
        )
        if not isinstance(items, list):
            items = []
        spec["labels"] = [
            str(t.get("period", t.get("date", t.get("as_of", "")))) for t in items
        ]
        spec["datasets"] = [
            {
                "key": "current",
                "label": "Lancar",
                "values": [float(t.get("current", 0)) for t in items],
                "color": "#5B8C51",
            },
            {
                "key": "overdue_30",
                "label": "30 hari",
                "values": [
                    float(t.get("overdue_30", t.get("30_days", 0))) for t in items
                ],
                "color": "#F5A623",
            },
            {
                "key": "overdue_60",
                "label": "60 hari",
                "values": [
                    float(t.get("overdue_60", t.get("60_days", 0))) for t in items
                ],
                "color": "#C45C4B",
            },
            {
                "key": "overdue_90",
                "label": "90+ hari",
                "values": [
                    float(t.get("overdue_90", t.get("90_days", 0))) for t in items
                ],
                "color": "#8B6AB8",
            },
        ]
        spec["drill_down"] = {
            "type": "query",
            "message_template": "Detail piutang overdue periode {label}",
        }
        return spec

    # ═══════════════ BATCH 1: Dashboard & KPI ═══════════════

    def _chart_kas_composition(self, data: dict, spec: dict) -> dict:
        """Transform kas-bank into donut: account balances."""
        accounts = data.get("accounts", [])
        spec["slices"] = [
            {
                "label": str(a.get("name", a.get("account_name", "Unknown"))),
                "value": abs(float(a.get("balance", a.get("ledger_balance", 0)))),
            }
            for a in accounts
            if float(a.get("balance", a.get("ledger_balance", 0))) != 0
        ]
        total = sum(s["value"] for s in spec["slices"])
        spec["summary"] = f"Total kas & bank: Rp {total:,.0f}".replace(",", ".")
        return spec

    def _chart_cash_projection(self, data: dict, spec: dict) -> dict:
        """Transform cash-flow-projection into area chart."""
        projections = data.get("projections", data.get("data", []))
        if isinstance(projections, list) and projections:
            spec["labels"] = [
                str(p.get("date", p.get("period", ""))) for p in projections
            ]
            spec["datasets"] = [
                {
                    "key": "projected",
                    "label": "Saldo Proyeksi",
                    "values": [
                        float(p.get("projected_balance", p.get("balance", 0)))
                        for p in projections
                    ],
                    "color": "#3B9FE8",
                },
                {
                    "key": "inflow",
                    "label": "Kas Masuk",
                    "values": [
                        float(p.get("inflow", p.get("kas_masuk", 0)))
                        for p in projections
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "outflow",
                    "label": "Kas Keluar",
                    "values": [
                        float(p.get("outflow", p.get("kas_keluar", 0)))
                        for p in projections
                    ],
                    "color": "#C45C4B",
                },
            ]
        else:
            spec["summary"] = "Tidak ada data proyeksi."
        return spec

    def _chart_overdue_invoices(self, data: dict, spec: dict) -> dict:
        """Transform overdue-invoices into horizontal bar."""
        invoices = data.get("invoices", [])
        spec["labels"] = [
            self._truncate(str(i.get("customer_name", i.get("name", "Unknown"))))
            for i in invoices[:10]
        ]
        spec["datasets"] = [
            {
                "key": "outstanding",
                "label": "Outstanding",
                "values": [
                    float(i.get("outstanding", i.get("amount", 0)))
                    for i in invoices[:10]
                ],
                "color": "#C45C4B",
            }
        ]
        total = float(data.get("total_outstanding", 0))
        count = int(data.get("count", len(invoices)))
        spec["highlights"] = [
            {
                "label": "Total Overdue",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "danger",
            }
        ]
        if count == 0:
            spec["summary"] = "Tidak ada invoice jatuh tempo."
        return spec

    def _chart_overdue_bills(self, data: dict, spec: dict) -> dict:
        """Transform overdue-bills into horizontal bar."""
        bills = data.get("bills", [])
        spec["labels"] = [
            self._truncate(str(b.get("vendor_name", b.get("name", "Unknown"))))
            for b in bills[:10]
        ]
        spec["datasets"] = [
            {
                "key": "outstanding",
                "label": "Outstanding",
                "values": [
                    float(b.get("outstanding", b.get("amount", 0))) for b in bills[:10]
                ],
                "color": "#C45C4B",
            }
        ]
        total = float(data.get("total_outstanding", 0))
        spec["highlights"] = [
            {
                "label": "Total Overdue",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "danger",
            }
        ]
        if not bills:
            spec["summary"] = "Tidak ada tagihan jatuh tempo."
        return spec

    def _chart_cash_flow_trends(self, data: dict, spec: dict) -> dict:
        """Transform cash-flow-trends into area chart."""
        trends = data.get("trends", [])
        spec["labels"] = [str(t.get("label", t.get("date", ""))) for t in trends]
        spec["datasets"] = [
            {
                "key": "kas_masuk",
                "label": "Kas Masuk",
                "values": [float(t.get("kas_masuk", 0)) for t in trends],
                "color": "#5B8C51",
            },
            {
                "key": "kas_keluar",
                "label": "Kas Keluar",
                "values": [float(t.get("kas_keluar", 0)) for t in trends],
                "color": "#C45C4B",
            },
        ]
        net = float(data.get("net_flow", 0))
        spec["highlights"] = [
            {
                "label": "Net Flow",
                "value": f"Rp {net:,.0f}".replace(",", "."),
                "color": "success" if net >= 0 else "danger",
            }
        ]
        return spec

    def _chart_dashboard_kpi(self, data: dict, spec: dict) -> dict:
        """Transform dashboard summary into KPI bar chart."""
        lr = data.get("laba_rugi", {})
        piutang = data.get("piutang", {})
        hutang = data.get("hutang", {})
        kas = data.get("kas_bank", {})
        spec["labels"] = ["Pendapatan", "Beban", "Piutang", "Hutang", "Kas"]
        spec["datasets"] = [
            {
                "key": "amount",
                "label": "Jumlah",
                "values": [
                    float(lr.get("pendapatan", 0)),
                    float(lr.get("pengeluaran", 0)),
                    float(piutang.get("total", 0)),
                    float(hutang.get("total", 0)),
                    float(kas.get("total", 0)),
                ],
            }
        ]
        profit = float(lr.get("profit", 0))
        spec["highlights"] = [
            {
                "label": "Laba Bersih",
                "value": f"Rp {profit:,.0f}".replace(",", "."),
                "color": "success" if profit >= 0 else "danger",
            }
        ]
        return spec

    # ═══════════════ BATCH 2: Laporan Keuangan ═══════════════

    def _chart_neraca(self, data: dict, spec: dict) -> dict:
        """Transform neraca into grouped bar: aset vs kewajiban+ekuitas."""
        al = float(data.get("aset_lancar", {}).get("total", 0))
        at_raw = data.get("aset_tetap", {})
        at = float(at_raw.get("total_neto", at_raw.get("total", 0)))
        kp = float(data.get("kewajiban_jangka_pendek", {}).get("total", 0))
        kj = float(data.get("kewajiban_jangka_panjang", {}).get("total", 0))
        eq = float(data.get("ekuitas", {}).get("total", 0))
        spec["labels"] = [
            "Aset Lancar",
            "Aset Tetap",
            "Kwjbn Pendek",
            "Kwjbn Panjang",
            "Ekuitas",
        ]
        spec["datasets"] = [
            {
                "key": "aset",
                "label": "Aset",
                "values": [al, at, 0, 0, 0],
                "color": "#3B9FE8",
            },
            {
                "key": "kewajiban_ekuitas",
                "label": "Kewajiban & Ekuitas",
                "values": [0, 0, kp, kj, eq],
                "color": "#F5A623",
            },
        ]
        balanced = data.get("is_balanced", True)
        spec["highlights"] = [
            {
                "label": "Balance",
                "value": "Seimbang" if balanced else "TIDAK SEIMBANG",
                "color": "success" if balanced else "danger",
            }
        ]
        return spec

    def _chart_neraca_composition(self, data: dict, spec: dict) -> dict:
        """Transform neraca into donut: aset composition."""
        al = data.get("aset_lancar", {})
        slices = []
        for key, label in [
            ("kas", "Kas"),
            ("piutang_usaha", "Piutang"),
            ("persediaan", "Persediaan"),
            ("beban_dibayar_dimuka", "Beban Dibayar Dimuka"),
            ("uang_muka_pembelian", "Uang Muka"),
        ]:
            val = float(al.get(key, 0))
            if val > 0:
                slices.append({"label": label, "value": val})
        at = data.get("aset_tetap", {})
        at_total = float(at.get("total_neto", at.get("total", 0)))
        if at_total > 0:
            slices.append({"label": "Aset Tetap", "value": at_total})
        spec["slices"] = slices
        total = float(data.get("total_aset", sum(s["value"] for s in slices)))
        spec["summary"] = f"Total aset: Rp {total:,.0f}".replace(",", ".")
        return spec

    def _chart_profit_trend(self, data: dict, spec: dict) -> dict:
        """Transform laba-rugi into line chart trend (reuses revenue_expense logic for line)."""
        monthly = data.get("monthly_breakdown", data.get("details", []))
        if isinstance(monthly, list) and monthly:
            spec["labels"] = [
                str(m.get("bulan", m.get("month", m.get("name", "")))) for m in monthly
            ]
            spec["datasets"] = [
                {
                    "key": "pendapatan",
                    "label": "Pendapatan",
                    "values": [
                        float(m.get("pendapatan", m.get("total_pendapatan", 0)))
                        for m in monthly
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "beban",
                    "label": "Beban",
                    "values": [
                        float(m.get("beban", m.get("total_beban", 0))) for m in monthly
                    ],
                    "color": "#C45C4B",
                },
                {
                    "key": "laba",
                    "label": "Laba Bersih",
                    "values": [
                        float(m.get("laba_bersih", m.get("laba", 0))) for m in monthly
                    ],
                    "color": "#3B9FE8",
                },
            ]
        else:
            p = float(data.get("total_pendapatan", 0))
            b = float(data.get("total_beban", 0))
            l = float(data.get("laba_bersih", p - b))  # noqa: E741
            spec["labels"] = ["Periode"]
            spec["datasets"] = [
                {
                    "key": "pendapatan",
                    "label": "Pendapatan",
                    "values": [p],
                    "color": "#5B8C51",
                },
                {"key": "beban", "label": "Beban", "values": [b], "color": "#C45C4B"},
                {"key": "laba", "label": "Laba", "values": [l], "color": "#3B9FE8"},
            ]
        return spec

    def _chart_profit_comparison(self, data: dict, spec: dict) -> dict:
        """Transform profit-loss comparison into grouped bar."""
        rev1 = float(data.get("revenue", {}).get("total", 0))
        exp1 = float(data.get("operating_expenses", {}).get("total", 0))
        cogs1 = float(data.get("cost_of_goods_sold", {}).get("total", 0))
        ni1 = float(data.get("net_income", 0))
        comp = data.get("comparison", {})
        rev2 = float(comp.get("revenue", {}).get("total", 0))
        exp2 = float(comp.get("operating_expenses", {}).get("total", 0))
        cogs2 = float(comp.get("cost_of_goods_sold", {}).get("total", 0))
        ni2 = float(comp.get("net_income", 0))
        spec["labels"] = ["Pendapatan", "HPP", "Beban Operasi", "Laba Bersih"]
        spec["datasets"] = [
            {
                "key": "period1",
                "label": "Periode 1",
                "values": [rev1, cogs1, exp1, ni1],
                "color": "#3B9FE8",
            },
            {
                "key": "period2",
                "label": "Periode 2",
                "values": [rev2, cogs2, exp2, ni2],
                "color": "#F5A623",
            },
        ]
        return spec

    def _chart_gross_margin(self, data: dict, spec: dict) -> dict:
        """Transform laba-rugi into revenue/COGS/gross profit bars."""
        revenue = float(data.get("total_pendapatan", 0))
        cogs = float(data.get("total_hpp", data.get("harga_pokok", {}).get("total", 0)))
        gross = float(data.get("laba_kotor", revenue - cogs))
        spec["labels"] = ["Pendapatan", "HPP", "Laba Kotor"]
        spec["datasets"] = [
            {"key": "amount", "label": "Jumlah", "values": [revenue, cogs, gross]}
        ]
        margin_pct = (gross / revenue * 100) if revenue > 0 else 0
        spec["highlights"] = [
            {
                "label": "Margin",
                "value": f"{margin_pct:.1f}%",
                "color": "success" if margin_pct > 20 else "danger",
            }
        ]
        return spec

    def _chart_monthly_cashflow(self, data: dict, spec: dict) -> dict:
        """Transform arus-kas into monthly area chart (same as cash_flow but area)."""
        return self._chart_cash_flow(data, spec)

    # ═══════════════ BATCH 3: AR/AP ═══════════════

    def _chart_ap_aging(self, data: dict, spec: dict) -> dict:
        """Transform ap-aging into stacked bar."""
        summary = data.get("summary", data)
        labels = [
            "Lancar",
            "1-30 hari",
            "31-60 hari",
            "61-90 hari",
            "91-120 hari",
            ">120 hari",
        ]
        values = [
            float(summary.get("total_current", 0)),
            float(summary.get("total_1_30", 0)),
            float(summary.get("total_31_60", 0)),
            float(summary.get("total_61_90", 0)),
            float(summary.get("total_91_120", 0)),
            float(summary.get("total_over_120", 0)),
        ]
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "amount", "label": "Hutang", "values": values, "color": "#C45C4B"}
        ]
        total = float(summary.get("grand_total", sum(values)))
        spec["highlights"] = [
            {
                "label": "Total AP",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "neutral",
            }
        ]
        return spec

    def _chart_ar_summary(self, data: dict, spec: dict) -> dict:
        """Transform dashboard piutang into donut."""
        spec["slices"] = []
        for key, label in [
            ("current", "Lancar"),
            ("overdue_1_30", "1-30 hari"),
            ("overdue_31_60", "31-60 hari"),
            ("overdue_61_90", "61-90 hari"),
            ("overdue_90_plus", "90+ hari"),
        ]:
            val = float(data.get(key, 0))
            if val > 0:
                spec["slices"].append({"label": label, "value": val})
        total = float(data.get("total", 0))
        spec["summary"] = f"Total piutang: Rp {total:,.0f}".replace(",", ".")
        if not spec["slices"] and total > 0:
            spec["slices"] = [{"label": "Lancar", "value": total}]
        return spec

    def _chart_ap_summary(self, data: dict, spec: dict) -> dict:
        """Transform dashboard hutang into donut."""
        spec["slices"] = []
        for key, label in [
            ("current", "Lancar"),
            ("overdue_1_30", "1-30 hari"),
            ("overdue_31_60", "31-60 hari"),
            ("overdue_61_90", "61-90 hari"),
            ("overdue_90_plus", "90+ hari"),
        ]:
            val = float(data.get(key, 0))
            if val > 0:
                spec["slices"].append({"label": label, "value": val})
        total = float(data.get("total", 0))
        spec["summary"] = f"Total hutang: Rp {total:,.0f}".replace(",", ".")
        if not spec["slices"] and total > 0:
            spec["slices"] = [{"label": "Lancar", "value": total}]
        return spec

    def _chart_invoice_status(self, data: dict, spec: dict) -> dict:
        """Transform sales-invoices/summary into donut."""
        status_map = [
            ("draft_count", "Draft"),
            ("posted_count", "Posted"),
            ("partial_count", "Partial"),
            ("paid_count", "Lunas"),
            ("overdue_count", "Overdue"),
        ]
        spec["slices"] = [
            {"label": label, "value": float(data.get(key, 0))}
            for key, label in status_map
            if float(data.get(key, 0)) > 0
        ]
        total = int(data.get("total_count", 0))
        outstanding = float(data.get("total_outstanding", 0))
        spec[
            "summary"
        ] = f"{total} invoice, outstanding: Rp {outstanding:,.0f}".replace(",", ".")
        spec["value_format"] = "number"
        return spec

    def _chart_bill_status(self, data: dict, spec: dict) -> dict:
        """Transform bills/summary into donut."""
        breakdown = data.get("breakdown", {})
        spec["slices"] = []
        for key, label in [
            ("paid", "Lunas"),
            ("partial", "Partial"),
            ("unpaid", "Belum Bayar"),
            ("overdue", "Overdue"),
        ]:
            val = float(breakdown.get(key, {}).get("count", 0))
            if val > 0:
                spec["slices"].append({"label": label, "value": val})
        total = int(data.get("total_count", 0))
        spec["summary"] = f"Total {total} tagihan"
        spec["value_format"] = "number"
        return spec

    def _chart_payment_trends(self, data: dict, spec: dict) -> dict:
        """Transform bill-payments/summary into bar by method."""
        by_method = data.get("by_method", {})
        spec["labels"] = []
        values = []
        method_labels = {
            "bank_transfer": "Transfer Bank",
            "cash": "Tunai",
            "cheque": "Cek/Giro",
            "other": "Lainnya",
        }
        for method, info in by_method.items():
            spec["labels"].append(method_labels.get(method, method))
            values.append(float(info.get("amount", 0)))
        spec["datasets"] = [
            {"key": "amount", "label": "Jumlah", "values": values, "color": "#3B9FE8"}
        ]
        total = float(data.get("total_paid", 0))
        spec["highlights"] = [
            {
                "label": "Total Bayar",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "neutral",
            }
        ]
        return spec

    # ═══════════════ BATCH 4: Inventory & Products ═══════════════

    def _chart_top_products(self, data: dict, spec: dict) -> dict:
        """Transform top-products into horizontal bar."""
        products = data.get("products", [])
        spec["labels"] = [
            self._truncate(str(p.get("product_name", ""))) for p in products[:10]
        ]
        spec["datasets"] = [
            {
                "key": "qty",
                "label": "Qty Terjual",
                "values": [float(p.get("total_qty_sold", 0)) for p in products[:10]],
                "color": "#5B8C51",
            }
        ]
        spec["value_format"] = "number"
        return spec

    def _chart_product_margins(self, data: dict, spec: dict) -> dict:
        """Transform product-margins into grouped bar."""
        products = data.get("products", [])
        top = [p for p in products if float(p.get("total_revenue", 0)) > 0][:10]
        spec["labels"] = [
            self._truncate(str(p.get("product_name", "")), 20) for p in top
        ]
        spec["datasets"] = [
            {
                "key": "revenue",
                "label": "Revenue",
                "values": [float(p.get("total_revenue", 0)) for p in top],
                "color": "#5B8C51",
            },
            {
                "key": "cogs",
                "label": "HPP",
                "values": [float(p.get("total_cogs", 0)) for p in top],
                "color": "#C45C4B",
            },
            {
                "key": "profit",
                "label": "Profit",
                "values": [float(p.get("total_profit", 0)) for p in top],
                "color": "#3B9FE8",
            },
        ]
        return spec

    def _chart_slow_moving(self, data: dict, spec: dict) -> dict:
        """Transform slow-moving-products into horizontal bar."""
        products = data.get("products", [])
        spec["labels"] = [
            self._truncate(str(p.get("product_name", ""))) for p in products[:10]
        ]
        spec["datasets"] = [
            {
                "key": "qty",
                "label": "Qty Terjual",
                "values": [float(p.get("total_qty_sold", 0)) for p in products[:10]],
                "color": "#F5A623",
            }
        ]
        if not products:
            spec["summary"] = "Tidak ada produk slow-moving."
        spec["value_format"] = "number"
        return spec

    def _chart_sales_trend(self, data: dict, spec: dict) -> dict:
        """Transform daily-summary into line chart."""
        items = (
            data if isinstance(data, list) else data.get("items", data.get("data", []))
        )
        if isinstance(items, list) and items:
            spec["labels"] = [str(i.get("date", i.get("tanggal", ""))) for i in items]
            spec["datasets"] = [
                {
                    "key": "total",
                    "label": "Penjualan",
                    "values": [
                        float(i.get("total", i.get("total_amount", 0))) for i in items
                    ],
                    "color": "#5B8C51",
                },
                {
                    "key": "count",
                    "label": "Jumlah Transaksi",
                    "values": [
                        float(i.get("count", i.get("transaction_count", 0)))
                        for i in items
                    ],
                    "color": "#3B9FE8",
                },
            ]
        else:
            spec["summary"] = "Tidak ada data penjualan."
        return spec

    def _chart_top_vendors(self, data: dict, spec: dict) -> dict:
        """Transform vendors list into horizontal bar by AP balance."""
        items = data.get("items", data) if isinstance(data, dict) else data
        if isinstance(items, list):
            sorted_v = sorted(
                items, key=lambda v: float(v.get("ap_balance", 0)), reverse=True
            )[:10]
            spec["labels"] = [
                self._truncate(str(v.get("name", v.get("display_name", ""))))
                for v in sorted_v
            ]
            spec["datasets"] = [
                {
                    "key": "ap_balance",
                    "label": "Saldo Hutang",
                    "values": [float(v.get("ap_balance", 0)) for v in sorted_v],
                    "color": "#C45C4B",
                }
            ]
        return spec

    # ═══════════════ BATCH 5: Financial Ratios ═══════════════

    def _chart_profitability_ratios(self, data: dict, spec: dict) -> dict:
        """Transform financial-ratios profitability section into bar."""
        ratios = data.get("ratios", {}).get("profitability", {})
        labels, values = [], []
        for key, label in [
            ("roa", "ROA"),
            ("roe", "ROE"),
            ("net_profit_margin", "Net Margin"),
            ("gross_profit_margin", "Gross Margin"),
        ]:
            r = ratios.get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                values.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "pct", "label": "%", "values": values, "color": "#5B8C51"}
        ]
        spec["value_format"] = "percent"
        return spec

    def _chart_liquidity_ratios(self, data: dict, spec: dict) -> dict:
        """Transform financial-ratios liquidity section into bar."""
        ratios = data.get("ratios", {}).get("liquidity", {})
        labels, values = [], []
        for key, label in [
            ("cash_ratio", "Cash Ratio"),
            ("quick_ratio", "Quick Ratio"),
            ("current_ratio", "Current Ratio"),
        ]:
            r = ratios.get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                values.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "ratio", "label": "Rasio", "values": values, "color": "#3B9FE8"}
        ]
        wc = ratios.get("working_capital", {}).get("value")
        if wc is not None:
            spec["highlights"] = [
                {
                    "label": "Working Capital",
                    "value": f"Rp {float(wc):,.0f}".replace(",", "."),
                    "color": "neutral",
                }
            ]
        spec["value_format"] = "number"
        return spec

    def _chart_leverage_ratios(self, data: dict, spec: dict) -> dict:
        """Transform financial-ratios leverage section into bar."""
        ratios = data.get("ratios", {}).get(
            "leverage", data.get("ratios", {}).get("solvency", {})
        )
        labels, values = [], []
        for key, label in [
            ("debt_to_equity", "Debt/Equity"),
            ("debt_to_asset", "Debt/Asset"),
            ("equity_ratio", "Equity Ratio"),
            ("debt_ratio", "Debt Ratio"),
        ]:
            r = ratios.get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                values.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {"key": "ratio", "label": "Rasio", "values": values, "color": "#F5A623"}
        ]
        spec["value_format"] = "number"
        return spec

    def _chart_ratio_dashboard(self, data: dict, spec: dict) -> dict:
        """Transform all financial-ratios into grouped bar dashboard."""
        all_ratios = data.get("ratios", {})
        labels, prof_vals, liq_vals, lev_vals = [], [], [], []
        # Profitability
        for key, label in [
            ("roa", "ROA"),
            ("roe", "ROE"),
            ("net_profit_margin", "Net Margin"),
        ]:
            r = all_ratios.get("profitability", {}).get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                prof_vals.append(float(val))
                liq_vals.append(0)
                lev_vals.append(0)
        # Liquidity
        for key, label in [("current_ratio", "Current"), ("quick_ratio", "Quick")]:
            r = all_ratios.get("liquidity", {}).get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                prof_vals.append(0)
                liq_vals.append(float(val))
                lev_vals.append(0)
        # Leverage
        for key, label in [("debt_to_equity", "D/E"), ("debt_to_asset", "D/A")]:
            r = all_ratios.get("leverage", all_ratios.get("solvency", {})).get(key, {})
            val = r.get("value")
            if val is not None:
                labels.append(label)
                prof_vals.append(0)
                liq_vals.append(0)
                lev_vals.append(float(val))
        spec["labels"] = labels
        spec["datasets"] = [
            {
                "key": "profitability",
                "label": "Profitabilitas",
                "values": prof_vals,
                "color": "#5B8C51",
            },
            {
                "key": "liquidity",
                "label": "Likuiditas",
                "values": liq_vals,
                "color": "#3B9FE8",
            },
            {
                "key": "leverage",
                "label": "Leverage",
                "values": lev_vals,
                "color": "#F5A623",
            },
        ]
        spec["value_format"] = "number"
        return spec

    # ═══════════════ BATCH 6: Budget & Production ═══════════════

    def _chart_budget_vs_actual(self, data: dict, spec: dict) -> dict:
        """Transform budget vs-actual into grouped bar."""
        items = data.get("items", data.get("line_items", []))
        if isinstance(items, list) and items:
            spec["labels"] = [
                self._truncate(str(i.get("account_name", i.get("name", ""))))
                for i in items[:15]
            ]
            spec["datasets"] = [
                {
                    "key": "budget",
                    "label": "Budget",
                    "values": [
                        float(i.get("budget_amount", i.get("budgeted", 0)))
                        for i in items[:15]
                    ],
                    "color": "#3B9FE8",
                },
                {
                    "key": "actual",
                    "label": "Aktual",
                    "values": [
                        float(i.get("actual_amount", i.get("actual", 0)))
                        for i in items[:15]
                    ],
                    "color": "#5B8C51",
                },
            ]
        else:
            spec["summary"] = "Tidak ada data budget."
        return spec

    def _chart_variance_alerts(self, data: dict, spec: dict) -> dict:
        """Transform variance-alerts into horizontal bar."""
        alerts = data.get("alerts", data.get("items", []))
        if isinstance(alerts, list) and alerts:
            spec["labels"] = [
                self._truncate(str(a.get("account_name", a.get("name", ""))))
                for a in alerts[:10]
            ]
            spec["datasets"] = [
                {
                    "key": "variance_pct",
                    "label": "Varians %",
                    "values": [
                        float(a.get("variance_pct", a.get("variance_percent", 0)))
                        for a in alerts[:10]
                    ],
                    "color": "#C45C4B",
                }
            ]
        else:
            spec["summary"] = "Tidak ada peringatan varians."
        return spec

    def _chart_production_costs(self, data: dict, spec: dict) -> dict:
        """Transform production cost-analysis into bar."""
        material = float(data.get("material_cost", data.get("total_material", 0)))
        labor = float(data.get("labor_cost", data.get("total_labor", 0)))
        overhead = float(data.get("overhead_cost", data.get("total_overhead", 0)))
        total = float(data.get("total_cost", material + labor + overhead))
        spec["labels"] = ["Material", "Tenaga Kerja", "Overhead"]
        spec["datasets"] = [
            {
                "key": "cost",
                "label": "Biaya",
                "values": [material, labor, overhead],
                "color": "#3B9FE8",
            }
        ]
        spec["highlights"] = [
            {
                "label": "Total Biaya",
                "value": f"Rp {total:,.0f}".replace(",", "."),
                "color": "neutral",
            }
        ]
        return spec


# --- Helpers ---



    # --- Update Document Context (Layer 2 document edit) ---

    async def _execute_update_document_context(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle user corrections to active document context.
        Merges edits into Layer 2 document_context, expires old pending action.
        """
        import uuid as _uuid
        edits = params.get("edits", {})
        if not edits:
            return {"success": False, "error": "No edits provided"}

        if not self.session_manager or not self.session_id:
            return {"success": False, "error": "No session context available"}

        state = await self.session_manager.get_state(self.session_id)
        doc_ctx = getattr(state, "document_context", None)
        if not doc_ctx:
            return {"success": False, "error": "Tidak ada dokumen aktif. User belum upload dokumen."}

        # Deep-merge edits
        existing_edits = doc_ctx.get("edits", {})
        for key, value in edits.items():
            if key == "items" and isinstance(value, dict):
                existing_items_edits = existing_edits.get("items", {})
                for idx_str, item_edits in value.items():
                    if idx_str in existing_items_edits:
                        existing_items_edits[idx_str] = {**existing_items_edits[idx_str], **item_edits}
                    else:
                        existing_items_edits[idx_str] = item_edits
                existing_edits["items"] = existing_items_edits
            else:
                existing_edits[key] = value
        doc_ctx["edits"] = existing_edits

        # Expire old pending action
        old_pending_id = doc_ctx.get("pending_action_id")
        if old_pending_id:
            try:
                from .db_utils import get_session_db_pool
                pool = await get_session_db_pool()
                await pool.execute(
                    "UPDATE pending_actions SET status = 'EXPIRED' WHERE id = $1 AND tenant_id = $2",
                    _uuid.UUID(str(old_pending_id)), self.context.tenant_id,
                )
            except Exception as e:
                logger.warning(f"[UpdateDocCtx] Failed to expire old pending action: {e}")

        # Update Layer 2
        await self.session_manager.update_state(self.session_id, document_context=doc_ctx)

        # Build summary
        edit_parts = []
        for k, v in edits.items():
            if k != "items":
                edit_parts.append(f"{k}={v}")
        if "items" in edits:
            edit_parts.append(f"{len(edits['items'])} item dikoreksi")

        return {
            "success": True,
            "message": f"Koreksi diterapkan: {', '.join(edit_parts)}. Data dokumen sudah diperbarui.",
            "replaces_action_id": old_pending_id,
            "document_id": doc_ctx.get("document_id"),
        }


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


def _generate_idempotency_key(
    tenant_id: str, action_type: str, payload: Dict[str, Any]
) -> str:
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
        return data[:MAX_LIST_ITEMS] + [
            {"_truncated": True, "_total": len(data), "_showing": MAX_LIST_ITEMS}
        ]
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and len(value) > MAX_LIST_ITEMS:
                data[key] = value[:MAX_LIST_ITEMS] + [
                    {
                        "_truncated": True,
                        "_total": len(value),
                        "_showing": MAX_LIST_ITEMS,
                    }
                ]
    return data


# ─── review_card helpers (Bridge: Tahap 5F backend) ───────────────────────────


def _fmt_idr(amount) -> str:
    """Format number as Indonesian currency string (dots as thousands)."""
    try:
        return f"{abs(float(amount)):,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return "0"


def _build_review_card(
    statement_line: dict,
    bill_suggestion: dict | None,
    invoice_suggestion: dict | None,
    category_suggestion: dict | None,
    bank_account_name: str,
    item_number: int,
    total_items: int,
) -> dict:
    """
    Build review_card payload for frontend InlineCard rendering.
    Called inside _execute_start_workflow after auto-propose succeeds.
    """
    line_amount = abs(float(statement_line.get("amount", 0)))
    is_credit = bool(statement_line.get("is_credit", False))

    # Variant label from match type
    if bill_suggestion:
        confidence = bill_suggestion.get("confidence", "LOW")
        variant = "auto-match" if confidence == "HIGH" else "suggested"
    elif invoice_suggestion:
        confidence = invoice_suggestion.get("confidence", "LOW")
        variant = "auto-match" if confidence == "HIGH" else "suggested"
    elif category_suggestion:
        variant = "suggested"
    else:
        variant = "kategorisasi"

    title_label = f"Review {item_number} / {total_items} \u00b7 {variant}"

    # Match section
    match_data = None
    if bill_suggestion:
        match_data = {
            "type": "bill",
            "label": "Cocokkan",
            "name": f"Bill {bill_suggestion.get('bill_number', '')}",
            "detail": f"{bill_suggestion.get('vendor_name', '')} \u00b7 outstanding {_fmt_idr(bill_suggestion.get('amount_due', 0))}",
            "amount": float(bill_suggestion.get("amount_due", 0)),
        }
    elif invoice_suggestion:
        match_data = {
            "type": "invoice",
            "label": "Cocokkan",
            "name": f"Invoice {invoice_suggestion.get('invoice_number', '')}",
            "detail": f"{invoice_suggestion.get('customer_name', '')} \u00b7 outstanding {_fmt_idr(invoice_suggestion.get('amount_due', 0))}",
            "amount": float(invoice_suggestion.get("amount_due", 0)),
        }
    elif category_suggestion:
        match_data = {
            "type": "expense_account",
            "label": "Catat ke",
            "name": f"{category_suggestion.get('account_code', '')} {category_suggestion.get('account_name', '')}",
            "detail": "",
        }

    # Warning (amount mismatch)
    warning = None
    if match_data and match_data.get("amount"):
        diff = abs(line_amount - match_data["amount"])
        if diff > 0.01:
            warning = f"Selisih {_fmt_idr(diff)} \u2014 pembayaran sebagian"

    # Journal preview lines
    journal_lines = _build_journal_preview(
        statement_line,
        bill_suggestion,
        invoice_suggestion,
        category_suggestion,
        bank_account_name,
    )

    # Button labels
    if bill_suggestion or invoice_suggestion:
        confirm_label = "Ok, cocokkan"
    elif category_suggestion:
        confirm_label = "Ok, catat biaya"
    else:
        confirm_label = "Ok"

    return {
        "title_label": title_label,
        "statement": {
            "description": statement_line.get("description", ""),
            "date": str(
                statement_line.get("date", "")
                or statement_line.get("transaction_date", "")
            ),
            "amount": line_amount,
            "is_credit": is_credit,
        },
        "match": match_data,
        "warning": warning,
        "journal_lines": journal_lines,
        "cancel_label": "Lewati",
        "confirm_label": confirm_label,
    }


def _build_journal_preview(
    statement_line: dict,
    bill_suggestion: dict | None,
    invoice_suggestion: dict | None,
    category_suggestion: dict | None,
    bank_account_name: str,
) -> list:
    """Build journal preview Dr/Cr lines based on match type."""
    amount = abs(float(statement_line.get("amount", 0)))
    is_credit = bool(statement_line.get("is_credit", False))

    if bill_suggestion:
        return [
            {"dir": "Dr", "account": "Hutang Usaha", "amount": amount},
            {"dir": "Cr", "account": bank_account_name, "amount": amount},
        ]
    elif invoice_suggestion:
        return [
            {"dir": "Dr", "account": bank_account_name, "amount": amount},
            {"dir": "Cr", "account": "Piutang Usaha", "amount": amount},
        ]
    elif category_suggestion:
        cat_name = f"{category_suggestion.get('account_code', '')} {category_suggestion.get('account_name', '')}"
        if is_credit:
            return [
                {"dir": "Dr", "account": bank_account_name, "amount": amount},
                {"dir": "Cr", "account": cat_name, "amount": amount},
            ]
        else:
            return [
                {"dir": "Dr", "account": cat_name, "amount": amount},
                {"dir": "Cr", "account": bank_account_name, "amount": amount},
            ]

    return []


# ─── Tool Stage Labels (Thinking Indicator) ────────────────────────────────────


TOOL_STAGE_LABELS: dict[str, str] = {
    # === Kas & Bank ===
    "get_bank_accounts": "Mencari rekening bank",
    "get_bank_transactions": "Memeriksa mutasi bank",
    "get_bank_balance": "Memeriksa saldo",
    "get_bank_transactions": "Memeriksa mutasi bank",  # noqa: F601
    "get_bank_statement_sessions": "Memeriksa rekening koran",
    # === Jurnal & Ledger ===
    "get_journal_entries": "Memeriksa jurnal",
    "get_journal_lines": "Memeriksa buku besar",
    "get_journal_detail": "Membaca detail jurnal",
    # === Chart of Accounts ===
    "get_chart_of_accounts": "Memeriksa daftar akun",
    "get_account_detail": "Membaca detail akun",
    # === Faktur & Piutang ===
    "get_sales_invoices": "Memeriksa faktur penjualan",
    "get_sales_invoice_detail": "Membaca detail faktur",
    "get_customers": "Memeriksa data pelanggan",
    "get_customer_detail": "Membaca detail pelanggan",
    # === Bill & Hutang ===
    "get_bills": "Memeriksa tagihan",
    "get_bill_detail": "Membaca detail tagihan",
    "get_vendors": "Memeriksa data vendor",
    # === Produk & Inventori ===
    "get_products": "Memeriksa data produk",
    "get_top_products": "Menganalisis penjualan produk",
    "get_slow_moving_products": "Menganalisis produk lambat terjual",
    "get_product_margins": "Menganalisis margin produk",
    # === Rasio Keuangan ===
    "get_financial_ratios": "Menganalisis rasio keuangan",
    "get_ratio_dashboard": "Menyusun dashboard rasio",
    "get_ratio_trend": "Menganalisis tren rasio",
    "get_ratio_alerts": "Memeriksa alert keuangan",
    # === Budget ===
    "get_budgets": "Memeriksa daftar budget",
    "get_budget_detail": "Menganalisis budget vs aktual",
    # === Cost Center ===
    "get_cost_centers": "Memeriksa cost center",
    "get_cost_center_summary": "Menganalisis biaya departemen",
    # === Sprint 2: Cash & Payment Workflows ===
    "get_bank_transfers": "Memeriksa transfer bank",
    "get_bank_transfer_detail": "Melihat detail transfer bank",
    "get_bank_transfer_summary": "Meringkas transfer bank",
    "get_vendor_deposits": "Memeriksa uang muka vendor",
    "get_vendor_deposit_detail": "Melihat detail deposit vendor",
    "get_customer_deposits": "Memeriksa uang muka pelanggan",
    "get_customer_deposit_detail": "Melihat detail deposit pelanggan",
    "get_cheques": "Memeriksa daftar giro",
    # === Sprint 3: Recurring & Pipeline ===
    "get_recurring_invoices": "Memeriksa faktur berulang",
    "get_recurring_invoices_due": "Memeriksa faktur berulang jatuh tempo",
    "get_recurring_bills": "Memeriksa tagihan berulang",
    "get_recurring_bills_due": "Memeriksa tagihan berulang jatuh tempo",
    "get_sales_orders": "Memeriksa sales order",
    "get_sales_order_detail": "Melihat detail sales order",
    "get_quotes": "Memeriksa penawaran",
    # Sprint 4: Asset & Inventory Operations
    "get_fixed_assets": "Memeriksa aset tetap",
    "get_fixed_asset_detail": "Memeriksa detail aset",
    "get_stock_adjustments": "Memeriksa penyesuaian stok",
    "get_stock_adjustment_detail": "Memeriksa detail penyesuaian stok",
    "get_payroll_summary": "Memeriksa ringkasan penggajian",
    "get_product_detail": "Membaca detail produk",
    "get_warehouse_stock": "Memeriksa stok gudang",
    "search_items": "Mencari barang",
    "search_customers": "Mencari pelanggan",
    "search_vendors": "Mencari vendor",
    "search_accounts": "Mencari akun",
    "search_bank_accounts": "Mencari rekening bank",
    "get_customer_invoices": "Mencari faktur pelanggan",
    "get_vendor_bills": "Mencari tagihan vendor",
    # === Laporan ===
    "get_trial_balance": "Menyusun neraca saldo",
    "get_profit_loss": "Menyusun laporan laba rugi",
    "get_balance_sheet": "Menyusun neraca",
    "get_cashflow": "Menyusun laporan arus kas",
    # === Rekonsiliasi ===
    "get_reconciliation_workspace": "Memeriksa rekonsiliasi",
    "run_auto_match": "Mencocokkan transaksi",
    "review_next_unmatched": "Memeriksa transaksi belum cocok",
    # === Action tools ===
    "propose_action": "Menyiapkan transaksi",
    "propose_direct_action": "Menyiapkan data",
    "simulate_action": "Mensimulasikan dampak",
    "start_workflow": "Memulai proses",
    "cancel_workflow": "Membatalkan proses",
    # === Session tools ===
    "get_session_events": "Membaca riwayat sesi",
    "search_chat_history": "Mencari riwayat chat",
    # === Tutorial tools ===
    "get_tutorial": "Memuat tutorial",
    "list_tutorials": "Menampilkan daftar tutorial",
    "start_tutorial": "Memulai tutorial",
    "advance_tutorial": "Melanjutkan tutorial",
    "dismiss_tutorial": "Melewatkan tutorial",
    # === Fallback ===
    "_composing": "Menyusun jawaban",
    "_default": "Sedang berpikir",
}


def get_stage_label(tool_name: str) -> str:
    """Get Indonesian stage label for a tool call."""
    return TOOL_STAGE_LABELS.get(tool_name, TOOL_STAGE_LABELS["_default"])
