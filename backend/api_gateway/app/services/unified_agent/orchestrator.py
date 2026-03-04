"""
Unified Agent Orchestrator for MilkyHoop v3.

Single agent loop that replaces: intent classifier + action_planner + enrichment.
Pattern: identical to ragllm/orchestrator.py but with action tool support.

The agent loop:
1. Build messages (system prompt + conversation history + user text)
2. Call LLM with all tools (read + action)
3. If LLM calls tools → execute → feed results back → continue loop
4. If LLM returns text → final answer
5. Special: if propose_action returns ACTION_PREVIEW → exit immediately
"""

import asyncio
import json
import time
import logging
from datetime import date
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from uuid import uuid4

from ..llm import LLMRouter, TaskComplexity, LLMMessage, LLMResponse
from .system_prompt import build_system_prompt, get_intent_bias
from .tool_registry import get_tools
from .tool_executor import ToolExecutor, TenantContext, get_stage_label
from .tool_registry import is_tutorial_tool  # noqa: E402
from .model_router import ModelRouter
from .correlation import TurnContext

logger = logging.getLogger("unified_agent.orchestrator")

import time as _time  # noqa: E402
import json as _json_helpers  # noqa: E402


def _format_thinking_label(function_name: str, arguments_json: str = "") -> str:
    """Translate tool function name to user-facing Indonesian label."""
    try:
        args = _json_helpers.loads(arguments_json) if arguments_json else {}
    except Exception:
        args = {}

    LABEL_MAP = {
        "search_customers": "Mencari data pelanggan",
        "search_vendors": "Mencari data vendor",
        "search_items": "Mencari data produk",
        "search_accounts": "Mencari akun keuangan",
        "search_bank_accounts": "Mencari rekening bank",
        "get_customers": "Mengambil data pelanggan",
        "get_vendors": "Mengambil data vendor",
        "get_items": "Mengambil data produk",
        "get_customer_detail": "Melihat detail pelanggan",
        "get_vendor_detail": "Melihat detail vendor",
        "get_item_detail": "Melihat detail produk",
        "get_invoices": "Mengambil data faktur penjualan",
        "get_invoice_detail": "Melihat detail faktur",
        "get_bills": "Mengambil data tagihan",
        "get_bill_detail": "Melihat detail tagihan",
        "get_bank_accounts": "Mengambil data rekening bank",
        "get_bank_transactions": "Mengambil mutasi bank",
        "get_bank_transfer_summary": "Mengambil ringkasan transfer",
        "get_bank_transfers": "Mengambil data transfer bank",
        "get_chart_of_accounts": "Mengambil daftar akun",
        "get_expenses": "Mengambil data pengeluaran",
        "get_expense_detail": "Melihat detail pengeluaran",
        "get_receive_payments": "Mengambil data penerimaan pembayaran",
        "get_bill_payments": "Mengambil data pembayaran tagihan",
        "get_journal_entries": "Mengambil data jurnal",
        "get_general_ledger": "Mengambil buku besar",
        "get_customer_invoices": "Melihat faktur pelanggan",
        "get_vendor_bills": "Melihat tagihan vendor",
        "get_stock_adjustments": "Mengambil data penyesuaian stok",
        "get_stock_adjustment_detail": "Melihat detail penyesuaian stok",
        "get_dashboard_summary": "Mengambil ringkasan dashboard",
        "get_ar_aging": "Memeriksa piutang",
        "get_ap_aging": "Memeriksa hutang",
        "get_overdue_invoices": "Mengecek faktur jatuh tempo",
        "get_overdue_bills": "Mengecek tagihan jatuh tempo",
        "get_profit_loss": "Menghitung laba rugi",
        "get_balance_sheet": "Menyusun neraca",
        "get_cash_flow": "Menghitung arus kas",
        "get_trial_balance": "Menyusun neraca saldo",
        "get_top_expenses": "Menganalisis pengeluaran terbesar",
        "get_financial_ratios": "Menghitung rasio keuangan",
        "get_ratio_trend": "Menganalisis tren rasio",
        "get_ratio_alerts": "Mengecek peringatan keuangan",
        "get_ratio_dashboard": "Menyusun dashboard keuangan",
        "get_top_products": "Menganalisis produk terlaris",
        "get_slow_moving_products": "Menganalisis produk lambat",
        "get_product_margins": "Menghitung margin produk",
        "get_payroll_summary": "Mengambil ringkasan penggajian",
        "get_cash_balance": "Menghitung saldo kas",
        "get_kasbank_stats": "Menghitung saldo kas & bank",
        "get_budgets": "Mengambil data anggaran",
        "get_budget_detail": "Melihat detail anggaran",
        "get_cost_centers": "Mengambil data cost center",
        "get_cost_center_summary": "Melihat ringkasan cost center",
        "get_vendor_deposits": "Mengambil data uang muka vendor",
        "get_vendor_deposit_detail": "Melihat detail deposit vendor",
        "get_customer_deposits": "Mengambil data uang muka pelanggan",
        "get_customer_deposit_detail": "Melihat detail deposit pelanggan",
        "get_cheques": "Mengambil data giro",
        "get_recurring_invoices": "Mengambil faktur berulang",
        "get_recurring_invoices_due": "Mengecek faktur berulang jatuh tempo",
        "get_recurring_bills": "Mengambil tagihan berulang",
        "get_recurring_bills_due": "Mengecek tagihan berulang jatuh tempo",
        "get_sales_orders": "Mengambil data pesanan",
        "get_sales_order_detail": "Melihat detail pesanan",
        "get_quotes": "Mengambil data penawaran",
        "get_fixed_assets": "Mengambil data aset tetap",
        "get_fixed_asset_detail": "Melihat detail aset tetap",
        "get_accounting_periods": "Mengambil periode akuntansi",
        "get_bank_reconciliation": "Memeriksa rekonsiliasi bank",
        "get_credit_notes": "Mengambil data nota kredit",
        "get_purchase_orders": "Mengambil data purchase order",
        "propose_action": "Menyusun usulan transaksi",
        "simulate_action": "Menjalankan simulasi",
        "propose_direct_action": "Menyusun data konfirmasi",
        "execute_query": "Menjalankan laporan keuangan",
        "start_workflow": "Memulai proses",
        "cancel_workflow": "Membatalkan proses",
        "review_next_unmatched": "Memeriksa item belum cocok",
        "import_bank_statement": "Mengimpor rekening koran",
        "agentic_reconcile": "Mencocokkan transaksi otomatis",
        "confirm_single_match": "Mengonfirmasi pencocokan",
        "categorize_statement": "Mengkategorikan transaksi",
        "exclude_statement_line": "Melewati baris mutasi",
        "review_document": "Memeriksa dokumen",
        "get_session_events": "Membaca riwayat sesi",
        "update_session_state": "Menyimpan konteks",
        "search_chat_history": "Mencari riwayat chat",
    }

    label = LABEL_MAP.get(function_name, "Memproses permintaan")

    entity_name = (
        args.get("query")
        or args.get("search")
        or args.get("name")
        or args.get("q")
        or ""
    )
    if entity_name and isinstance(entity_name, str) and len(entity_name) < 50:
        label += f' "{entity_name}"'

    return label


