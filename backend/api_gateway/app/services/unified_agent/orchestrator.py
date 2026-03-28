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
from .system_prompt import build_system_prompt, build_system_messages, get_intent_bias, _infer_intent
from .intent_classifier import RouteResult  # classify_and_route removed (Final Cleanup)
from .tool_registry import get_tools, get_tools_for_domains, TOOL_DOMAINS
from .tool_executor import ToolExecutor, TenantContext, get_stage_label
from .tool_registry import is_tutorial_tool  # noqa: E402
from .model_router import ModelRouter
from .correlation import TurnContext

logger = logging.getLogger("unified_agent.orchestrator")

# ─── Phase 2A: Domain-Based Tool Loading ─────────────────────────────────────
# Maps intent_bias modes → tool domains.
# Uses get_intent_bias() return value to determine which tool domains to load.

# Broad fallback — when no signal words match
BROAD_FALLBACK_DOMAINS = {
    "CORE", "MASTER_DATA", "BANKING", "REPORTS", "ACTIONS",
}

def _resolve_domains_from_hint(hint_text: str) -> set:
    """Parse get_intent_bias() MODE hint and map to tool domains.

    DESIGN: Hints add MINIMAL domains — just the structural ones.
    Specific module domains (AR, AP, INVENTORY, etc.) come from
    _resolve_domains_from_text() based on actual user words.
    This keeps tool count low while text signals handle precision.
    """
    if not hint_text:
        return set()

    domains = set()
    ht = hint_text.upper()

    # Specific workflow modes — these need precise domain sets
    if "REKONSILIASI" in ht or "REKON" in ht:
        domains |= {"BANKING", "ACCOUNTING", "WORKFLOW"}
    if "FILE UPLOAD" in ht:
        domains |= {"BANKING", "WORKFLOW"}
    if "REVIEW DOKUMEN" in ht or "DOCUMENT" in ht:
        domains |= {"WORKFLOW"}
    if "RE-CONFIRM" in ht or "RECONFIRM" in ht:
        domains |= {"ACTIONS", "MASTER_DATA"}
    if "EDIT" in ht:
        domains |= {"ACTIONS", "MASTER_DATA"}
    if "TUTORIAL" in ht:
        pass  # Tutorial tools in CORE
    if "QUERY" in ht:
        pass  # execute_query in CORE, specific domains from text signals
    if "DIRECT ACTION" in ht:
        domains |= {"ACTIONS"}  # specific modules from text signals
    if "ACTION" in ht and "DIRECT" not in ht:
        domains |= {"ACTIONS"}  # specific modules from text signals
    if "ANALYSIS" in ht or "ANALISIS" in ht:
        domains |= {"REPORTS"}
    if "PLANNING" in ht:
        domains |= {"REPORTS"}
    if "BRAINSTORM" in ht:
        domains |= {"REPORTS"}
    # INSIGHT (default) → EMPTY — rely on text signals
    # This is intentional: INSIGHT is the catch-all default mode,
    # so adding domains here would defeat domain-based loading.

    return domains


def _resolve_domains_from_text(user_text: str) -> set:
    """Signal-word check on raw user text for domain routing.

    This is the PRIMARY domain selector. Hint-based selection (from MODE)
    only adds structural domains (ACTIONS, WORKFLOW, REPORTS).
    Text signals add the specific module domains (AR, AP, BANKING, etc.).
    """
    text_lower = user_text.lower()
    domains = set()

    # ── AR signals (piutang / customer side) ──
    ar_words = [
        "piutang", "receivable", "faktur jual", "sales invoice", "invoice",
        "pelanggan", "customer", "penjualan",
    ]
    if any(w in text_lower for w in ar_words):
        domains |= {"AR_INVOICES", "MASTER_DATA"}

    # ── AP signals (hutang / vendor side) ──
    ap_words = [
        "hutang", "payable", "tagihan", "bill", "vendor", "supplier",
        "pembelian", "faktur beli", "purchase",
    ]
    if any(w in text_lower for w in ap_words):
        domains |= {"AP_BILLS", "MASTER_DATA"}

    # ── Obligation words → BOTH AR and AP (ambiguous side) ──
    # "berapa yang belum dibayar si Budi?" — could be customer or vendor
    obligation_words = [
        "bayar", "dibayar", "belum bayar", "belum dibayar",
        "jatuh tempo", "overdue", "outstanding", "tunggakan",
        "lunas", "belum lunas", "sisa", "terutang",
    ]
    if any(w in text_lower for w in obligation_words):
        domains |= {"AR_INVOICES", "AP_BILLS", "MASTER_DATA"}

    # ── Banking signals ──
    bank_words = ["bank", "rekening", "saldo", "transfer", "mutasi", "kas"]
    if any(w in text_lower for w in bank_words):
        domains |= {"BANKING"}

    # ── Accounting signals ──
    acct_words = [
        "jurnal", "journal", "buku besar", "ledger",
        "neraca saldo", "trial balance", "akun", "coa",
    ]
    if any(w in text_lower for w in acct_words):
        domains |= {"ACCOUNTING"}

    # ── Report signals ──
    report_words = [
        "laba rugi", "profit", "neraca", "balance sheet",
        "arus kas", "cash flow", "laporan",
    ]
    if any(w in text_lower for w in report_words):
        domains |= {"REPORTS"}

    # ── Chart signals ──
    chart_words = ["grafik", "chart", "graph", "visualisasi", "diagram"]
    if any(w in text_lower for w in chart_words):
        domains |= {"CHARTS"}

    # ── Expense signals ──
    expense_words = ["biaya", "expense", "pengeluaran"]
    if any(w in text_lower for w in expense_words):
        domains |= {"EXPENSES"}

    # ── Inventory signals ──
    inv_words = [
        "stok", "stock", "persediaan", "inventory",
        "gudang", "warehouse", "barang",
    ]
    if any(w in text_lower for w in inv_words):
        domains |= {"INVENTORY", "MASTER_DATA"}

    # ── Action signals ──
    action_words = ["buat", "bikin", "create", "tambah", "catat", "void", "hapus"]
    if any(w in text_lower for w in action_words):
        domains |= {"ACTIONS"}

    # ── Workflow signals ──
    wf_words = ["rekonsiliasi", "rekon", "workflow"]
    if any(w in text_lower for w in wf_words):
        domains |= {"WORKFLOW"}

    # ── Pipeline signals ──
    pipe_words = [
        "deposit", "uang muka", "giro", "cheque", "recurring", "berulang",
        "sales order", "pesanan", "quote", "penawaran", "aset tetap", "fixed asset",
    ]
    if any(w in text_lower for w in pipe_words):
        domains |= {"PIPELINE"}

    # ── Analytics ──
    analytics_words = ["rasio", "ratio", "budget", "anggaran", "cost center", "payroll", "gaji"]
    if any(w in text_lower for w in analytics_words):
        domains |= {"ANALYTICS"}

    return domains


def resolve_domains(user_text: str, intent_hint: str) -> set:
    """Determine which tool domains to load for this turn.

    Uses BOTH:
    1. get_intent_bias() MODE hint (high-level category from system_prompt.py)
    2. Direct text signal words (catches specific domain keywords)

    Always includes CORE.
    Falls back to BROAD_FALLBACK_DOMAINS if nothing matches.
    """
    domains = {"CORE"}  # always

    # From intent bias MODE
    domains |= _resolve_domains_from_hint(intent_hint)

    # From raw text signals
    domains |= _resolve_domains_from_text(user_text)

    # Fallback: if only CORE, use broad set
    if domains == {"CORE"}:
        domains |= BROAD_FALLBACK_DOMAINS

    return domains



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


def _infer_tool_success(result) -> bool:
    """Infer success from tool result dict.

    Most tools don't return explicit 'success' key.
    Heuristic: if 'error' key exists and is truthy, it's a failure.
    Otherwise treat as success.
    """
    if not isinstance(result, dict):
        return False
    # Explicit success flag takes precedence
    if "success" in result:
        return bool(result["success"])
    # Error key means failure
    if result.get("error"):
        return False
    # No error = success
    return True


def _get_tool_category(function_name: str) -> str:
    """Map tool function name to UI category for icon rendering."""
    if function_name.startswith("search_"):
        return "search"
    if function_name.startswith("get_") or function_name.startswith("read_"):
        return "read"
    if function_name in ("propose_action", "propose_direct_action", "simulate_action"):
        return "write"
    if function_name in ("agentic_reconcile", "confirm_single_match", "categorize_statement"):
        return "verify"
    if function_name.startswith("execute_"):
        return "analyze"
    if function_name in ("start_workflow", "cancel_workflow"):
        return "workflow"
    return "general"


def _extract_result_meta(function_name: str, result) -> tuple:
    """Extract result count and label from tool result for UI display.
    Returns (count: int | None, label: str | None).
    """
    if not isinstance(result, dict):
        return None, None

    # Try common result list keys
    for key in ("results", "data", "bills", "invoices", "items", "entries",
                "transactions", "accounts", "vendors", "customers", "payments"):
        val = result.get(key)
        if isinstance(val, list) and len(val) > 0:
            count = len(val)
            # Build Indonesian label based on tool context
            label_map = {
                "bills": "tagihan",
                "invoices": "faktur",
                "items": "produk",
                "entries": "jurnal",
                "transactions": "transaksi",
                "accounts": "akun",
                "vendors": "vendor",
                "customers": "pelanggan",
                "payments": "pembayaran",
                "results": "hasil",
                "data": "data",
            }
            unit = label_map.get(key, "hasil")
            return count, f"{count} {unit}"

    return None, None


def _extract_sub_items(function_name: str, result) -> list | None:
    """Extract sub-items from tool result for frontend sub-item wrapper display.
    Returns list of {id, title, subtitle?, badge?} or None.
    Max 5 items to keep UI clean.
    """
    if not isinstance(result, dict):
        return None

    items = None
    badge_label = None

    # Search results: vendors, customers, items
    if function_name.startswith("search_"):
        for key in ("results", "data"):
            val = result.get(key)
            if isinstance(val, list) and val:
                items = val
                break
        if function_name == "search_vendors":
            badge_label = "VENDOR"
        elif function_name == "search_customers":
            badge_label = "PELANGGAN"
        elif function_name == "search_items":
            badge_label = "PRODUK"

    # Bill/invoice results
    elif function_name in ("get_vendor_bills", "get_customer_invoices"):
        items = result.get("results", [])
        badge_label = "TAGIHAN" if "vendor" in function_name else "FAKTUR"

    # Generic list results
    elif function_name.startswith("get_"):
        for key in ("results", "data", "entries", "transactions"):
            val = result.get(key)
            if isinstance(val, list) and val:
                items = val
                break

    if not items:
        return None

    sub_items = []
    for item in items[:5]:  # Max 5
        if not isinstance(item, dict):
            continue
        # Try to extract meaningful title
        title = (
            item.get("name")
            or item.get("number")
            or item.get("bill_number")
            or item.get("invoice_number")
            or item.get("journal_number")
            or item.get("display_name")
            or item.get("account_name")
            or str(item.get("id", ""))[:12]
        )
        # Try to extract subtitle
        subtitle = (
            item.get("amount_due")
            or item.get("total")
            or item.get("total_amount")
            or item.get("balance")
            or item.get("email")
            or item.get("phone")
            or item.get("vendor_name")
            or item.get("customer_name")
            or None
        )
        if subtitle is not None:
            subtitle = str(subtitle)
            # Format currency if it looks like a number
            try:
                num = float(subtitle)
                if num >= 1000:
                    subtitle = f"Rp {num:,.0f}".replace(",", ".")
            except (ValueError, TypeError):
                pass

        sub_items.append({
            "id": str(item.get("id", f"si-{len(sub_items)}")),
            "title": str(title),
            "subtitle": subtitle,
            "badge": badge_label,
        })

    return sub_items if sub_items else None


# ─── Configuration ─────────────────────────────────────────────────────────────

MAX_ITERATIONS = 8  # Max agent loop iterations
MAX_TOOL_CALLS = 12  # Hard budget: total tool calls per request
MAX_PROPOSE_ATTEMPTS = 2  # Max propose_action retries
MAX_OUTPUT_TOKENS = 4000  # LLM output token limit

# Per-intent iteration budgets (Final Cleanup: capped for token savings)
MAX_ITERATIONS_BY_INTENT = {
    "CHITCHAT": 1,
    "SIMPLE_READ": 3,
    "COMPLEX_READ": 3,
    "ACTION": 6,
    "CHART": 2,
    "RECON": 5,
    "FOLLOWUP": 3,
    "WORKFLOW_CONTINUE": 3,
}