def _get_thinking_badge(function_name: str, success: bool = True):
    """Return badge for financial verification steps. None for non-financial."""
    VERIFIED_TOOLS = {
        "propose_direct_action",
        "propose_action",
        "agentic_reconcile",
        "confirm_single_match",
        "categorize_statement",
        "start_workflow",
    }
    if function_name in VERIFIED_TOOLS and success:
        return {"type": "ok", "label": "Verified"}
    if not success:
        return {"type": "block", "label": "Gagal"}
    return None


# ─── Configuration ─────────────────────────────────────────────────────────────

MAX_ITERATIONS = 8  # Max agent loop iterations
MAX_TOOL_CALLS = 12  # Hard budget: total tool calls per request
MAX_PROPOSE_ATTEMPTS = 2  # Max propose_action retries
MAX_OUTPUT_TOKENS = 4000  # LLM output token limit

# Tools that MUST execute sequentially (state-mutating or order-dependent)
MUST_SEQUENTIAL_TOOLS = {
    "propose_action",
    "simulate_action",
    "propose_direct_action",  # ACTION tools
    "start_workflow",
    "review_next_unmatched",  # WORKFLOW tools
}
TEMPERATURE_DEFAULT = 0.3  # Deterministic for accounting
TEMPERATURE_CHAT = 0.5  # Slightly warmer for conversational