# Tools that MUST execute sequentially (state-mutating or order-dependent)
MUST_SEQUENTIAL_TOOLS = {
    "propose_action",
    "simulate_action",
    "propose_direct_action",  # ACTION tools
    "update_document_context",  # Document context edit
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


# ─── Phase 3C: Conversation History Pruning ──────────────────────────────────
# Sliding window: keep last RECENT_WINDOW messages verbatim.
# Older messages: summarize with gpt-4o-mini and cache the summary.

RECENT_WINDOW = 4  # Keep last 4 messages verbatim
SUMMARIZE_THRESHOLD = 8000  # Estimate: trigger summarization above this
_CHARS_PER_TOKEN = 4  # Rough estimate for token counting

import hashlib as _hashlib


def _estimate_tokens(messages: list) -> int:
    """Rough token estimate: ~4 chars per token for Indonesian text."""
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return total_chars // _CHARS_PER_TOKEN


async def _summarize_history(older_messages: list, llm_router) -> str:
    """Summarize older conversation messages using gpt-4o-mini.

    Returns a concise summary string (~200-300 tokens).
    """
    from ..llm import LLMMessage, TaskComplexity

    # Build messages for summarization
    history_text = ""
    for msg in older_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if content:
            history_text += f"{role}: {content[:500]}\n"

    summarize_prompt = (
        "Ringkas percakapan berikut menjadi summary singkat (maks 3 kalimat). "
        "Fokus pada: entity yang dibahas (customer/vendor/item), "
        "aksi yang sudah dilakukan, angka/nominal penting, dan konteks kunci.\n\n"
        f"{history_text}"
    )

    try:
        client, model = llm_router.get_client_and_model(TaskComplexity.SIMPLE_READ)
        resp = await client.chat(
            messages=[
                LLMMessage(role="system", content="Kamu adalah summarizer percakapan."),
                LLMMessage(role="user", content=summarize_prompt),
            ],
            model=model,
            temperature=0.1,
            max_tokens=300,
        )
        return resp.content or ""
    except Exception as e:
        logger.warning("[Phase3C] Summarization failed: %s", e)
        # Fallback: just take first message content
        return f"Konteks sebelumnya: {older_messages[0].get('content', '')[:200]}"


def _prune_history(
    conversation_history: list,
    cached_summary: str | None = None,
    cached_hash: str | None = None,
) -> tuple:
    """Decide if history needs pruning.

    Returns (needs_summary: bool, older_messages: list, recent_messages: list, older_hash: str)
    """
    if not conversation_history:
        return False, [], [], ""

    total_tokens = _estimate_tokens(conversation_history)

    if total_tokens <= SUMMARIZE_THRESHOLD:
        return False, [], conversation_history, ""

    # Split: keep last RECENT_WINDOW messages verbatim
    recent = conversation_history[-RECENT_WINDOW:]
    older = conversation_history[:-RECENT_WINDOW]

    if not older:
        return False, [], conversation_history, ""

    # Hash the older messages to check if cached summary is still valid
    older_text = "".join(m.get("content", "") for m in older)
    older_hash = _hashlib.md5(older_text.encode()).hexdigest()

    # If cached summary covers these exact older messages, reuse it
    if cached_summary and cached_hash == older_hash:
        return False, older, recent, older_hash  # No new summary needed

    return True, older, recent, older_hash


def _safe_num(val, decimals=0):
    """Convert string/Decimal to int or float for compact data."""
    try:
        f = float(val)
        if decimals == 0 and f == int(f):
            return int(f)
        return round(f, decimals)
    except (TypeError, ValueError):
        return 0

class UnifiedAgent:
    """
    Single agent loop. Replaces: intent classifier + action_planner + enrichment.
    Pattern: identical to ragllm orchestrator (proven 12/12 tests).

    Uses LLM abstraction layer for provider-agnostic model calls.
    """

    def __init__(self):
        self.router = LLMRouter.from_env()

    async def _handle_chitchat(
        self,
        user_text: str,
        context: TenantContext,
        route_result: "RouteResult",
    ) -> AgentResponse:
        """Phase 4B: CHITCHAT short-circuit.
        0 tools, IDENTITY_ONLY prompt, max_tokens=300, 1 iteration.
        Cost: ~500 tokens input + ~100 output = ~$0.0001.
        """
        start_time = time.time()
        from .system_prompt import build_system_messages as _build_sys

        sys_msgs = _build_sys(
            tenant_name=context.tenant_name,
            today=date.today().isoformat(),
            user_text=user_text,
            intent="CHITCHAT",
        )
        messages = [
            LLMMessage(role=msg["role"], content=msg["content"])
            for msg in sys_msgs
        ]
        messages.append(LLMMessage(role="user", content=user_text))

        client, model = self.router.route("chitchat")
        try:
            resp = await client.chat(
                messages=messages,
                tools=[],
                model=model,
                temperature=TEMPERATURE_CHAT,
                max_tokens=300,
            )
            content = (resp.content or "").strip()
            usage = {}
            if hasattr(resp, "usage") and resp.usage:
                usage = dict(resp.usage)
            # Inject classifier metrics into usage
            usage["classifier_tokens_in"] = route_result.classifier_tokens_in
            usage["classifier_tokens_out"] = route_result.classifier_tokens_out
            usage["classifier_latency_ms"] = route_result.classifier_latency_ms
            usage["classifier_intent"] = route_result.intent
            usage["classifier_confidence"] = route_result.confidence
            usage["classifier_skipped"] = route_result.classifier_skipped
        except Exception as e:
            logger.error("[CHITCHAT] LLM error: %s", e)
            content = "Halo! Ada yang bisa saya bantu dengan pembukuan hari ini?"
            usage = {
                "classifier_tokens_in": route_result.classifier_tokens_in,
                "classifier_tokens_out": route_result.classifier_tokens_out,
                "classifier_latency_ms": route_result.classifier_latency_ms,
                "classifier_intent": route_result.intent,
                "classifier_confidence": route_result.confidence,
                "classifier_skipped": route_result.classifier_skipped,
            }

        latency_ms = int((time.time() - start_time) * 1000)
        logger.warning(
            "[Phase4-CHITCHAT] SHORT-CIRCUIT user='%s' latency=%dms",
            user_text[:30], latency_ms,
        )

        return AgentResponse(
            message_type="TEXT",
            content=content,
            iterations=1,
            tool_calls_made=[],
            model_used=model,
            total_latency_ms=latency_ms,
            thinking_stages=[],
            usage=usage,
        )


    # ── Compiler Pipeline Handler ─────────────────────────────────────────────
    async def _handle_pipeline(
        self,
        user_text: str,
        context: TenantContext,
        extraction,
        conversation_history: list = None,
        tool_executor: "ToolExecutor" = None,
        event_callback=None,
    ) -> "AgentResponse":
        """
        Compiler pipeline: extract -> resolve -> validate -> build -> propose.
        1 LLM call already done (extraction). Rest is code.
        Output format IDENTICAL to existing DIRECT_ACTION_PREVIEW.
        """
        import time as _time
        start_time = _time.time()

        async def emit(event_type, data):
            if event_callback:
                try:
                    await event_callback(event_type, data)
                except Exception:
                    pass

        await emit("THINKING_STEP", {
            "step_id": "pipeline-resolve",
            "text": "Menyiapkan data",
            "status": "running",
            "category": "search",
        })

        # Resolve + Complete
        from .entity_resolver import EntityResolver
        from .db_utils import get_session_db_pool

        pool = await get_session_db_pool()
        resolver = EntityResolver(pool, context.tenant_id)

        # Get L2 state + entity graph
        memory_state = {}
        entity_graph = {}
        if tool_executor and tool_executor.session_manager and tool_executor.session_id:
            try:
                state = await tool_executor.session_manager.get_state(tool_executor.session_id)
                memory_state = {
                    "active_customer_id": getattr(state, "active_customer_id", None),
                    "active_customer_name": getattr(state, "active_customer_name", None),
                    "active_vendor_id": getattr(state, "active_vendor_id", None),
                    "active_vendor_name": getattr(state, "active_vendor_name", None),
                    "active_invoice_id": getattr(state, "active_invoice_id", None),
                    "active_invoice_number": getattr(state, "active_invoice_number", None),
                    "active_bill_id": getattr(state, "active_bill_id", None),
                    "active_bill_number": getattr(state, "active_bill_number", None),
                }
                entity_graph = getattr(state, "entity_graph", {}) or {}
            except Exception as e:
                logger.warning("[PIPELINE] Failed to get L2 state: %s", e)

        # Action Memory: check for pattern suggestion
        action_memory_suggestion = None
        if tool_executor and tool_executor.session_manager:
            try:
                from .action_memory import ActionMemory
                am = ActionMemory(pool, context.tenant_id, getattr(context, "user_id", ""))
                action_memory_suggestion = await am.suggest_pattern(
                    extraction.intent, {**extraction.entities, **memory_state}
                )
                if action_memory_suggestion:
                    logger.warning(
                        "[PIPELINE] Action memory suggestion: confidence=%.2f usage=%d",
                        action_memory_suggestion["confidence"],
                        action_memory_suggestion["usage_count"],
                    )
            except Exception as e:
                logger.warning("[PIPELINE] Action memory lookup failed: %s", e)

        merged_entities = dict(extraction.entities)

        # ── Stage 2: Registry-driven field extraction ──
        # If intent has registry fields not in Stage 1 schema, extract them.
        # This fires an additional cheap LLM call (~300ms, ~100 tokens).
        from .direct_action_registry import get_direct_action as _s2_get_config
        from .entity_extractor import EXTRACTION_SCHEMAS as _S1_SCHEMAS
        _da_config = _s2_get_config(extraction.intent)
        if _da_config and _da_config.fields and not extraction.intent.startswith("query_"):
            _stage1_fields = set(_S1_SCHEMAS["general"]["json_schema"]["schema"]["properties"]["entities"]["properties"].keys())
            _registry_fields = {f.name for f in _da_config.fields if not f.hidden and not f.display_only}
            _fields_only_in_registry = _registry_fields - _stage1_fields

            if _fields_only_in_registry:
                from .entity_extractor import FieldExtractor
                _s2_client, _s2_model = self.router.route("field_extract")
                _field_extractor = FieldExtractor(_s2_client, _s2_model)

                _s2_result = await _field_extractor.extract_fields(
                    user_text=user_text,
                    intent=extraction.intent,
                    collected=merged_entities,
                )

                if _s2_result:
                    for k, v in _s2_result.items():
                        if v is not None:
                            merged_entities[k] = v
                    extraction.entities = merged_entities
                    logger.warning(
                        "[PIPELINE] Stage 2 extracted: %s -> merged total: %s",
                        list(_s2_result.keys()),
                        list(merged_entities.keys()),
                    )

        resolution = await resolver.resolve_and_complete(
            intent=extraction.intent,
            entities=extraction.entities,
            modifiers=extraction.modifiers,
            memory_state=memory_state,
            system_defaults={"date": date.today().isoformat()},
            entity_graph=entity_graph,
            action_memory_suggestion=action_memory_suggestion,
        )

        # Update entity graph with resolved entities
        if tool_executor and tool_executor.session_manager and tool_executor.session_id:
            if resolution.resolved:
                try:
                    from .session_manager import StateUpdateHooks
                    await StateUpdateHooks.after_resolve(
                        tool_executor.session_manager,
                        tool_executor.session_id,
                        extraction.intent,
                        resolution.resolved,
                    )
                except Exception as e:
                    logger.warning("[PIPELINE] Graph update failed (non-fatal): %s", e)

        await emit("THINKING_STEP", {
            "step_id": "pipeline-resolve",
            "text": "Menyiapkan data",
            "status": "done",
            "duration_ms": int((_time.time() - start_time) * 1000),
            "category": "search",
        })

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ── WORKFLOW PRIMARY PATH: check active crud_form ──
        # If there is an active workflow, it is the SOLE mechanism.
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _active_crud_wf = None
        _wf_engine = None
        if tool_executor and tool_executor.session_id:
            try:
                from .workflow_engine import WorkflowEngine
                from .db_utils import get_session_db_pool as _wf_pool
                _wf_db = await _wf_pool()
                _wf_engine = WorkflowEngine(
                    _wf_db, context.tenant_id,
                    getattr(context, "user_id", ""),
                    getattr(context, "auth_token", ""),
                )
                _active_crud_wf = await _wf_engine.get_state(tool_executor.session_id, "crud_form")
                if _active_crud_wf and _active_crud_wf.status != "active":
                    _active_crud_wf = None
            except Exception as _wf_err:
                logger.warning("[PIPELINE] Workflow check failed (non-fatal): %s", _wf_err)

        if _active_crud_wf and _wf_engine:
            # ── ACTIVE WORKFLOW: this path handles EVERYTHING and RETURNs ──
            _wf_action_key = _active_crud_wf.data.get("action_key", "")
            _wf_intent = _active_crud_wf.data.get("intent", "")
            _wf_payload = _active_crud_wf.data.get("payload", {})

            # Check if user started a DIFFERENT action
            if (extraction.intent and extraction.intent not in ("ambiguous", "chitchat", "")
                    and _wf_intent and extraction.intent != _wf_intent
                    and extraction.confidence > 0.7):
                await _wf_engine.cancel(tool_executor.session_id, "crud_form")
                logger.warning("[PIPELINE] Cancelled stale crud_form: was %s, now %s", _wf_intent, extraction.intent)
                # Fall through to normal flow below
            else:
                # ── CANDIDATE PICKING: resolve user's choice from saved candidates ──
                _wf_candidates = _active_crud_wf.data.get("candidates", [])
                _wf_phase = _active_crud_wf.data.get("phase", "")
                if _wf_candidates and _wf_phase == "picking_candidate":
                    _user_lower = user_text.strip().lower()
                    _matched_candidate = None

                    # Try number pick first ("1", "2", "yang pertama")
                    import re as _pick_re
                    _num_match = _pick_re.search(r'\b(\d+)\b', _user_lower)
                    if _num_match:
                        _pick_idx = int(_num_match.group(1)) - 1
                        if 0 <= _pick_idx < len(_wf_candidates):
                            _matched_candidate = _wf_candidates[_pick_idx]

                    # Try name substring match ("dewasa", "anak", "yang dewasa")
                    if not _matched_candidate:
                        # Check if any significant word from user text appears in candidate name
                        _skip_words = {"yang", "mau", "pilih", "nomor", "no", "item", "barang", "produk", "itu", "ini", "ya", "dong", "deh", "aja", "saja"}
                        _user_words = [w for w in _user_lower.split() if w not in _skip_words and len(w) > 1]
                        _best_match = None
                        _best_score = 0
                        for _cand in _wf_candidates:
                            _cand_lower = _cand.get("name", "").lower()
                            _score = sum(1 for w in _user_words if w in _cand_lower)
                            if _score > _best_score:
                                _best_score = _score
                                _best_match = _cand
                        if _best_match and _best_score > 0:
                            _matched_candidate = _best_match

                    if _matched_candidate:
                        logger.warning("[PIPELINE] Candidate picked: %s -> id=%s", _matched_candidate["name"], str(_matched_candidate["id"])[:8])
                        await _wf_engine.cancel(tool_executor.session_id, "crud_form")

                        # Directly execute update flow: fetch current data + show
                        _entity_type = _wf_action_key.replace("update_", "").replace("delete_", "")
                        _entity_id = _matched_candidate["id"]
                        _entity_name = _matched_candidate["name"]
                        _get_endpoints = {
                            "item": f"/api/items/{_entity_id}",
                            "customer": f"/api/customers/{_entity_id}",
                            "vendor": f"/api/vendors/{_entity_id}",
                            "warehouse": f"/api/warehouses/{_entity_id}",
                            "bank_account": f"/api/bank-accounts/{_entity_id}",
                        }
                        _get_ep = _get_endpoints.get(_entity_type)
                        _cur_data = None
                        if _get_ep:
                            try:
                                import httpx as _httpx
                                _auth = getattr(context, "auth_token", "") or ""
                                async with _httpx.AsyncClient(timeout=10.0) as _cl:
                                    _rr = await _cl.get(f"http://localhost:8000{_get_ep}", headers={"Authorization": f"Bearer {_auth}", "X-Tenant-ID": context.tenant_id})
                                    if _rr.status_code == 200:
                                        _raw = _rr.json()
                                        _cur_data = _raw.get("data", _raw) if isinstance(_raw, dict) else _raw
                            except Exception as _e:
                                logger.warning("[PIPELINE] Fetch after candidate pick failed: %s", _e)

                        if _cur_data:
                            _display = self._compact_current_data(_entity_type, _cur_data)
                            _real_name = _cur_data.get("nama_produk") or _cur_data.get("nama") or _cur_data.get("name") or _entity_name

                            # Create fresh workflow with resolved entity
                            try:
                                await _wf_engine.process(
                                    tool_executor.session_id, "crud_form",
                                    user_data={
                                        "action_key": _wf_action_key,
                                        "intent": _wf_action_key,
                                        "payload": {"id": _entity_id},
                                        "current_data": _display,
                                        "entity_name": _real_name,
                                        "phase": "showing_current",
                                    },
                                )
                            except Exception:
                                pass

                            _show = await self._polish_current_data(entity_name=_real_name, entity_type=_entity_type, current_data=_display)
                            await emit("THINKING_DONE", {"summary": "Data ditemukan", "total_ms": int((_time.time() - start_time) * 1000)})
                            return AgentResponse(
                                message_type="TEXT", content=_show,
                                iterations=1, model_used="gpt-4o-mini-2024-07-18",
                                total_latency_ms=int((_time.time() - start_time) * 1000),
                                thinking_stages=["Menganalisis pesan", "Mencari data"],
                            )
                        else:
                            # Fallback: couldn't fetch data, proceed with propose
                            merged_entities["id"] = _entity_id
                            merged_entities["name"] = _entity_name
                            extraction.entities = merged_entities

                    else:
                        # No match — ask again
                        _names = [c.get("name", "?") for c in _wf_candidates]
                        await emit("THINKING_DONE", {"summary": "Pilih salah satu", "total_ms": int((_time.time() - start_time) * 1000)})
                        return AgentResponse(
                            message_type="TEXT",
                            content=f"Maaf, saya tidak yakin yang mana. Pilih salah satu:\n" + "\n".join(f"{i+1}. {n}" for i, n in enumerate(_names)),
                            iterations=1, model_used="pipeline",
                            total_latency_ms=int((_time.time() - start_time) * 1000),
                        )

                # CONTINUATION: merge + process workflow
                if _wf_intent:
                    extraction.intent = _wf_intent

                # Merge: workflow payload (accumulated) + new extraction (new fields win)
                merged_for_wf = {**_wf_payload}
                for k, v in merged_entities.items():
                    if v is not None:
                        merged_for_wf[k] = v
                # Also merge resolution payload
                if resolution.payload:
                    for k, v in resolution.payload.items():
                        if v is not None and (k not in merged_for_wf or merged_for_wf.get(k) is None):
                            merged_for_wf[k] = v

                # Process workflow with merged payload
                _wf_result = await _wf_engine.process(
                    tool_executor.session_id, "crud_form",
                    user_data={"payload": merged_for_wf, "action_key": _wf_action_key, "intent": _wf_intent},
                )

                if _wf_result.new_state in ("PROPOSING", "COMPLETED") or _wf_result.completed:
                    # Gate passed: all fields present. Orchestrator handles propose directly.
                    _final_payload = merged_for_wf

                    # ── Entity ID resolution for update_*/delete_* ──
                    if (_wf_action_key.startswith("update_") or _wf_action_key.startswith("delete_")) and "id" not in _final_payload:
                        _WF_ENTITY_SEARCH = {
                            "update_item": ("item_name", "/api/items", "nama_produk"),
                            "update_customer": ("customer_name", "/api/customers", "nama"),
                            "update_vendor": ("vendor_name", "/api/vendors", "name"),
                            "update_bank_account": ("bank_name", "/api/bank-accounts", "account_name"),
                            "update_warehouse": ("warehouse_name", "/api/warehouses", "name"),
                            "delete_item": ("item_name", "/api/items", "nama_produk"),
                            "delete_customer": ("customer_name", "/api/customers", "nama"),
                            "delete_vendor": ("vendor_name", "/api/vendors", "name"),
                            "delete_bank_account": ("bank_name", "/api/bank-accounts", "account_name"),
                            "delete_warehouse": ("warehouse_name", "/api/warehouses", "name"),
                        }
                        _wf_s_cfg = _WF_ENTITY_SEARCH.get(_wf_action_key)
                        if _wf_s_cfg:
                            _wf_name_key, _wf_api_path, _wf_db_name_key = _wf_s_cfg
                            _wf_search = _final_payload.get(_wf_name_key) or _final_payload.get("name") or _final_payload.get("item_name") or ""
                            if _wf_search:
                                try:
                                    import httpx as _httpx
                                    _wf_auth = getattr(context, "auth_token", "") or ""
                                    _wf_headers = {"Authorization": f"Bearer {_wf_auth}"} if _wf_auth else {}
                                    async with _httpx.AsyncClient(base_url="http://localhost:8000", timeout=5.0) as _wf_hc:
                                        _wf_r = await _wf_hc.get(f"{_wf_api_path}?search={_wf_search}&limit=1", headers=_wf_headers)
                                        _wf_r.raise_for_status()
                                        _wf_d = _wf_r.json()
                                        _wf_list = _wf_d.get("items") or _wf_d.get("data") or (_wf_d if isinstance(_wf_d, list) else [])
                                        if _wf_list:
                                            _final_payload["id"] = _wf_list[0].get("id")
                                            logger.warning("[PIPELINE-WF] Entity resolved: %s -> id=%s", _wf_action_key, str(_final_payload["id"])[:8])
                                        else:
                                            await emit("THINKING_DONE", {"summary": "Tidak ditemukan", "total_ms": int((_time.time() - start_time) * 1000)})
                                            return AgentResponse(
                                                message_type="TEXT",
                                                content=f"Maaf, saya tidak menemukan {_wf_search}. Bisa cek kembali namanya?",
                                                iterations=1, model_used="pipeline",
                                                total_latency_ms=int((_time.time() - start_time) * 1000),
                                            )
                                except Exception as _wf_re:
                                    logger.warning("[PIPELINE-WF] Entity resolution failed: %s", _wf_re)

                    if _wf_result.auto_results and _wf_result.auto_results.get("propose_result"):
                        _propose_data = _wf_result.auto_results["propose_result"]
                    else:
                        # Normalize entity name field (Bug 3 fix)
                        if "name" not in _final_payload or not _final_payload.get("name"):
                            _final_payload["name"] = (
                                _final_payload.get("item_name") or
                                _final_payload.get("customer_name") or
                                _final_payload.get("vendor_name") or
                                _final_payload.get("entity_name") or
                                _active_crud_wf.data.get("entity_name", "") or
                                ""
                            )
                        _propose_data = await tool_executor._execute_propose_direct({
                            "action_key": _wf_action_key,
                            "payload": _final_payload,
                        })

                    if _propose_data.get("message_type") == "DIRECT_ACTION_PREVIEW":
                        _direct_data = _propose_data.get("data", {})

                        # Fire L2 + L3 hooks
                        if tool_executor.session_manager and tool_executor.session_id:
                            try:
                                from .session_manager import StateUpdateHooks
                                await StateUpdateHooks.after_propose(
                                    tool_executor.session_manager,
                                    tool_executor.session_id,
                                    extraction.intent.upper(),
                                    _final_payload,
                                    {"pending_action_id": _direct_data.get("pending_action_id")},
                                )
                            except Exception as _hook_err:
                                logger.warning("[PIPELINE] WF state hook failed: %s", _hook_err)

                        # Save pending for Edit flow
                        if tool_executor.session_manager:
                            try:
                                _save = {k: v for k, v in _final_payload.items() if v is not None}
                                await tool_executor.session_manager.update_state(
                                    tool_executor.session_id,
                                    pending_payload=_save,
                                    pending_intent=extraction.intent,
                                )
                            except Exception:
                                pass

                        # Cancel workflow (propose reached = done)
                        try:
                            await _wf_engine.cancel(tool_executor.session_id, "crud_form")
                        except Exception:
                            pass

                        await emit("THINKING_DONE", {
                            "summary": "Data siap dikonfirmasi",
                            "total_ms": int((_time.time() - start_time) * 1000),
                        })
                        return AgentResponse(
                            message_type="DIRECT_ACTION_PREVIEW",
                            content=_propose_data.get("content", ""),
                            pending_action_id=_direct_data.get("pending_action_id", ""),
                            preview=_direct_data,
                            expires_at=_direct_data.get("expires_at", ""),
                            iterations=1,
                            tool_calls_made=[
                                {"name": "entity_extractor", "args": {"intent": extraction.intent}, "success": True},
                                {"name": "propose_direct_action", "args": {"action_key": _wf_action_key}, "success": True},
                            ],
                            model_used="pipeline",
                            total_latency_ms=int((_time.time() - start_time) * 1000),
                            thinking_stages=["Menganalisis pesan", "Mencari data", "Menyiapkan konfirmasi"],
                        )
                    else:
                        # Propose failed
                        _error_msg = _propose_data.get("error", "")
                        _error_type = _propose_data.get("error_type", "")
                        if _error_type == "VALIDATION_ERROR":
                            _val_missing = _propose_data.get("missing_fields", [str(_error_msg)])
                            clarification_text = await self._natural_clarification(
                                intent=extraction.intent,
                                collected=merged_for_wf,
                                missing_labels=_val_missing,
                                resolution=resolution,
                            )
                            await emit("THINKING_DONE", {
                                "summary": "Butuh info tambahan",
                                "total_ms": int((_time.time() - start_time) * 1000),
                            })
                            return AgentResponse(
                                message_type="TEXT",
                                content=clarification_text,
                                iterations=1, tool_calls_made=[], model_used="pipeline",
                                total_latency_ms=int((_time.time() - start_time) * 1000),
                                thinking_stages=["Menganalisis pesan", "Validasi data"],
                            )
                        else:
                            if isinstance(_error_msg, dict):
                                _error_msg = _error_msg.get("message", str(_error_msg))
                            await emit("THINKING_DONE", {
                                "summary": "Terjadi error",
                                "total_ms": int((_time.time() - start_time) * 1000),
                            })
                            return AgentResponse(
                                message_type="TEXT",
                                content=str(_error_msg) if _error_msg else "Terjadi error saat menyiapkan data.",
                                iterations=1, tool_calls_made=[], model_used="pipeline",
                                total_latency_ms=int((_time.time() - start_time) * 1000),
                            )

                if _wf_result.new_state == "COLLECTING":
                    # Gate failed: still missing fields
                    _gate_instruction = _wf_result.llm_instruction or ""
                    if _gate_instruction:
                        clarification_text = await self._natural_clarification(
                            intent=extraction.intent,
                            collected=merged_for_wf,
                            missing_labels=[],
                            resolution=resolution,
                            override_instruction=_gate_instruction,
                        )
                    else:
                        clarification_text = await self._natural_clarification(
                            intent=extraction.intent,
                            collected=merged_for_wf,
                            missing_labels=resolution.clarifications if resolution.needs_clarification else [],
                            resolution=resolution,
                        )

                    await emit("THINKING_DONE", {
                        "summary": "Butuh info tambahan",
                        "total_ms": int((_time.time() - start_time) * 1000),
                    })
                    return AgentResponse(
                        message_type="TEXT",
                        content=clarification_text,
                        iterations=1, tool_calls_made=[], model_used="pipeline",
                        total_latency_ms=int((_time.time() - start_time) * 1000),
                        thinking_stages=["Menganalisis pesan", "Mencari data"],
                    )

                # Unexpected workflow state: log and fall through
                logger.warning("[PIPELINE] Unexpected workflow state: %s", _wf_result.new_state)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ── NO active workflow: original flow ──
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

                # Clarification needed? -> Natural LLM-driven question + save pending
        if resolution.needs_clarification and not extraction.intent.startswith("update_") and not extraction.intent.startswith("delete_"):
            # Save partial payload for next turn
            save_payload = {k: v for k, v in merged_entities.items() if v is not None}
            if resolution.payload:
                for k, v in resolution.payload.items():
                    if v is not None and k not in save_payload:
                        save_payload[k] = v

            if tool_executor and tool_executor.session_manager and tool_executor.session_id:
                try:
                    await tool_executor.session_manager.update_state(
                        tool_executor.session_id,
                        pending_payload=save_payload,
                        pending_intent=extraction.intent,
                    )
                    logger.warning(
                        "[PIPELINE] Saved pending: intent=%s keys=%s",
                        extraction.intent, list(save_payload.keys()),
                    )
                except Exception as e:
                    logger.warning("[PIPELINE] Save pending failed: %s", e)

            # ── Create crud_form workflow for multi-turn tracking ──
            if _wf_engine and tool_executor and tool_executor.session_id:
                try:
                    await _wf_engine.process(
                        tool_executor.session_id, "crud_form",
                        user_data={
                            "action_key": extraction.intent,
                            "payload": save_payload,
                            "intent": extraction.intent,
                        },
                    )
                    logger.warning("[PIPELINE] Created crud_form workflow: intent=%s", extraction.intent)
                except Exception as _wf_err2:
                    logger.warning("[PIPELINE] Workflow create failed (non-fatal): %s", _wf_err2)
            elif tool_executor and tool_executor.session_id and not _wf_engine:
                try:
                    from .workflow_engine import WorkflowEngine
                    from .db_utils import get_session_db_pool as _wf_pool2
                    _wf_db2 = await _wf_pool2()
                    _wf_engine_new = WorkflowEngine(
                        _wf_db2, context.tenant_id,
                        getattr(context, "user_id", ""),
                        getattr(context, "auth_token", ""),
                    )
                    await _wf_engine_new.process(
                        tool_executor.session_id, "crud_form",
                        user_data={
                            "action_key": extraction.intent,
                            "payload": save_payload,
                            "intent": extraction.intent,
                        },
                    )
                    logger.warning("[PIPELINE] Created crud_form workflow (new engine): intent=%s", extraction.intent)
                except Exception as _wf_err2:
                    logger.warning("[PIPELINE] Workflow create failed (non-fatal): %s", _wf_err2)

            # Build natural clarification via LLM
            clarification_text = await self._natural_clarification(
                intent=extraction.intent,
                collected=merged_entities,
                missing_labels=resolution.clarifications,
                resolution=resolution,
            )

            await emit("THINKING_DONE", {
                "summary": "Butuh info tambahan",
                "total_ms": int((_time.time() - start_time) * 1000),
            })
            return AgentResponse(
                message_type="TEXT",
                content=clarification_text,
                iterations=1,
                tool_calls_made=[{
                    "name": "entity_extractor",
                    "args": {"user_text": user_text},
                    "success": True,
                    "latency_ms": int((_time.time() - start_time) * 1000),
                }],
                model_used="gpt-4o-mini-2024-07-18",
                total_latency_ms=int((_time.time() - start_time) * 1000),
                thinking_stages=["Menganalisis pesan", "Mencari data"],
            )

        # Propose via existing _execute_propose_direct
        await emit("THINKING_STEP", {
            "step_id": "pipeline-propose",
            "text": "Menyiapkan konfirmasi",
            "status": "running",
            "category": "write",
        })

        # ── Entity Resolution for update_*/delete_* intents ──
        if (extraction.intent.startswith("update_") or extraction.intent.startswith("delete_")) and ("id" not in merged_entities or len(str(merged_entities.get("id",""))) < 30):
            _ENTITY_SEARCH = {
                "update_item": ("item_name", "/api/items", "nama_produk"),
                "update_customer": ("customer_name", "/api/customers", "nama"),
                "update_vendor": ("vendor_name", "/api/vendors", "name"),
                "update_bank_account": ("bank_name", "/api/bank-accounts", "account_name"),
                "update_warehouse": ("warehouse_name", "/api/warehouses", "name"),
                "delete_item": ("item_name", "/api/items", "nama_produk"),
                "delete_customer": ("customer_name", "/api/customers", "nama"),
                "delete_vendor": ("vendor_name", "/api/vendors", "name"),
                "delete_bank_account": ("bank_name", "/api/bank-accounts", "account_name"),
                "delete_warehouse": ("warehouse_name", "/api/warehouses", "name"),
            }
            _s_cfg = _ENTITY_SEARCH.get(extraction.intent)
            if _s_cfg:
                _name_key, _api_path, _db_name_key = _s_cfg
                _search_name = merged_entities.get(_name_key) or merged_entities.get("name") or merged_entities.get("item_name") or ""
                if _search_name:
                    try:
                        import httpx as _httpx
                        _auth = getattr(context, "auth_token", "") or ""
                        _headers = {"Authorization": f"Bearer {_auth}"} if _auth else {}
                        async with _httpx.AsyncClient(base_url="http://localhost:8000", timeout=5.0) as _hc:
                            _r = await _hc.get(f"{_api_path}?search={_search_name}&limit=5", headers=_headers)
                            _r.raise_for_status()
                            _d = _r.json()
                            _list = _d.get("items") or _d.get("data") or (_d if isinstance(_d, list) else [])
                            if len(_list) == 1:
                                _found = _list[0]
                                merged_entities["id"] = _found.get("id")
                                merged_entities.setdefault("name", _found.get(_db_name_key) or _found.get("name") or _found.get("nama_produk") or _search_name)
                                extraction.entities = merged_entities
                                logger.warning("[PIPELINE] Entity resolved: %s -> id=%s", extraction.intent, str(merged_entities["id"])[:8])
                            elif len(_list) > 1:
                                # Multiple matches — save candidates + ask user to pick
                                _candidates_display = []
                                _candidates_data = []
                                for _ci, _c in enumerate(_list, 1):
                                    _cname = _c.get(_db_name_key) or _c.get("name") or _c.get("nama_produk") or _c.get("nama") or "?"
                                    _cdetail = ""
                                    if _c.get("item_code"):
                                        _cdetail += f" ({_c['item_code']})"
                                    if _c.get("item_type"):
                                        _cdetail += f" — {_c['item_type']}"
                                    _candidates_display.append(f"{_ci}. {_cname}{_cdetail}")
                                    _candidates_data.append({"id": _c.get("id"), "name": _cname, "code": _c.get("item_code", "")})
                                _pick_text = f"Ada {len(_list)} hasil untuk \"{_search_name}\":\n" + "\n".join(_candidates_display) + "\n\nYang mana yang kamu maksud?"

                                # Save candidates in workflow for next turn resolution
                                if _wf_engine and tool_executor and tool_executor.session_id:
                                    try:
                                        await _wf_engine.process(
                                            tool_executor.session_id, "crud_form",
                                            user_data={
                                                "action_key": extraction.intent,
                                                "intent": extraction.intent,
                                                "payload": {},
                                                "candidates": _candidates_data,
                                                "phase": "picking_candidate",
                                            },
                                        )
                                        logger.warning("[PIPELINE] Saved %d candidates in workflow", len(_candidates_data))
                                    except Exception as _wf_e:
                                        logger.warning("[PIPELINE] Save candidates failed: %s", _wf_e)

                                await emit("THINKING_DONE", {"summary": "Beberapa ditemukan", "total_ms": int((_time.time() - start_time) * 1000)})
                                return AgentResponse(
                                    message_type="TEXT",
                                    content=_pick_text,
                                    iterations=1, model_used="pipeline",
                                    total_latency_ms=int((_time.time() - start_time) * 1000),
                                    thinking_stages=["Menganalisis pesan", "Mencari data"],
                                )
                            else:
                                await emit("THINKING_DONE", {"summary": "Tidak ditemukan", "total_ms": int((_time.time() - start_time) * 1000)})
                                return AgentResponse(
                                    message_type="TEXT",
                                    content=f"Maaf, saya tidak menemukan \'{_search_name}\'. Bisa cek kembali namanya?",
                                    iterations=1, model_used="pipeline",
                                    total_latency_ms=int((_time.time() - start_time) * 1000),
                                )
                    except Exception as _re:
                        logger.warning("[PIPELINE] Entity resolution failed: %s", _re)


        # ── UPDATE FLOW: Show current data + ask what to change ──
        if extraction.intent.startswith("update_") and not _active_crud_wf:
            _entity_type = extraction.intent.replace("update_", "")
            _entity_id = merged_entities.get("id") or (resolution.payload or {}).get("id")

            if _entity_id:
                _get_endpoints = {
                    "item": f"/api/items/{_entity_id}",
                    "customer": f"/api/customers/{_entity_id}",
                    "vendor": f"/api/vendors/{_entity_id}",
                    "warehouse": f"/api/warehouses/{_entity_id}",
                    "bank_account": f"/api/bank-accounts/{_entity_id}",
                }
                _get_endpoint = _get_endpoints.get(_entity_type)

                if _get_endpoint:
                    _current_data = None
                    try:
                        import httpx as _httpx
                        _auth = getattr(context, "auth_token", "") or ""
                        async with _httpx.AsyncClient(timeout=10.0) as _client:
                            _resp = await _client.get(
                                f"http://localhost:8000{_get_endpoint}",
                                headers={
                                    "Authorization": f"Bearer {_auth}",
                                    "Content-Type": "application/json",
                                    "X-Tenant-ID": context.tenant_id,
                                },
                            )
                            if _resp.status_code == 200:
                                _rj = _resp.json()
                                _current_data = _rj.get("data", _rj) if isinstance(_rj, dict) else _rj
                    except Exception as _e:
                        logger.warning("[PIPELINE] Fetch current data failed: %s", _e)

                    if _current_data:
                        _display_data = self._compact_current_data(_entity_type, _current_data)
                        _entity_name = (
                            _current_data.get("name") or
                            _current_data.get("nama_produk") or
                            _current_data.get("nama") or
                            _current_data.get("account_name") or
                            "item"
                        )

                        # ── FAST PATH: if user already provided field changes, skip "mau ubah?" ──
                        _id_fields = {"id", "item_name", "customer_name", "vendor_name", "warehouse_name", "bank_name", "name", "date", "entity_name"}
                        _change_fields = {k: v for k, v in merged_entities.items() if k not in _id_fields and v is not None and v != ""}
                        if _change_fields:
                            # User said "edit X, harga jual 43000" — go straight to propose
                            _fast_payload = {"id": _entity_id, "name": _entity_name, **_change_fields}
                            logger.warning("[PIPELINE] Update fast path: %s changes=%s", extraction.intent, list(_change_fields.keys()))

                            # Normalize name
                            if "name" not in _fast_payload or not _fast_payload.get("name"):
                                _fast_payload["name"] = _entity_name

                            propose_result = await tool_executor._execute_propose_direct({
                                "action_key": extraction.intent,
                                "payload": _fast_payload,
                            })

                            if propose_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
                                _direct_data = propose_result.get("data", {})
                                await emit("THINKING_DONE", {
                                    "summary": "Data siap dikonfirmasi",
                                    "total_ms": int((_time.time() - start_time) * 1000),
                                })
                                return AgentResponse(
                                    message_type="DIRECT_ACTION_PREVIEW",
                                    content=propose_result.get("content", ""),
                                    pending_action_id=_direct_data.get("pending_action_id", ""),
                                    preview=_direct_data,
                                    expires_at=_direct_data.get("expires_at", ""),
                                    iterations=1,
                                    tool_calls_made=[],
                                    model_used="pipeline",
                                    total_latency_ms=int((_time.time() - start_time) * 1000),
                                    thinking_stages=["Menganalisis pesan", "Mencari data", "Menyiapkan konfirmasi"],
                                )

                        # Create workflow with current data
                        if _wf_engine and tool_executor and tool_executor.session_id:
                            try:
                                await _wf_engine.process(
                                    tool_executor.session_id, "crud_form",
                                    user_data={
                                        "action_key": extraction.intent,
                                        "payload": {"id": _entity_id},
                                        "intent": extraction.intent,
                                        "current_data": _display_data,
                                        "entity_name": _entity_name,
                                        "phase": "showing_current",
                                    },
                                )
                                logger.warning("[PIPELINE] Created update workflow: %s phase=showing_current", extraction.intent)
                            except Exception as _e:
                                logger.warning("[PIPELINE] Create update workflow failed: %s", _e)

                        # Also save pending state for session continuity
                        if tool_executor and tool_executor.session_manager and tool_executor.session_id:
                            try:
                                await tool_executor.session_manager.update_state(
                                    tool_executor.session_id,
                                    pending_payload={"id": _entity_id},
                                    pending_intent=extraction.intent,
                                )
                            except Exception:
                                pass

                        _show_text = await self._polish_current_data(
                            entity_name=_entity_name,
                            entity_type=_entity_type,
                            current_data=_display_data,
                        )

                        await emit("THINKING_DONE", {
                            "summary": "Data ditemukan",
                            "total_ms": int((_time.time() - start_time) * 1000),
                        })

                        return AgentResponse(
                            message_type="TEXT",
                            content=_show_text,
                            iterations=1,
                            model_used="gpt-4o-mini-2024-07-18",
                            total_latency_ms=int((_time.time() - start_time) * 1000),
                            thinking_stages=["Menganalisis pesan", "Mencari data"],
                        )
            else:
                # No entity ID resolved — item not found
                _search_name = merged_entities.get("item_name") or merged_entities.get("name") or ""
                await emit("THINKING_DONE", {"summary": "Tidak ditemukan", "total_ms": int((_time.time() - start_time) * 1000)})
                return AgentResponse(
                    message_type="TEXT",
                    content=f"Maaf, saya tidak menemukan '{_search_name}'. Bisa cek kembali namanya?",
                    iterations=1, model_used="pipeline",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Normalize entity name field for propose (Bug 3 fix)
        _propose_payload = {**resolution.payload, **{k: v for k, v in merged_entities.items() if v is not None}}
        if "name" not in _propose_payload or not _propose_payload.get("name"):
            _propose_payload["name"] = (
                _propose_payload.get("item_name") or
                _propose_payload.get("customer_name") or
                _propose_payload.get("vendor_name") or
                _propose_payload.get("warehouse_name") or
                _propose_payload.get("bank_name") or
                _propose_payload.get("account_name") or
                ""
            )

        propose_result = await tool_executor._execute_propose_direct({
            "action_key": extraction.intent,
            "payload": _propose_payload,
        })

        await emit("THINKING_STEP", {
            "step_id": "pipeline-propose",
            "text": "Menyiapkan konfirmasi",
            "status": "done",
            "duration_ms": int((_time.time() - start_time) * 1000),
            "category": "write",
        })

        # Return in existing format
        if propose_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
            direct_data = propose_result.get("data", {})
            await emit("THINKING_DONE", {
                "summary": "Data siap dikonfirmasi",
                "total_ms": int((_time.time() - start_time) * 1000),
            })

            # Fire L2 + L3 hooks
            if tool_executor and tool_executor.session_manager and tool_executor.session_id:
                try:
                    from .session_manager import StateUpdateHooks
                    await StateUpdateHooks.after_propose(
                        tool_executor.session_manager,
                        tool_executor.session_id,
                        extraction.intent.upper(),
                        resolution.payload,
                        {"pending_action_id": direct_data.get("pending_action_id")},
                    )
                except Exception as e:
                    logger.warning("[PIPELINE] State hook failed: %s", e)

            # Save proposed payload as pending (for Edit flow — user may tap Edit and modify)
            # Pending is only cleared on successful CONFIRM, not on propose
            if tool_executor and tool_executor.session_manager and tool_executor.session_id:
                try:
                    _save = {k: v for k, v in resolution.payload.items() if v is not None}
                    await tool_executor.session_manager.update_state(
                        tool_executor.session_id,
                        pending_payload=_save,
                        pending_intent=extraction.intent,
                    )
                except Exception:
                    pass

            return AgentResponse(
                message_type="DIRECT_ACTION_PREVIEW",
                content=propose_result.get("content", ""),
                pending_action_id=direct_data.get("pending_action_id", ""),
                preview=direct_data,
                expires_at=direct_data.get("expires_at", ""),
                iterations=1,
                tool_calls_made=[
                    {"name": "entity_extractor", "args": {"intent": extraction.intent}, "success": True},
                    {"name": "entity_resolver", "args": list(extraction.entities.keys()), "success": True},
                    {"name": "propose_direct_action", "args": {"action_key": extraction.intent}, "success": True},
                ],
                model_used="gpt-4o-mini-2024-07-18",
                total_latency_ms=int((_time.time() - start_time) * 1000),
                thinking_stages=["Menganalisis pesan", "Mencari data", "Menyiapkan konfirmasi"],
            )

        # Propose failed — check if validation error (missing fields) vs backend error
        error_msg = propose_result.get("error", "")
        error_type = propose_result.get("error_type", "")

        if error_type == "VALIDATION_ERROR":
            # Validation error = missing fields -> retry via natural clarification
            save_payload = {k: v for k, v in merged_entities.items() if v is not None}
            if resolution.payload:
                for k, v in resolution.payload.items():
                    if v is not None and k not in save_payload:
                        save_payload[k] = v
            if tool_executor and tool_executor.session_manager and tool_executor.session_id:
                try:
                    await tool_executor.session_manager.update_state(
                        tool_executor.session_id,
                        pending_payload=save_payload,
                        pending_intent=extraction.intent,
                    )
                except Exception:
                    pass

            # Create crud_form workflow for retry tracking
            if _wf_engine and tool_executor and tool_executor.session_id:
                try:
                    await _wf_engine.process(
                        tool_executor.session_id, "crud_form",
                        user_data={
                            "action_key": extraction.intent,
                            "payload": save_payload,
                            "intent": extraction.intent,
                        },
                    )
                except Exception:
                    pass

            # Extract clean missing labels from validation error
            _val_missing = propose_result.get("missing_fields", [])
            if not _val_missing:
                # Parse from error message as fallback
                _val_missing = [str(error_msg)]
            clarification = await self._natural_clarification(
                intent=extraction.intent,
                collected=merged_entities,
                missing_labels=_val_missing,
                resolution=resolution,
            )

            await emit("THINKING_DONE", {
                "summary": "Butuh info tambahan",
                "total_ms": int((_time.time() - start_time) * 1000),
            })

            return AgentResponse(
                message_type="TEXT",
                content=clarification,
                iterations=1,
                model_used="gpt-4o-mini-2024-07-18",
                total_latency_ms=int((_time.time() - start_time) * 1000),
                thinking_stages=["Menganalisis pesan", "Validasi data"],
            )

        # Non-validation error — show as-is
        if isinstance(error_msg, dict):
            error_msg = error_msg.get("message", str(error_msg))
        elif not isinstance(error_msg, str):
            error_msg = str(propose_result)

        await emit("THINKING_DONE", {
            "summary": "Terjadi error",
            "total_ms": int((_time.time() - start_time) * 1000),
        })

        return AgentResponse(
            message_type="TEXT",
            content=str(error_msg),
            iterations=1,
            model_used="gpt-4o-mini-2024-07-18",
            total_latency_ms=int((_time.time() - start_time) * 1000),
            thinking_stages=["Menganalisis pesan", "Validasi data"],
        )


    async def _natural_clarification(
        self,
        intent: str,
        collected: dict,
        missing_labels: list,
        resolution=None,
        override_instruction: str = None,
    ) -> str:
        """Generate natural conversational clarification using LLM."""
        import json as _json

        collected_clean = {k: v for k, v in collected.items() if v is not None and k != "date"}
        _field_labels = {
            "name": "nama", "item_name": "nama produk", "customer_name": "pelanggan",
            "vendor_name": "vendor", "bank_name": "bank", "warehouse_name": "gudang",
            "item_type": "tipe", "base_unit": "satuan (pcs, kg, box, dll)",
            "amount": "jumlah", "quantity": "qty", "unit_price": "harga satuan",
            "description": "deskripsi", "phone": "telepon", "email": "email",
            "address": "alamat", "reason": "alasan", "payment_method": "metode bayar",
            "account_type": "tipe akun",
        }
        collected_display = {_field_labels.get(k, k): v for k, v in collected_clean.items() if k in _field_labels}

        # Map API values to Indonesian display labels
        _VALUE_DISPLAY = {
            "item_type": {"goods": "persediaan", "service": "jasa", "non_inventory": "non-persediaan"},
        }
        for field_key, val_map in _VALUE_DISPLAY.items():
            display_key = _field_labels.get(field_key, field_key)
            if display_key in collected_display and isinstance(collected_display[display_key], str):
                mapped = val_map.get(collected_display[display_key].lower())
                if mapped:
                    collected_display[display_key] = mapped

        # Get field options from registry
        from .direct_action_registry import get_direct_action
        config = get_direct_action(intent)
        field_hints = {}
        if config:
            for f in config.fields:
                if f.required and f.name not in collected_clean:
                    hint = f.label
                    if f.options:
                        hint += " (" + ", ".join(f.options) + ")"
                    elif f.description:
                        hint += " - " + f.description
                    # Context-aware unit hints based on item_type
                    if f.name == "base_unit" and intent == "create_item":
                        _item_type = collected_clean.get("item_type", "")
                        if _item_type in ("service", "jasa"):
                            hint = "Satuan - Contoh: jam, sesi, paket, hari, bulan"
                        elif _item_type in ("goods", "persediaan"):
                            hint = "Satuan - Contoh: pcs, kg, box, roll, meter, lusin"
                        else:
                            hint = "Satuan - Contoh: pcs, kg, box, jam, paket, dll"
                    field_hints[f.name] = hint
            # At-least-one groups
            if hasattr(config, "at_least_one_groups"):
                for group in config.at_least_one_groups:
                    has_any = any(collected_clean.get(fn) for fn in group["fields"])
                    if not has_any:
                        # Add each field in the group as a hint
                        for fn in group["fields"]:
                            if fn not in collected_clean:
                                fs = next((f for f in config.fields if f.name == fn), None)
                                if fs:
                                    field_hints[fn] = fs.label + " (minimal salah satu)"

        # Get action description from registry (scalable — no hardcoded dict)
        _action_desc = "membuat data baru"
        if config:
            _action_desc = config.display_name.lower()

        system_prompt = (
            "Kamu asisten pembukuan. User BARU SAJA minta " + _action_desc + " via chat.\n"
            "User BELUM punya data ini — kamu sedang MEMBANTU MENDAFTARKAN, bukan mengedit.\n\n"
            "TUGASMU: Konfirmasi apa yang user minta, lalu tanya info yang kurang.\n\n"
            "RULES:\n"
            "- Mulai dengan konfirmasi aksi, misal 'Baik, saya akan daftarkan [nama].' atau 'Siap, ...'\n"
            "- JANGAN bilang 'kamu sudah punya' atau 'Jadi, kamu sudah punya' — user BARU mau buat\n"
            "- Sebutkan data yang sudah ditangkap secara natural dalam kalimat\n"
            "- Tanya field yang kurang — natural, kayak ngobrol\n"
            "- Kalau field punya opsi (goods/service), sebutkan opsinya\n"
            "- Kalau ada field '(minimal salah satu)', gunakan 'dan/atau' BUKAN 'dan'\n"
            "- JANGAN bilang 'Saya perlu info tambahan' atau 'Mohon lengkapi'\n"
            "- JANGAN pakai format list/bullet\n"
            "- JANGAN tanya field opsional — hanya yang WAJIB\n"
            "- Singkat, 1-2 kalimat\n"
            "- Bahasa Indonesia natural\n"
        )

        if override_instruction:
            user_prompt = (
                "User minta: " + _action_desc + "\n\n"
                + override_instruction + "\n\n"
                "Balas user secara natural, singkat (1-2 kalimat)."
            )
        else:
            user_prompt = (
                "User minta: " + _action_desc + "\n"
                "Data yang sudah ditangkap: " + _json.dumps(collected_display, ensure_ascii=False) + "\n"
                "Field WAJIB yang masih kurang: " + _json.dumps(field_hints, ensure_ascii=False) + "\n"
                "Balas user secara natural."
            )

        try:
            client, model = self.router.route("clarification")
            response = await client.chat(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ],
                tools=[],
                model=model,
                temperature=0.5,
                max_tokens=150,
            )
            text = (response.content or "").strip()
            if text:
                return text
        except Exception as e:
            logger.warning("[PIPELINE] Natural clarification LLM failed: %s", e)

        # Fallback
        missing_str = ", ".join(missing_labels) if missing_labels else "beberapa info"
        if collected_display:
            collected_str = ", ".join(str(k) + ": " + str(v) for k, v in list(collected_display.items())[:3])
            return "Oke, " + collected_str + ". Masih butuh: " + missing_str
        return "Untuk lanjut, saya butuh: " + missing_str

    async def _handle_query_pipeline(
        self,
        user_text: str,
        context,
        extraction,
        tool_executor=None,
        event_callback=None,
    ) -> "AgentResponse":
        """
        Query pipeline: extract -> resolve -> GET endpoint -> LLM polish -> TEXT.
        2 LLM calls total: extraction (done) + polish (~800ms).
        """
        import time as _time
        import httpx

        start_time = _time.time()

        async def emit(event_type, data):
            if event_callback:
                try:
                    await event_callback(event_type, data)
                except Exception:
                    pass

        await emit("THINKING_STEP", {
            "step_id": "query-resolve",
            "text": "Mencari data",
            "status": "running",
            "category": "search",
        })

        # Get query config from registry
        from .direct_action_registry import get_query_action
        query_config = get_query_action(extraction.intent)
        if not query_config:
            return AgentResponse(
                message_type="TEXT",
                content="Hmm, saya belum paham pertanyaannya. Coba lebih spesifik, misalnya:\n\n\u2022 \"Berapa total piutang?\"\n\u2022 \"Daftar faktur pembelian\"\n\u2022 \"Saldo kas dan bank\"\n",
                iterations=1,
                model_used="gpt-4o-mini",
                total_latency_ms=int((_time.time() - start_time) * 1000),
            )

        # Resolve entities for parameterized endpoints
        endpoint = query_config.rest_endpoint
        query_params = {}

        # Resolve item by name -> get ID for {id} endpoints
        if "{id}" in endpoint and extraction.entities.get("item_name"):
            from .entity_resolver import EntityResolver
            from .db_utils import get_session_db_pool
            pool = await get_session_db_pool()
            resolver = EntityResolver(pool, context.tenant_id)
            resolved_item = await resolver._resolve_item(extraction.entities["item_name"])
            if resolved_item and resolved_item.entity_id and resolved_item.confidence >= 0.5:
                endpoint = endpoint.replace("{id}", resolved_item.entity_id)
            else:
                # Try entity graph focus
                state = None
                if tool_executor and tool_executor.session_manager and tool_executor.session_id:
                    try:
                        state = await tool_executor.session_manager.get_state(tool_executor.session_id)
                    except Exception:
                        pass
                if state and getattr(state, 'entity_graph', None):
                    from .entity_graph import get_last_node
                    last_item = get_last_node(state.entity_graph, "item")
                    if last_item:
                        endpoint = endpoint.replace("{id}", last_item["id"])
                    else:
                        item_name = extraction.entities.get("item_name", "")
                        return AgentResponse(
                            message_type="TEXT",
                            content=f"Barang '{item_name}' tidak ditemukan.",
                            iterations=1,
                            model_used="gpt-4o-mini",
                            total_latency_ms=int((_time.time() - start_time) * 1000),
                        )
                else:
                    item_name = extraction.entities.get("item_name", "")
                    return AgentResponse(
                        message_type="TEXT",
                        content=f"Barang '{item_name}' tidak ditemukan.",
                        iterations=1,
                        model_used="gpt-4o-mini",
                        total_latency_ms=int((_time.time() - start_time) * 1000),
                    )

        # Resolve warehouse by name -> get ID
        if "{id}" in endpoint and extraction.entities.get("warehouse_name"):
            from .entity_resolver import EntityResolver
            from .db_utils import get_session_db_pool
            pool = await get_session_db_pool()
            resolver = EntityResolver(pool, context.tenant_id)
            resolved_wh = await resolver._resolve_warehouse(extraction.entities["warehouse_name"])
            if resolved_wh and resolved_wh.entity_id and resolved_wh.confidence >= 0.5:
                endpoint = endpoint.replace("{id}", resolved_wh.entity_id)
            else:
                wh_name = extraction.entities.get("warehouse_name", "")
                return AgentResponse(
                    message_type="TEXT",
                    content=f"Gudang '{wh_name}' tidak ditemukan.",
                    iterations=1,
                    model_used="gpt-4o-mini",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Resolve item_id query param (for endpoints like /api/item-batches?item_id=UUID)
        if any(qp.name == "item_id" for qp in (query_config.query_params or [])) and extraction.entities.get("item_name"):
            if "{id}" not in endpoint:  # Only for non-path-param endpoints
                from .entity_resolver import EntityResolver
                from .db_utils import get_session_db_pool
                pool = await get_session_db_pool()
                resolver = EntityResolver(pool, context.tenant_id)
                resolved_item = await resolver._resolve_item(extraction.entities["item_name"])
                if resolved_item and resolved_item.entity_id and resolved_item.confidence >= 0.5:
                    query_params["item_id"] = resolved_item.entity_id
                else:
                    return AgentResponse(
                        message_type="TEXT",
                        content=f"Barang '{extraction.entities['item_name']}' tidak ditemukan.",
                        iterations=1,
                        model_used="gpt-4o-mini",
                        total_latency_ms=int((_time.time() - start_time) * 1000),
                    )

        # Build query params from config defaults + extraction
        # Entity-to-param mapping (extractor uses entity names, API uses param names)
        _entity_aliases = {
            "search": ["item_name", "name", "keyword"],
            "warehouse_id": ["warehouse_name"],
        }
        for qp in (query_config.query_params or []):
            entity_val = extraction.entities.get(qp.name)
            if not entity_val:
                # Try aliases
                for alias in _entity_aliases.get(qp.name, []):
                    entity_val = extraction.entities.get(alias)
                    if entity_val:
                        break
            if entity_val:
                query_params[qp.name] = entity_val
            elif qp.default:
                query_params[qp.name] = qp.default

        # Bail if still unresolved {id}
        if "{id}" in endpoint:
            return AgentResponse(
                message_type="TEXT",
                content="Mohon sebutkan nama barang yang ingin dicek.",
                iterations=1,
                model_used="gpt-4o-mini",
                total_latency_ms=int((_time.time() - start_time) * 1000),
            )

        # Call REST endpoint
        try:
            base_url = "http://localhost:8000"
            auth_token = getattr(context, 'auth_token', '') or ''
            headers = {
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json",
                "X-Tenant-ID": context.tenant_id,
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{base_url}{endpoint}",
                    params=query_params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"[QUERY_PIPELINE] REST call failed: {e}")
            err_msg = str(e)[:100]
            return AgentResponse(
                message_type="TEXT",
                content=f"Gagal mengambil data: {err_msg}",
                iterations=1,
                model_used="gpt-4o-mini",
                total_latency_ms=int((_time.time() - start_time) * 1000),
            )

        await emit("THINKING_STEP", {
            "step_id": "query-resolve",
            "text": "Mencari data",
            "status": "done",
            "duration_ms": int((_time.time() - start_time) * 1000),
            "category": "search",
        })

        # Format + LLM Polish
        await emit("THINKING_STEP", {
            "step_id": "query-format",
            "text": "Menyusun jawaban",
            "status": "running",
            "category": "write",
        })

        response_text = await self._polish_query_response(
            query_config=query_config,
            data=data,
            user_text=user_text,
            entity_name=extraction.entities.get("item_name") or extraction.entities.get("warehouse_name") or "",
        )

        await emit("THINKING_STEP", {
            "step_id": "query-format",
            "text": "Menyusun jawaban",
            "status": "done",
            "duration_ms": int((_time.time() - start_time) * 1000),
            "category": "write",
        })

        await emit("THINKING_DONE", {
            "summary": "Data ditemukan",
            "total_ms": int((_time.time() - start_time) * 1000),
        })

        return AgentResponse(
            message_type="TEXT",
            content=response_text,
            iterations=1,
            tool_calls_made=[
                {"name": "entity_extractor", "args": {"intent": extraction.intent}, "success": True},
                {"name": "query_endpoint", "args": {"endpoint": endpoint}, "success": True},
            ],
            model_used=getattr(self, "_last_polish_model", "pipeline"),
            total_latency_ms=int((_time.time() - start_time) * 1000),
            thinking_stages=["Menganalisis pesan", "Mencari data", "Menyusun jawaban"],
        )

    async def _polish_query_response(self, query_config, data, user_text, entity_name=""):
        """Polish raw API response into natural Bahasa Indonesia via LLM."""
        import json as _json

        response_format = query_config.response_format

        # Simple list — template ONLY for trivial name-only entities
        # Complex data (invoices, bills, transactions) always goes to LLM polish
        if response_format == "list":
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                for _k in ("items", "data", "warehouses", "categories", "default_units", "units", "transfers", "adjustments"):
                    if _k in data and isinstance(data[_k], list):
                        items = data[_k]
                        break
                else:
                    items = []
            else:
                items = []
            _simple_entities = {"query_categories_list", "query_warehouses", "query_items_units"}
            if (query_config.action_key in _simple_entities
                    and isinstance(items, list) and 0 < len(items) <= 10):
                names = [
                    i.get("name", i.get("nama", i.get("nama_produk", "?")))
                    if isinstance(i, dict) else str(i)
                    for i in items
                ]
                count = data.get("total", len(names)) if isinstance(data, dict) else len(names)
                return str(count) + " " + query_config.display_name.lower() + ":\n" + ", ".join(names)
            # All other lists -> fall through to LLM polish

        # Compact data for LLM
        compact = self._compact_query_data(query_config.action_key, data)

        # LLM Polish
        polish_system = (
            "Kamu teman kerja yang paham bisnis dan selalu siap bantu. "
            "Balas dengan hangat dan natural — kayak ngobrol sama rekan yang bisa diandalkan. "
            "Jawab dari DATA yang ada, kasih konteks dan insight yang berguna.\n\n"
            "KEPRIBADIAN:\n"
            "- Hangat dan friendly, bukan robot — sesekali pakai emoji di poin penting\n"
            "- Emoji hemat tapi tepat: 📦 stok, 💰 uang/harga, ✅ aman/ok, ⚠️ warning, 📊 statistik\n"
            "- Bahasa Indonesia natural sehari-hari, jangan terasa seperti terjemahan\n"
            "- Langsung ke inti, tapi tetap ramah — bukan kaku\n\n"
            "ANGKA:\n"
            "- Format Rp ribuan (Rp 5.400.000), satuan (45 pcs)\n"
            "- Stok selalu integer: 110 pcs bukan 110.000 pcs\n\n"
            "INSIGHT (wajib kalau relevan):\n"
            "- 📦 Stok aman (>50): 'stoknya masih aman nih'\n"
            "- ⚠️ Stok rendah (<10): 'tinggal dikit, mungkin perlu restock ya'\n"
            "- Stok 0: 'habis nih, perlu segera diorder lagi'\n"
            "- 💰 Margin bagus (>30%): sebutin, kasih apresiasi singkat\n"
            "- Dead stock / lambat gerak: kasih saran konstruktif\n"
            "- Sertakan info TAMBAHAN dari DATA yang berguna (harga, kategori, satuan) walau nggak ditanya\n\n"
            "FORMAT:\n"
            "- List: bullet max 5 item + '(+N lainnya)'\n"
            "- Simple query: 2-4 kalimat aja, ringkas\n"
            "- JANGAN tambah info yang TIDAK ADA di DATA\n"
            "- JANGAN bilang 'berdasarkan data' atau 'menurut sistem'\n"
            "- Kalau data kosong/nol: jujur tapi positif ('belum ada transaksi' bukan 'tidak tersedia')\n"
            "\n"
            "INSIGHT:\n"
            "- Kalau ada INSIGHT section di data, WAJIB sampaikan ke user dalam bahasa natural.\n"
            "- Format: jawab pertanyaan dulu dengan data, lalu tambahkan insight di bawahnya.\n"
            "- Kalau severity=HIGH, mulai insight dengan emoji warning.\n"
            "- Kalau ada recommended_action, tutup dengan saran yang actionable.\n"
            "- JANGAN tutup dengan 'Ada yang bisa saya bantu?' — tutup dengan saran kontekstual dari insight.\n"
            "- Kalau tidak ada insight, tutup langsung tanpa basa-basi.\n"
        )

        compact_str = _json.dumps(compact, ensure_ascii=False, default=str)[:1500]

        # ── Insight Engine: rule-based interpretation ──
        _insight_text = ""
        try:
            from .insight_engine import evaluate as _evaluate_insights, format_insights_for_prompt as _format_insights
            _insights = _evaluate_insights(query_config.action_key, data)
            _insight_text = _format_insights(_insights)
            if _insights:
                logger.warning("[INSIGHT] %d insights for %s (top: %s/%s)",
                    len(_insights), query_config.action_key, _insights[0].severity, _insights[0].insight_type)
        except Exception as _ie:
            logger.warning("[INSIGHT] Failed: %s", _ie)
        polish_user = (
            f"Pertanyaan user: \"{user_text}\"\n"
            f"Tipe: {response_format}\n"
            f"DATA:\n```json\n{compact_str}\n```{_insight_text}"
        )

        try:
            client, model = self.router.route("polish")

            response = await client.chat(
                messages=[
                    LLMMessage(role="system", content=polish_system),
                    LLMMessage(role="user", content=polish_user),
                ],
                tools=[],
                model=model,
                temperature=0.3,
                max_tokens=300,
            )
            return (response.content or "").strip() or "Data ditemukan tapi gagal diformat."
        except Exception as e:
            logger.warning(f"[QUERY_PIPELINE] LLM polish failed, using template: {e}")
            return f"{query_config.display_name}:\n{_json.dumps(compact, ensure_ascii=False, indent=2, default=str)[:500]}"

    def _compact_current_data(self, entity_type: str, data: dict) -> dict:
        """Compact current entity data for display. Strip UUIDs, internal fields."""
        if entity_type == "item":
            return {
                "nama": data.get("name") or data.get("nama_produk", ""),
                "kode": data.get("item_code", ""),
                "tipe": data.get("item_type", ""),
                "satuan": data.get("base_unit") or data.get("satuan", ""),
                "harga_jual": data.get("sales_price") or data.get("harga_jual", 0),
                "harga_beli": data.get("purchase_price") or data.get("harga_beli", 0),
                "kategori": data.get("kategori", ""),
                "deskripsi": data.get("deskripsi") or data.get("description", ""),
                "sku": data.get("sku", ""),
                "barcode": data.get("barcode", ""),
                "stok": data.get("current_stock") or data.get("stock_quantity", 0),
                "status": data.get("status", ""),
            }
        elif entity_type == "customer":
            return {
                "nama": data.get("nama", data.get("name", "")),
                "telepon": data.get("telepon", data.get("phone", "")),
                "email": data.get("email", ""),
                "alamat": data.get("alamat", data.get("address", "")),
                "perusahaan": data.get("company_name", ""),
            }
        elif entity_type == "vendor":
            return {
                "nama": data.get("name", ""),
                "telepon": data.get("phone", ""),
                "email": data.get("email", ""),
                "alamat": data.get("address", ""),
                "perusahaan": data.get("company_name", ""),
            }
        elif entity_type == "warehouse":
            return {
                "nama": data.get("name", ""),
                "kode": data.get("code", ""),
                "alamat": data.get("address", ""),
                "kota": data.get("city", ""),
            }
        elif entity_type == "bank_account":
            return {
                "nama_akun": data.get("account_name", ""),
                "bank": data.get("bank_name", ""),
                "nomor": data.get("account_number", ""),
                "tipe": data.get("account_type", ""),
            }
        return {k: v for k, v in data.items() if k not in ("id", "tenant_id", "created_at", "updated_at", "deleted_at") and isinstance(v, (str, int, float, bool))}

    async def _polish_current_data(self, entity_name: str, entity_type: str, current_data: dict) -> str:
        """LLM polish: show current data naturally + ask what to change."""
        import json as _json
        display = {k: v for k, v in current_data.items() if v is not None and v != "" and v != 0}

        system_prompt = (
            "Kamu asisten pembukuan yang ramah. User mau edit data yang sudah ada.\n"
            "Tugasmu: tampilkan data saat ini secara singkat, lalu tanya mau ubah yang mana.\n\n"
            "RULES:\n"
            "- Sebut nama entity di awal\n"
            "- Tampilkan data yang ada sebagai bullet list (hanya yang ada nilainya)\n"
            "- Format angka: Rp ribuan pakai titik (contoh: Rp 350.000)\n"
            "- Akhiri dengan 'Mau ubah yang mana?' atau variasi natural\n"
            "- Singkat, 3-6 baris total\n"
            "- Bahasa Indonesia natural\n"
        )
        user_prompt = (
            f"Entity: {entity_name} (tipe: {entity_type})\n"
            f"Data saat ini:\n{_json.dumps(display, ensure_ascii=False, default=str)}"
        )
        try:
            client, model = self.router.route("polish")
            response = await client.chat(
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ],
                tools=[],
                model=model,
                temperature=0.4,
                max_tokens=200,
            )
            return (response.content or "").strip() or f"Data {entity_name} ditemukan. Mau ubah yang mana?"
        except Exception:
            lines = [f"**{entity_name}**:"]
            for k, v in display.items():
                if isinstance(v, (int, float)) and v > 1000:
                    lines.append(f"- {k}: Rp {int(v):,}".replace(",", "."))
                else:
                    lines.append(f"- {k}: {v}")
            lines.append("\nMau ubah yang mana?")
            return "\n".join(lines)

    def _compact_query_data(self, action_key: str, data) -> dict:
        """
        Generic auto-compactor for query responses.
        No per-endpoint custom code needed. Auto-detects structure,
        strips noise, renames common fields, truncates lists.
        LLM polish handles the rest.
        """
        if not data:
            return {}

        # Unwrap {"success": true, "data": {...}} envelope if present
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], (dict, list)):
            data = data["data"]

        # If data is a list, wrap it
        if isinstance(data, list):
            return {"total": len(data), "items": self._compact_list(data, max_items=10)}

        # Auto-detect the main list key in response dict
        list_data = None
        list_key = None
        _COMMON_LIST_KEYS = [
            "items", "data", "products", "transactions", "entries",
            "categories", "units", "default_units", "custom_units", "warehouses",
            "adjustments", "transfers", "stock", "lines", "batches",
            "invoices", "bills", "payments", "results", "records",
            "movements", "activities", "related", "history",
            "journal_entries", "purchase_orders",
        ]
        for key in _COMMON_LIST_KEYS:
            val = data.get(key)
            if isinstance(val, list) and len(val) > 0:
                list_data = val
                list_key = key
                break

        # Build compact response
        compact = {}

        # Copy scalar fields (totals, counts, summaries) — strip noise
        for k, v in data.items():
            if k == list_key:
                continue  # Handle list separately
            if self._is_noise_field(k):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                renamed = self._rename_field(k)
                compact[renamed] = self._safe_val(v)
            elif isinstance(v, list) and len(v) <= 5 and all(isinstance(x, str) for x in v):
                # Small string list (e.g. default_units: ["pcs", "kg"])
                compact[self._rename_field(k)] = v
            elif isinstance(v, dict) and len(v) <= 8:
                # Small nested dict (e.g. breakdown) — include stripped
                compact[self._rename_field(k)] = {
                    self._rename_field(sk): self._safe_val(sv)
                    for sk, sv in v.items()
                    if not self._is_noise_field(sk) and isinstance(sv, (str, int, float, bool, type(None)))
                }

        # Process list data
        if list_data:
            compact["total"] = compact.get("total") or data.get("total", data.get("count", len(list_data)))
            compact_key = list_key if list_key not in ("data",) else "items"
            compact[compact_key] = self._compact_list(list_data, max_items=10)
        elif not compact:
            # No list, no scalars — return raw (stripped) for LLM to figure out
            return {
                self._rename_field(k): self._safe_val(v)
                for k, v in data.items()
                if not self._is_noise_field(k) and isinstance(v, (str, int, float, bool, type(None)))
            }

        return compact

    def _compact_list(self, items: list, max_items: int = 10) -> list:
        """Compact a list of dicts: strip noise fields, rename, truncate."""
        result = []
        for item in items[:max_items]:
            if not isinstance(item, dict):
                result.append(item)
                continue
            compact_item = {}
            for k, v in item.items():
                if self._is_noise_field(k):
                    continue
                if isinstance(v, (list, dict)):
                    # Skip nested complex objects unless small
                    if isinstance(v, list) and len(v) <= 3:
                        compact_item[self._rename_field(k)] = v
                    elif isinstance(v, dict) and len(v) <= 5:
                        compact_item[self._rename_field(k)] = v
                    continue
                renamed = self._rename_field(k)
                compact_item[renamed] = self._safe_val(v)
            result.append(compact_item)
        return result

    @staticmethod
    def _safe_val(v):
        """Convert Decimal-like strings to numbers, pass through others."""
        if isinstance(v, str):
            stripped = v.strip()
            if stripped and stripped.replace(".", "", 1).replace("-", "", 1).isdigit():
                try:
                    num = float(stripped)
                    if num == int(num) and "." not in stripped:
                        return int(num)
                    # For values like "110.0000" → 110
                    if num == int(num) and abs(num) < 1e15:
                        return int(num)
                    return round(num, 2)
                except (ValueError, OverflowError):
                    pass
        return v

    _NOISE_FIELDS = frozenset({
        # UUIDs and internal IDs
        "id", "uuid", "tenant_id", "user_id", "created_by", "updated_by",
        "posted_by", "voided_by", "shipped_by", "received_by", "cancelled_by",
        "session_id", "conversation_id", "idempotency_key",
        # Timestamps (keep only human-readable dates)
        "created_at", "updated_at", "deleted_at",
        "posted_at", "voided_at",
        # Internal references
        "journal_id", "source_id",
        "coa_id", "confidentiality_level",
        "sales_account_id", "purchase_account_id",
        "inventory_account_id", "cogs_account_id",
        "pph_account_id",
        # Technical
        "is_matrix_parent", "matrix_parent_id",
        "content_unit", "wholesale_unit", "units_per_wholesale",
        "image_url", "image_urls",
    })

    _FIELD_RENAMES = {
        # Bahasa Indonesia → readable
        "nama_produk": "nama",
        "harga_jual": "harga_jual",
        "harga_beli": "harga_beli",
        "satuan": "satuan",
        "kategori": "kategori",
        "deskripsi": "deskripsi",
        "telepon": "telepon",
        "alamat": "alamat",
        # English variants → consistent
        "product_name": "nama",
        "item_name": "nama",
        "item_code": "kode",
        "account_name": "nama",
        "warehouse_name": "gudang",
        "vendor_name": "vendor",
        "customer_name": "pelanggan",
        "invoice_number": "no_faktur",
        "bill_number": "no_tagihan",
        "adjustment_number": "no_adjustment",
        "transfer_number": "no_transfer",
        # Quantity variants
        "stock_quantity": "stok",
        "current_stock": "stok",
        "current_quantity": "stok",
        "quantity": "jumlah",
        "quantity_in": "masuk",
        "quantity_out": "keluar",
        "quantity_balance": "saldo",
        "quantity_adjustment": "penyesuaian",
        "quantity_before": "sebelum",
        "quantity_after": "sesudah",
        "initial_quantity": "stok_awal",
        # Price/cost variants
        "sales_price": "harga_jual",
        "purchase_price": "harga_beli",
        "sales_price_amount": "harga_jual",
        "purchase_price_amount": "harga_beli",
        "average_cost": "wac",
        "unit_cost": "biaya_satuan",
        "total_cost": "total_biaya",
        "total_value": "nilai",
        "stock_value": "nilai_stok",
        # Status/type
        "item_type": "tipe",
        "adjustment_type": "tipe",
        "movement_type": "tipe_gerakan",
        "source_type": "sumber",
        "source_number": "no_sumber",
        "document_number": "nomor",
        "costing_method": "metode_biaya",
        "track_inventory": "lacak_stok",
        # Date variants
        "movement_date": "tanggal",
        "adjustment_date": "tanggal",
        "transfer_date": "tanggal",
        "transaction_date": "tanggal",
        "entry_date": "tanggal",
        "expiry_date": "kadaluarsa",
        "expiration_date": "kadaluarsa",
        # Misc
        "reorder_level": "stok_minimum",
        "total_products": "total_produk",
        "active_count": "aktif",
        "inactive_count": "nonaktif",
        "total_stock_value": "total_nilai_stok",
        "margin_percent": "margin_persen",
        "gross_profit": "laba_kotor",
        "batch_number": "no_batch",
        "lot_number": "no_batch",
        "from_warehouse_name": "dari",
        "to_warehouse_name": "ke",
        "from_warehouse": "dari",
        "to_warehouse": "ke",
    }

    def _is_noise_field(self, field_name: str) -> bool:
        """Check if field should be stripped from compact output."""
        if field_name in self._NOISE_FIELDS:
            return True
        # Strip any field ending with _id EXCEPT known useful ones
        if field_name.endswith("_id") and field_name not in (
            "product_id", "item_id", "warehouse_id",
        ):
            return True
        return False

    def _rename_field(self, field_name: str) -> str:
        """Rename field to human-readable Indonesian name."""
        return self._FIELD_RENAMES.get(field_name, field_name)


    async def process_message(
        self,
        user_text: str,
        context: TenantContext,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        tool_executor: Optional[ToolExecutor] = None,
        image_content: Optional[list] = None,
        event_callback=None,
        db_pool=None,
        chat_session_id: str = None,
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

        # Intent classification via heuristic (Final Cleanup: LLM classifier removed)
        _intent = _infer_intent(user_text)
        _route = RouteResult(
            intent=_intent,
            confidence=1.0,
            classifier_skipped=True,
            low_confidence_fallback=False,
            classifier_tokens_in=0,
            classifier_tokens_out=0,
            classifier_latency_ms=0,
        )
        logger.warning(
            "[INTENT] intent=%s user='%s'",
            _intent, user_text[:50],
        )

        # CHITCHAT short-circuit — bypass agent loop entirely
        if _intent == "CHITCHAT":
            return await self._handle_chitchat(user_text, context, _route)

        # ── Workflow trigger detection ──────────────────────────
        _workflow_triggers = {
            "invoice_and_payment": [
                "faktur dan bayar", "invoice dan bayar", "buat faktur langsung bayar",
                "faktur sekaligus bayar", "langsung lunas",
            ],
            "monthly_closing": [
                "tutup bulan", "closing bulan", "tutup buku", "monthly closing",
                "akhir bulan", "closing bulanan",
            ],
        }

        _text_lower = user_text.lower()
        for _wf_type, _wf_triggers in _workflow_triggers.items():
            if any(t in _text_lower for t in _wf_triggers):
                logger.warning(f"[WORKFLOW] Trigger detected: {_wf_type}")
                try:
                    # Extract period for monthly_closing
                    _wf_user_data = {}
                    if _wf_type == "monthly_closing":
                        _month_map = {
                            "januari": "01", "februari": "02", "maret": "03", "april": "04",
                            "mei": "05", "juni": "06", "juli": "07", "agustus": "08",
                            "september": "09", "oktober": "10", "november": "11", "desember": "12",
                        }
                        for _mn, _mnum in _month_map.items():
                            if _mn in _text_lower:
                                from datetime import date as _date_type
                                _wf_user_data["period"] = f"{_date_type.today().year}-{_mnum}"
                                break

                    wf_result = await tool_executor.execute(
                        "start_workflow",
                        {"workflow_type": _wf_type, "user_data": _wf_user_data},
                    )
                    if isinstance(wf_result, dict):
                        content_text = wf_result.get("llm_instruction", wf_result.get("message", ""))
                        if wf_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
                            return AgentResponse(
                                message_type="DIRECT_ACTION_PREVIEW",
                                content=content_text,
                                preview=wf_result.get("data", {}),
                                pending_action_id=wf_result.get("data", {}).get("pending_action_id", ""),
                                iterations=1,
                                model_used="gpt-4o-mini",
                                total_latency_ms=int((time.time() - start_time) * 1000),
                                thinking_stages=["Menganalisis pesan", "Memulai workflow"],
                            )
                        return AgentResponse(
                            message_type="TEXT",
                            content=content_text,
                            iterations=1,
                            model_used="gpt-4o-mini",
                            total_latency_ms=int((time.time() - start_time) * 1000),
                            thinking_stages=["Menganalisis pesan", "Memulai workflow"],
                        )
                except Exception as _wf_err:
                    logger.warning(f"[WORKFLOW] Trigger failed: {_wf_err}")
                break


        # ── COMPILER PIPELINE: Side-by-side with agent loop ──
        # Feature-flagged: only enabled intents use pipeline.
        from .entity_extractor import EntityExtractor, is_pipeline_enabled

        if _intent in ("ACTION", "SIMPLE_READ"):
            _extract_client, _extract_model = self.router.route("extraction")
            extractor = EntityExtractor(_extract_client, _extract_model)

            _ctx_summary = ""
            if tool_executor and tool_executor.session_manager and tool_executor.session_id:
                try:
                    _state = await tool_executor.session_manager.get_state(tool_executor.session_id)
                    _ctx_summary = _state.to_context_string() if hasattr(_state, "to_context_string") else ""
                except Exception:
                    pass

            # ── Code-driven CRUD intent classifier (pre-LLM, deterministic) ──
            from .entity_extractor import classify_crud_intent, classify_query_intent
            _code_intent, _code_entity_name, _code_name_field = classify_crud_intent(user_text)
            # Query code classifier first
            _qci, _, _ = classify_query_intent(user_text)
            if _qci:
                _code_intent = _qci
            if _code_intent:
                logger.warning("[PIPELINE] Code classifier: intent=%s name='%s' field=%s", _code_intent, _code_entity_name or "", _code_name_field or "")

            extraction = await extractor.extract(user_text, context_summary=_ctx_summary)
            if _code_intent:
                extraction.intent = _code_intent
                extraction.confidence = 1.0
                extraction.needs_escalation = False
                if _code_entity_name and _code_name_field:
                    extraction.entities[_code_name_field] = _code_entity_name
                logger.warning("[PIPELINE] Intent overridden by code classifier: %s", _code_intent)


            # Calculation pipeline — code-driven numerics (zero LLM compute)
            if extraction.intent.startswith("calc_") and is_pipeline_enabled(extraction.intent):
                from .calculation_engine import is_calculation_intent, get_calculation_template, execute_calculation, format_calculation_result
                if is_calculation_intent(extraction.intent):
                    logger.warning("[CALC_PIPELINE] Routing to calculation engine: intent=%s", extraction.intent)
                    _calc_template = get_calculation_template(extraction.intent)
                    _calc_result = await execute_calculation(
                        _calc_template,
                        auth_token=getattr(context, "auth_token", "") or "",
                        tenant_id=context.tenant_id,
                    )
                    if _calc_result.get("type") != "error":
                        _calc_text = format_calculation_result(_calc_result)
                        return AgentResponse(
                            message_type="TEXT",
                            content=_calc_text,
                            iterations=1,
                            model_used="calc_engine",
                            total_latency_ms=int((_time.monotonic() - _process_start) * 1000),
                        )
                    else:
                        logger.warning("[CALC_PIPELINE] Failed: %s", _calc_result)
                        extraction.needs_escalation = True

            # Query pipeline — before write pipeline
            if extraction.intent.startswith("query_") and is_pipeline_enabled(extraction.intent):
                from .direct_action_registry import get_query_action
                _qconfig = get_query_action(extraction.intent)
                if _qconfig:
                    logger.warning(
                        "[QUERY_PIPELINE] Routing to query pipeline: intent=%s",
                        extraction.intent,
                    )
                    return await self._handle_query_pipeline(
                        user_text=user_text,
                        context=context,
                        extraction=extraction,
                        tool_executor=tool_executor,
                        event_callback=event_callback,
                    )

            # ── Pending-intent fallback: if extraction is ambiguous but there's a pending action ──
            if extraction.intent in ("ambiguous", "chitchat", "unknown", "SIMPLE_READ") and tool_executor and tool_executor.session_manager and tool_executor.session_id:
                try:
                    _route_state = await tool_executor.session_manager.get_state(tool_executor.session_id)
                    _route_pending = getattr(_route_state, "pending_intent", "") or ""
                    _route_payload = getattr(_route_state, "pending_payload", {}) or {}
                    if _route_pending and _route_payload and is_pipeline_enabled(_route_pending):
                        logger.warning(
                            "[PIPELINE] Ambiguous override: extraction=%s -> pending=%s",
                            extraction.intent, _route_pending,
                        )
                        extraction.intent = _route_pending
                        extraction.needs_escalation = False
                except Exception as _re:
                    logger.warning("[PIPELINE] Routing pending check failed: %s", _re)

            # ── Active workflow override: force pipeline routing ──
            if not is_pipeline_enabled(extraction.intent) or extraction.needs_escalation:
                if tool_executor and tool_executor.session_id:
                    try:
                        from .workflow_engine import WorkflowEngine
                        from .db_utils import get_session_db_pool as _rp_pool
                        _rp_db = await _rp_pool()
                        _rp_engine = WorkflowEngine(
                            _rp_db, context.tenant_id,
                            getattr(context, "user_id", ""),
                            getattr(context, "auth_token", ""),
                        )
                        _rp_wf = await _rp_engine.get_state(tool_executor.session_id, "crud_form")
                        if _rp_wf and _rp_wf.status == "active":
                            _rp_intent = _rp_wf.data.get("intent", "")
                            if _rp_intent and is_pipeline_enabled(_rp_intent):
                                logger.warning(
                                    "[PIPELINE] Active workflow override: %s -> %s",
                                    extraction.intent, _rp_intent,
                                )
                                extraction.intent = _rp_intent
                                extraction.needs_escalation = False
                    except Exception as _rp_err:
                        logger.warning("[PIPELINE] Workflow routing check failed: %s", _rp_err)

            if is_pipeline_enabled(extraction.intent) and not extraction.needs_escalation:
                logger.warning(
                    "[PIPELINE] Routing to compiler pipeline: intent=%s confidence=%.2f",
                    extraction.intent, extraction.confidence,
                )
                return await self._handle_pipeline(
                    user_text=user_text,
                    context=context,
                    extraction=extraction,
                    conversation_history=conversation_history,
                    tool_executor=tool_executor,
                    event_callback=event_callback,
                )
            else:
                logger.warning(
                    "[PIPELINE] Fallback to agent loop: intent=%s confidence=%.2f escalation=%s",
                    extraction.intent, extraction.confidence, extraction.needs_escalation,
                )
        # ── END COMPILER PIPELINE ──

        # Build messages — segmented system prompt (Phase 3A)
        # Segments loaded based on intent: CHITCHAT=~500tok, SIMPLE_READ=~2.5K, etc.
        system_msgs = build_system_messages(
            tenant_name=context.tenant_name,
            today=date.today().isoformat(),
            user_text=user_text,
            intent=_intent,
        )

        messages: List[LLMMessage] = [
            LLMMessage(role=msg["role"], content=msg["content"])
            for msg in system_msgs
        ]

        # Conversation history injection
        if conversation_history:
            if chat_session_id:
                # SessionAwareAgent already assembled 4-layer context
                # with token budget management. Add as-is — no re-pruning.
                for msg in conversation_history:
                    messages.append(
                        LLMMessage(
                            role=msg.get("role", "user"),
                            content=msg.get("content", ""),
                        )
                    )
            else:
                # Fallback: no session manager, use pruning
                needs_summary, older, recent, older_hash = _prune_history(
                    conversation_history,
                )
                if needs_summary and older:
                    summary = await _summarize_history(older, self.router)
                    logger.warning(
                        "[Phase3C] Summarized %d older messages (%d chars) -> %d chars",
                        len(older),
                        sum(len(m.get("content", "")) for m in older),
                        len(summary),
                    )
                    messages.append(
                        LLMMessage(
                            role="system",
                            content=f"Ringkasan percakapan sebelumnya:\n{summary}",
                        )
                    )
                    for msg in recent:
                        messages.append(
                            LLMMessage(
                                role=msg.get("role", "user"),
                                content=msg.get("content", ""),
                            )
                        )
                else:
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
        # Phase 2A: Domain-based tool loading
        if model_choice.tier == "cheap":
            tools = []
            _active_domains = {"CORE"}
        else:
            _intent_hint = get_intent_bias(user_text)
            _active_domains = resolve_domains(user_text, _intent_hint)
            tools = get_tools_for_domains(_active_domains)
            logger.warning(
                "[Phase2] domains=%d tools=%d active=%s",
                len(_active_domains), len(tools), sorted(_active_domains),
            )
            logger.warning(
                f"[DOMAIN] domains={sorted(_active_domains)} tools_loaded={len(tools)} "
                f"(of {len(get_tools())} total)"
            )

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
            # Phase 4: Classifier telemetry
            "classifier_tokens_in": _route.classifier_tokens_in,
            "classifier_tokens_out": _route.classifier_tokens_out,
            "classifier_latency_ms": _route.classifier_latency_ms,
            "classifier_intent": _route.intent,
            "classifier_confidence": _route.confidence,
            "classifier_skipped": _route.classifier_skipped,
            "low_confidence_fallback": _route.low_confidence_fallback,
        }

        _max_iter = MAX_ITERATIONS_BY_INTENT.get(_intent, MAX_ITERATIONS)
        for iteration in range(_max_iter):
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

                # ── NUDGE: If ACTION intent, LLM has data but didn't call propose_direct_action ──
                _tool_names_used = {tc.get("name", "") for tc in tool_calls_log}
                _has_data_tools = bool(_tool_names_used & {"get_bills", "search_bank_accounts", "get_sales_invoices", "search_customers", "search_vendors", "update_document_context"})
                _has_action_tool = bool(_tool_names_used & {"propose_direct_action", "propose_action"})
                _has_doc_update = "update_document_context" in _tool_names_used

                # ── DOC-REPROPOSE: Build DIRECT_ACTION_PREVIEW deterministically from updated doc ──
                if _has_doc_update and not _has_action_tool and tool_executor.session_manager and tool_executor.session_id:
                    try:
                        _rp_state = await tool_executor.session_manager.get_state(tool_executor.session_id)
                        _rp_doc = getattr(_rp_state, "document_context", None)
                        if _rp_doc and _rp_doc.get("document_id"):
                            logger.warning(f"[DOC-REPROPOSE] Building re-proposal from corrected document_context")
                            # Apply edits to base data
                            _rp_edits = _rp_doc.get("edits", {})
                            _rp_vendor = _rp_edits.get("vendor_name", _rp_doc.get("vendor_name", ""))
                            _rp_total = float(_rp_edits.get("total_amount", _rp_doc.get("total_amount", 0)))
                            _rp_tax = float(_rp_edits.get("tax_amount", _rp_doc.get("tax_amount", 0)))
                            _rp_items = _rp_doc.get("items", [])
                            # Apply item edits
                            _item_edits = _rp_edits.get("items", {})
                            for _idx_str, _ie in _item_edits.items():
                                _idx = int(_idx_str)
                                if 0 <= _idx < len(_rp_items):
                                    _rp_items[_idx] = {**_rp_items[_idx], **_ie}

                            # Build preview payload
                            import uuid as _rp_uuid
                            _rp_pending_id = str(_rp_uuid.uuid4())
                            _rp_preview = {
                                "pending_action_id": _rp_pending_id,
                                "action_type": "CONFIRM_DOCUMENT_DRAFT",
                                "action_key": "confirm_document_draft",
                                "display_name": "Konfirmasi Dokumen",
                                "confirmation_table": (
                                    f"| Field | Value |\n|---|---|\n"
                                    f"| Vendor | {_rp_vendor} |\n"
                                    f"| No. Dokumen | {_rp_doc.get('document_number', '-')} |\n"
                                    f"| Tanggal | {_rp_doc.get('document_date', '-')} |\n"
                                    f"| Total | Rp {_rp_total:,.0f} |\n"
                                    f"| Pajak | Rp {_rp_tax:,.0f} |\n"
                                ),
                                "payload": {
                                    "document_id": _rp_doc.get("document_id"),
                                    "vendor_name": _rp_vendor,
                                    "document_number": _rp_doc.get("document_number"),
                                    "document_date": _rp_doc.get("document_date"),
                                    "total_amount": _rp_total,
                                    "tax_amount": _rp_tax,
                                    "items": _rp_items,
                                },
                                "replaces_action_id": _rp_doc.get("pending_action_id"),
                                "loading_message": f"Memproses dokumen dari {_rp_vendor}...",
                                "entity_type": "document",
                            }

                            # Store pending action
                            from datetime import datetime, timedelta, timezone
                            _rp_expires = datetime.now(timezone.utc) + timedelta(seconds=300)
                            try:
                                from .db_utils import get_session_db_pool as _rp_get_pool
                                _rp_pool = await _rp_get_pool()
                                await _rp_pool.execute(
                                    """INSERT INTO pending_actions (id, tenant_id, user_id, action_id, action_type, action_category, action_plan, status, expires_at)
                                       VALUES ($1, $2, $3, $4, $5, $6, $7, 'PENDING', $8)""",
                                    _rp_uuid.UUID(_rp_pending_id),
                                    context.tenant_id,
                                    context.user_id,
                                    "confirm_document_draft",
                                    "CONFIRM_DOCUMENT_DRAFT",
                                    "DOCUMENT",
                                    _json_helpers.dumps(_rp_preview["payload"]),
                                    _rp_expires,
                                )
                            except Exception as _rp_db_err:
                                logger.warning(f"[DOC-REPROPOSE] Failed to store pending: {_rp_db_err}")

                            # Update document_context with new pending_action_id
                            _rp_doc["pending_action_id"] = _rp_pending_id
                            await tool_executor.session_manager.update_state(
                                tool_executor.session_id, document_context=_rp_doc
                            )

                            return AgentResponse(
                                message_type="DIRECT_ACTION_PREVIEW",
                                content=f"Data dokumen dikoreksi. Vendor diubah menjadi {_rp_vendor}.",
                                preview=_rp_preview,
                                pending_action_id=_rp_pending_id,
                                expires_at=_rp_expires.isoformat(),
                                iterations=iteration + 1,
                                tool_calls_made=tool_calls_log,
                                model_used=current_model,
                                total_latency_ms=int((time.time() - start_time) * 1000),
                                thinking_stages=thinking_stages + ["Menyiapkan konfirmasi ulang"],
                                usage=accumulated_usage,
                            )
                    except Exception as _rp_err:
                        logger.warning(f"[DOC-REPROPOSE] Failed: {_rp_err}")
                # ── END DOC-REPROPOSE ──

                _should_nudge = (
                    _intent in ("ACTION",)
                    and iteration >= 1
                    and _has_data_tools
                    and not _has_action_tool
                )
                if _should_nudge and not locals().get("_nudge_done"):
                    _nudge_done = True
                    logger.warning(f"[NUDGE] LLM has data but didn't call propose_direct_action. Injecting nudge at iter={iteration}")
                    messages.append(LLMMessage(role="assistant", content=llm_response.content or ""))
                    messages.append(LLMMessage(role="user", content=(
                            "Jangan tanya konfirmasi via text. "
                            "LANGSUNG panggil propose_direct_action() dengan data yang sudah kamu dapatkan. "
                            "Kamu sudah punya semua data yang dibutuhkan dari tool calls sebelumnya."
                        )
                    ))
                    thinking_stages.append("Menyiapkan konfirmasi")
                    continue  # Go back to loop for another LLM call
                # ── END NUDGE ──

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

            # ── DOC-INTERCEPT: Redirect search_vendors/search_customers to update_document_context ──
            # When document_context is active, user corrections like "vendornya PT X"
            # cause LLM to call search_vendors instead of update_document_context.
            # Deterministic intercept: replace the tool call before execution.
            _doc_intercept_map = {"search_vendors": "vendor_name", "search_customers": "vendor_name"}
            if (
                tool_executor.session_manager
                and tool_executor.session_id
                and any(tc.function_name in _doc_intercept_map for tc in all_tool_calls)
            ):
                try:
                    _di_state = await tool_executor.session_manager.get_state(tool_executor.session_id)
                    _di_doc_ctx = getattr(_di_state, "document_context", None)
                    if _di_doc_ctx and _di_doc_ctx.get("document_id"):
                        _new_tool_calls = []
                        _intercepted = False
                        for tc in all_tool_calls:
                            if tc.function_name in _doc_intercept_map and not _intercepted:
                                _edit_field = _doc_intercept_map[tc.function_name]
                                _search_q = (tc.arguments or {}).get("q") or (tc.arguments or {}).get("query") or (tc.arguments or {}).get("search") or (tc.arguments or {}).get("name", "")
                                if _search_q:
                                    logger.warning(f"[DOC-INTERCEPT] Redirecting {tc.function_name}('{_search_q}') -> update_document_context(edits={{{_edit_field}: '{_search_q}'}})")
                                    tc.function_name = "update_document_context"
                                    tc.arguments = {"edits": {_edit_field: _search_q}}
                                    _intercepted = True
                            _new_tool_calls.append(tc)
                        all_tool_calls = _new_tool_calls
                except Exception as _di_err:
                    logger.warning(f"[DOC-INTERCEPT] Failed to check document_context: {_di_err}")
            # ── END DOC-INTERCEPT ──
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
                            "category": _get_tool_category(_ptc.function_name),
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
                    _rc, _rl = _extract_result_meta(tc.function_name, result)
                    _si = _extract_sub_items(tc.function_name, result)
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
                                tc.function_name, _infer_tool_success(result)
                            ),
                            "category": _get_tool_category(tc.function_name),
                            "result_count": _rc,
                            "result_label": _rl,
                            "sub_items": _si,
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
                        "category": _get_tool_category(tool_name),
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
                _src, _srl = _extract_result_meta(tool_name, result)
                _ssi = _extract_sub_items(tool_name, result)
                await emit(
                    "THINKING_STEP",
                    {
                        "step_id": tool_id,
                        "text": _format_thinking_label(tool_name, _seq_args_json),
                        "status": "done",
                        "duration_ms": _seq_tool_duration,
                        "badge": _get_thinking_badge(
                            tool_name,
                            _infer_tool_success(result),
                        ),
                        "category": _get_tool_category(tool_name),
                        "result_count": _src,
                        "result_label": _srl,
                        "sub_items": _ssi,
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
            content="Maaf, saya belum paham maksudnya. Coba ceritakan lebih spesifik, misalnya:\n\n\u2022 \"Catat pembelian kain 800rb\"\n\u2022 \"Cek piutang pelanggan Budi\"\n\u2022 \"Berapa total pengeluaran bulan ini\"\n"
            "Bisa coba ulangi dengan informasi yang lebih spesifik?",
            iterations=_max_iter,
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