# ─── Response Types ────────────────────────────────────────────────────────────


@dataclass
class AgentResponse:
    """Unified response from the agent loop."""

    message_type: str  # TEXT, ACTION_PREVIEW, CLARIFICATION, VALIDATION_ERROR
    content: str = ""  # Text content (for TEXT/CLARIFICATION)
    pending_action_id: str = ""  # For ACTION_PREVIEW
    preview: Dict[str, Any] = field(default_factory=dict)  # For ACTION_PREVIEW
    expires_at: str = ""  # For ACTION_PREVIEW
    errors: List[Dict] = field(default_factory=list)  # For VALIDATION_ERROR
    # Telemetry
    iterations: int = 0
    tool_calls_made: List[Dict] = field(default_factory=list)
    model_used: str = ""
    total_latency_ms: int = 0
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    thinking_stages: List[str] = field(default_factory=list)  # Stage labels for UX
    usage: Dict[str, Any] = field(default_factory=dict)  # Token usage from LLM


class UnifiedAgent:
    """
    Single agent loop. Replaces: intent classifier + action_planner + enrichment.
    Pattern: identical to ragllm orchestrator (proven 12/12 tests).

    Uses LLM abstraction layer for provider-agnostic model calls.
    """

    def __init__(self):
        self.router = LLMRouter.from_env()

    async def process_message(
        self,
        user_text: str,
        context: TenantContext,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        image_content: Optional[list] = None,
        event_callback=None,
    ) -> AgentResponse:
        """
        Main entry point. Processes a user message through the agent loop.

        Args:
            user_text: The user's message
            context: Tenant context (auth, IDs)
            conversation_history: Previous messages [{role, content}, ...]

        Returns:
            AgentResponse with message_type, content, and optional preview
        """
        start_time = time.time()
        _process_start = _time.monotonic()

        async def emit(event_type: str, data: dict):
            if event_callback:
                try:
                    await event_callback(event_type, data)
                except Exception:
                    pass

        # --- Observability: create turn correlation context ---
        try:
            turn_ctx = TurnContext()
        except Exception:
            turn_ctx = None

        if not tool_executor:
            tool_executor = ToolExecutor(context, user_text=user_text)

        # Build messages
        system_prompt = build_system_prompt(
            tenant_name=context.tenant_name,
            today=date.today().isoformat(),
        )
        # Add soft intent bias
        system_prompt += get_intent_bias(user_text)

        messages: List[LLMMessage] = [
            LLMMessage(role="system", content=system_prompt),
        ]

        # Add conversation history (last 10 messages to stay within context)
        if conversation_history:
            for msg in conversation_history[-10:]:
                messages.append(
                    LLMMessage(
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                    )
                )

        # Vision: use content block array for THIS turn only (ephemeral)
        if image_content:
            messages.append(LLMMessage(role="user", content=image_content))
        else:
            messages.append(LLMMessage(role="user", content=user_text))

        # Agent loop state
        tool_calls_log: List[Dict] = []
        propose_count = 0
        has_proposed = False
        thinking_stages: List[str] = []

        # Model routing (M2) — determine tier based on message + state
        model_choice = ModelRouter.route(
            user_message=user_text,
            conversation_depth=len(conversation_history or []) // 2,
        )

        # Map model tier to existing TaskComplexity
        tier_to_complexity = {
            "flagship": TaskComplexity.ACTION,
            "reliable": TaskComplexity.SIMPLE_READ,
            "cheap": TaskComplexity.SIMPLE_READ,
        }
        complexity = tier_to_complexity.get(
            model_choice.tier, TaskComplexity.SIMPLE_READ
        )
        logger.info(
            f"[MODEL] tier={model_choice.tier} reason='{model_choice.reason}' -> complexity={complexity.value}"
        )

        # Get tools — skip for "cheap" tier (chitchat/greetings) to save ~9K tokens
        tools = [] if model_choice.tier == "cheap" else get_tools()

        # --- Observability: capture model choice ---
        try:
            if turn_ctx:
                turn_ctx.model_id_used = f"{model_choice.tier}"
        except Exception:
            pass

        client, current_model = self.router.get_client_and_model(complexity)

        accumulated_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        for iteration in range(MAX_ITERATIONS):
            # Budget guard
            if tool_executor.call_count >= MAX_TOOL_CALLS:
                await emit(
                    "THINKING_DONE",
                    {
                        "summary": "Terlalu banyak langkah",
                        "total_ms": int((_time.monotonic() - _process_start) * 1000),
                    },
                )
                return AgentResponse(
                    message_type="TEXT",
                    content="Maaf, permintaan ini terlalu kompleks untuk satu sesi. "
                    "Bisa dipecah jadi beberapa langkah?",
                    iterations=iteration,
                    tool_calls_made=tool_calls_log,
                    model_used=current_model,
                    total_latency_ms=int((time.time() - start_time) * 1000),
                    thinking_stages=thinking_stages + [get_stage_label("_composing")],
                    usage=accumulated_usage,
                )

            # Call LLM via abstraction layer
            try:
                llm_response: LLMResponse = await client.chat(
                    messages=messages,
                    tools=tools,
                    model=current_model,
                    temperature=TEMPERATURE_DEFAULT,
                    max_tokens=model_choice.max_tokens,
                )
            except Exception:
                logger.exception("LLM API call failed")
                await emit(
                    "THINKING_DONE",
                    {
                        "summary": "Kesalahan sistem",
                        "total_ms": int((_time.monotonic() - _process_start) * 1000),
                    },
                )
                return AgentResponse(
                    message_type="TEXT",
                    content="Maaf, terjadi kesalahan sistem. Coba lagi nanti.",
                    iterations=iteration,
                    tool_calls_made=tool_calls_log,
                    model_used=current_model,
                    total_latency_ms=int((time.time() - start_time) * 1000),
                    thinking_stages=thinking_stages + [get_stage_label("_composing")],
                    usage=accumulated_usage,
                )

            # Accumulate token usage across iterations
            if hasattr(llm_response, "usage") and llm_response.usage:
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    accumulated_usage[k] += llm_response.usage.get(k, 0)

            # Case 1: LLM returns text (no tool calls) → final answer
            if llm_response.finish_reason == "stop" or not llm_response.tool_calls:
                content = llm_response.content or ""

                # --- SSE: Emit THINKING_DONE ---
                _last_tool = tool_calls_log[-1]["name"] if tool_calls_log else ""
                await emit(
                    "THINKING_DONE",
                    {
                        "summary": _format_thinking_label(_last_tool)
                        if _last_tool
                        else "Menyusun jawaban",
                        "total_ms": int((_time.monotonic() - _process_start) * 1000),
                    },
                )

                # --- Observability: log turn context ---
                try:
                    if turn_ctx:
                        logger.info(
                            f"[OBSERVABILITY] turn_context={turn_ctx.to_log_dict()}"
                        )
                except Exception:
                    pass

                # Detect tutorial tool usage -> override message_type
                _final_msg_type = "TEXT"
                _tutorial_preview = None
                for _tc in tool_calls_log:
                    if is_tutorial_tool(_tc.get("name", "")):
                        _tc_data = _tc.get("data") or {}
                        if _tc_data.get("message_type") == "TUTORIAL_STEP":
                            _final_msg_type = "TUTORIAL_STEP"
                            _step = _tc_data.get("step") or {}
                            _tutorial_preview = {
                                "tutorial_key": _tc_data.get("tutorial_key", ""),
                                "step_key": _step.get("step_key", ""),
                                "step_index": _tc_data.get("current_step", 0),
                                "total_steps": _tc_data.get("total_steps", 0),
                                "content": content.strip(),
                                "linked_action": _step.get("linked_action"),
                                "skippable": _step.get("skippable", True),
                                "actions": [],
                            }
                            # Build action chips
                            if _step.get("linked_action"):
                                _tutorial_preview["actions"].append(
                                    {"key": "start_action", "label": "Mulai →"}
                                )
                            _tutorial_preview["actions"].append(
                                {"key": "continue", "label": "Lanjut"}
                            )
                            if _step.get("skippable", True):
                                _tutorial_preview["actions"].append(
                                    {"key": "skip", "label": "Lewati"}
                                )
                            break

                return AgentResponse(
                    message_type=_final_msg_type,
                    content=content.strip(),
                    preview=_tutorial_preview,
                    iterations=iteration + 1,
                    tool_calls_made=tool_calls_log,
                    model_used=current_model,
                    total_latency_ms=int((time.time() - start_time) * 1000),
                    thinking_stages=thinking_stages + [get_stage_label("_composing")],
                    usage=accumulated_usage,
                )

            # Case 2: LLM wants to call tools
            # Append assistant message (with raw tool_calls for provider threading)
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=llm_response.content,
                    tool_calls=llm_response.raw_message.get("tool_calls")
                    if llm_response.raw_message
                    else None,
                )
            )

            # SSE: Emit intermediate LLM text as paraphrase (shows instant response to user)
            if llm_response.content and llm_response.content.strip():
                await emit("TEXT_CHUNK", {"text": llm_response.content.strip()})

            # Execute tool calls — parallel for READ tools, sequential for ACTION/WORKFLOW
            all_tool_calls = llm_response.tool_calls
            parallel_tcs = [
                tc
                for tc in all_tool_calls
                if tc.function_name not in MUST_SEQUENTIAL_TOOLS
            ]
            sequential_tcs = [
                tc for tc in all_tool_calls if tc.function_name in MUST_SEQUENTIAL_TOOLS
            ]

            # --- Phase 1: Execute READ tools in parallel ---
            _chart_result = None  # Intercept CHART responses from parallel tools
            if parallel_tcs:
                par_start = time.time()

                # SSE: Emit "running" for all parallel tools before gather
                for _ptc in parallel_tcs:
                    _ptc_args_json = (
                        _json_helpers.dumps(_ptc.arguments, default=str)
                        if _ptc.arguments
                        else ""
                    )
                    await emit(
                        "THINKING_STEP",
                        {
                            "step_id": _ptc.id,
                            "text": _format_thinking_label(
                                _ptc.function_name, _ptc_args_json
                            ),
                            "status": "running",
                        },
                    )

                async def _exec_one(tc_item):
                    t0 = time.time()
                    res = await tool_executor.execute(
                        tc_item.function_name, tc_item.arguments
                    )
                    ms = int((time.time() - t0) * 1000)
                    return tc_item, res, ms

                par_results = await asyncio.gather(
                    *[_exec_one(tc) for tc in parallel_tcs],
                    return_exceptions=True,
                )
                par_ms = int((time.time() - par_start) * 1000)
                logger.info(
                    f"[PERF] parallel_tools={len(parallel_tcs)} total_ms={par_ms}"
                )

                for item in par_results:
                    if isinstance(item, Exception):
                        logger.exception("Parallel tool execution failed: %s", item)
                        continue
                    tc, result, call_ms = item

                    # Collect stage label for thinking indicator
                    thinking_stages.append(get_stage_label(tc.function_name))

                    # SSE: Emit "done" for parallel tool
                    _ptc_done_args = (
                        _json_helpers.dumps(tc.arguments, default=str)
                        if tc.arguments
                        else ""
                    )
                    await emit(
                        "THINKING_STEP",
                        {
                            "step_id": tc.id,
                            "text": _format_thinking_label(
                                tc.function_name, _ptc_done_args
                            ),
                            "status": "done",
                            "duration_ms": call_ms,
                            "badge": _get_thinking_badge(
                                tc.function_name, result.get("success", False)
                            ),
                        },
                    )

                    # Observability
                    try:
                        if turn_ctx:
                            tctx = turn_ctx.new_tool_call(
                                tc.function_name, retry_attempt=0
                            )
                            tc_status = "success" if result.get("success") else "failed"
                            tc_error = (
                                result.get("error_type")
                                if not result.get("success")
                                else None
                            )
                            tctx.complete(tc_status, error_type=tc_error)
                    except Exception:
                        pass

                    # For tutorial tools, store full result as data (it contains step info at top level)
                    _par_log_data = (
                        result
                        if (
                            is_tutorial_tool(tc.function_name) and result.get("success")
                        )
                        else result.get("data")
                    )
                    tool_calls_log.append(
                        {
                            "name": tc.function_name,
                            "args": tc.arguments,
                            "success": result.get("success", False),
                            "latency_ms": call_ms,
                            "data": _par_log_data,
                        }
                    )

                    # Intercept CHART result from parallel tools (execute_query with chart_ key)
                    if (
                        isinstance(result, dict)
                        and result.get("message_type") == "CHART"
                        and _chart_result is None
                    ):
                        _chart_result = result

                    messages.append(
                        LLMMessage(
                            role="tool",
                            tool_call_id=tc.id,
                            content=json.dumps(result, default=str),
                        )
                    )

            # --- Intercept: If parallel phase found a CHART, return immediately ---
            if _chart_result is not None:
                chart_data = _chart_result.get("data", {})
                await emit(
                    "THINKING_DONE",
                    {
                        "summary": "Membuat grafik",
                        "total_ms": int((_time.monotonic() - _process_start) * 1000),
                    },
                )
                return AgentResponse(
                    message_type="CHART",
                    content=_chart_result.get("content", ""),
                    preview=chart_data,
                    iterations=iteration + 1,
                    tool_calls_made=tool_calls_log,
                    model_used=current_model,
                    total_latency_ms=int((time.time() - start_time) * 1000),
                    thinking_stages=thinking_stages,
                    usage=accumulated_usage,
                )

            # --- Phase 2: Execute ACTION/WORKFLOW tools sequentially ---
            for tc in sequential_tcs:
                tool_name = tc.function_name
                tool_id = tc.id
                tool_args = tc.arguments

                # Propose budget guard
                if tool_name == "propose_action":
                    propose_count += 1
                    has_proposed = True
                    if propose_count > MAX_PROPOSE_ATTEMPTS:
                        result = {
                            "success": False,
                            "error": {
                                "code": "PROPOSE_BUDGET_EXCEEDED",
                                "message": "Sudah mencoba propose 2 kali. Tanya user untuk klarifikasi.",
                            },
                        }
                        messages.append(
                            LLMMessage(
                                role="tool",
                                tool_call_id=tool_id,
                                content=json.dumps(result),
                            )
                        )
                        tool_calls_log.append(
                            {
                                "name": tool_name,
                                "args": tool_args,
                                "result": "BUDGET_EXCEEDED",
                            }
                        )
                        continue

                # Execute tool
                call_start = time.time()
                _seq_tool_start = _time.monotonic()

                # SSE: Emit "running" for sequential tool
                _seq_args_json = (
                    _json_helpers.dumps(tool_args, default=str) if tool_args else ""
                )
                await emit(
                    "THINKING_STEP",
                    {
                        "step_id": tool_id,
                        "text": _format_thinking_label(tool_name, _seq_args_json),
                        "status": "running",
                    },
                )

                tool_call_ctx = None
                try:
                    if turn_ctx:
                        tool_call_ctx = turn_ctx.new_tool_call(
                            tool_name, retry_attempt=0
                        )
                except Exception:
                    pass

                result = await tool_executor.execute(tool_name, tool_args)
                call_ms = int((time.time() - call_start) * 1000)
                _seq_tool_duration = int((_time.monotonic() - _seq_tool_start) * 1000)

                # Collect stage label for thinking indicator
                thinking_stages.append(get_stage_label(tool_name))

                # SSE: Emit "done" for sequential tool
                await emit(
                    "THINKING_STEP",
                    {
                        "step_id": tool_id,
                        "text": _format_thinking_label(tool_name, _seq_args_json),
                        "status": "done",
                        "duration_ms": _seq_tool_duration,
                        "badge": _get_thinking_badge(
                            tool_name,
                            result.get("success", False)
                            if isinstance(result, dict)
                            else False,
                        ),
                    },
                )

                try:
                    if tool_call_ctx:
                        tc_status = "success" if result.get("success") else "failed"
                        tc_error = (
                            result.get("error_type")
                            if not result.get("success")
                            else None
                        )
                        tool_call_ctx.complete(tc_status, error_type=tc_error)
                except Exception:
                    pass

                # For tutorial tools, store full result as data (it contains step info at top level)
                _log_data = (
                    result
                    if (is_tutorial_tool(tool_name) and result.get("success"))
                    else result.get("data")
                )
                tool_calls_log.append(
                    {
                        "name": tool_name,
                        "args": tool_args,
                        "success": result.get("success", False),
                        "latency_ms": call_ms,
                        "data": _log_data,
                    }
                )

                # Special: propose_action returned ACTION_PREVIEW → exit immediately
                if (
                    tool_name == "propose_action"
                    and result.get("success")
                    and result.get("data", {}).get("status") == "ACTION_PREVIEW"
                ):
                    preview_data = result["data"]

                    # SSE: Emit THINKING_DONE for ACTION_PREVIEW
                    await emit(
                        "THINKING_DONE",
                        {
                            "summary": "Menyiapkan transaksi",
                            "total_ms": int(
                                (_time.monotonic() - _process_start) * 1000
                            ),
                        },
                    )

                    try:
                        if turn_ctx:
                            logger.info(
                                f"[OBSERVABILITY] turn_context={turn_ctx.to_log_dict()}"
                            )
                    except Exception:
                        pass

                    return AgentResponse(
                        message_type="ACTION_PREVIEW",
                        pending_action_id=preview_data.get("pending_action_id", ""),
                        preview=preview_data.get("preview", {}),
                        expires_at=preview_data.get("expires_at", ""),
                        iterations=iteration + 1,
                        tool_calls_made=tool_calls_log,
                        model_used=current_model,
                        total_latency_ms=int((time.time() - start_time) * 1000),
                        thinking_stages=thinking_stages,
                        usage=accumulated_usage,
                    )

                # Special: any tool returned DIRECT_ACTION_PREVIEW → exit immediately
                if (
                    isinstance(result, dict)
                    and result.get("message_type") == "DIRECT_ACTION_PREVIEW"
                ):
                    direct_data = result.get("data", {})

                    # SSE: Emit THINKING_DONE for DIRECT_ACTION_PREVIEW
                    await emit(
                        "THINKING_DONE",
                        {
                            "summary": "Menyiapkan data konfirmasi",
                            "total_ms": int(
                                (_time.monotonic() - _process_start) * 1000
                            ),
                        },
                    )

                    try:
                        if turn_ctx:
                            logger.info(
                                f"[OBSERVABILITY] turn_context={turn_ctx.to_log_dict()}"
                            )
                    except Exception:
                        pass

                    return AgentResponse(
                        message_type="DIRECT_ACTION_PREVIEW",
                        content=result.get("content", ""),
                        pending_action_id=direct_data.get("pending_action_id", ""),
                        preview=direct_data,
                        expires_at=direct_data.get("expires_at", ""),
                        iterations=iteration + 1,
                        tool_calls_made=tool_calls_log,
                        model_used=current_model,
                        total_latency_ms=int((time.time() - start_time) * 1000),
                        thinking_stages=thinking_stages,
                        usage=accumulated_usage,
                    )

                # Special: any tool returned CHART -> exit immediately (read-only visualization)
                if isinstance(result, dict) and result.get("message_type") == "CHART":
                    chart_data = result.get("data", {})

                    await emit(
                        "THINKING_DONE",
                        {
                            "summary": "Membuat grafik",
                            "total_ms": int(
                                (_time.monotonic() - _process_start) * 1000
                            ),
                        },
                    )

                    try:
                        if turn_ctx:
                            logger.info(
                                f"[OBSERVABILITY] turn_context={turn_ctx.to_log_dict()}"
                            )
                    except Exception:
                        pass

                    return AgentResponse(
                        message_type="CHART",
                        content=result.get("content", ""),
                        preview=chart_data,
                        iterations=iteration + 1,
                        tool_calls_made=tool_calls_log,
                        model_used=current_model,
                        total_latency_ms=int((time.time() - start_time) * 1000),
                        thinking_stages=thinking_stages,
                        usage=accumulated_usage,
                    )

                # Feed tool result back to LLM
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=tool_id,
                        content=json.dumps(result, default=str),
                    )
                )

            # Update model for next iteration (tiered routing via M2)
            if has_proposed and complexity != TaskComplexity.ACTION:
                complexity = TaskComplexity.ACTION
                logger.info("[MODEL] Upgraded to ACTION after propose_action")
            elif iteration > 2 and complexity != TaskComplexity.ACTION:
                complexity = TaskComplexity.SELF_CORRECT
                logger.info(
                    f"[MODEL] Upgraded to SELF_CORRECT at iteration {iteration + 1}"
                )
            client, current_model = self.router.get_client_and_model(complexity)

            # Continue loop — LLM sees tool results, decides next step

        # --- Observability: log turn context at end ---
        try:
            if turn_ctx:
                logger.info(f"[OBSERVABILITY] turn_context={turn_ctx.to_log_dict()}")
        except Exception:
            pass

        # Max iterations reached
        await emit(
            "THINKING_DONE",
            {
                "summary": "Terlalu banyak iterasi",
                "total_ms": int((_time.monotonic() - _process_start) * 1000),
            },
        )
        return AgentResponse(
            message_type="TEXT",
            content="Maaf, saya tidak bisa menyelesaikan permintaan ini. "
            "Bisa coba ulangi dengan informasi yang lebih spesifik?",
            iterations=MAX_ITERATIONS,
            tool_calls_made=tool_calls_log,
            model_used=current_model,
            total_latency_ms=int((time.time() - start_time) * 1000),
            thinking_stages=thinking_stages,
            usage=accumulated_usage,
        )

    # ─── Tiered Complexity Selection (legacy — kept for backward compat) ─────

    def _select_complexity(
        self, user_text: str, iteration: int, has_proposed: bool
    ) -> TaskComplexity:
        """
        Tiered model routing via TaskComplexity:
        - ACTION for action proposals (needs reasoning)
        - SELF_CORRECT for iteration > 2 (needs reasoning)
        - SIMPLE_READ for simple reads and insights (cheap model)
        """
        if has_proposed:
            return TaskComplexity.ACTION
        if iteration > 2:
            return TaskComplexity.SELF_CORRECT
        return TaskComplexity.SIMPLE_READ
