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

import json
import os
import time
import logging
from datetime import date

# Phase 2: LLM Router primary classifier feature flag
USE_LLM_ROUTER = os.environ.get("USE_LLM_ROUTER", "false").lower() == "true"
from typing import Any, Dict, List, Optional  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from uuid import uuid4  # noqa: E402

from ..llm import LLMRouter, TaskComplexity, LLMMessage, LLMResponse  # noqa: E402
from .system_prompt import build_system_messages, get_intent_bias, _infer_intent  # noqa: E402
from .intent_classifier import (  # noqa: E402
    RouteResult,
)  # classify_and_route removed (Final Cleanup)  # noqa: E402
from .tool_registry import get_tools, get_tools_for_domains  # noqa: E402
from .tool_executor import ToolExecutor, TenantContext, get_stage_label  # noqa: E402
from .tool_registry import is_tutorial_tool  # noqa: E402
from .model_router import ModelRouter  # noqa: E402
from .guard_arbiter import GuardArbiter  # noqa: E402
from .correlation import TurnContext  # noqa: E402

logger = logging.getLogger("unified_agent.orchestrator")

# ─── Phase 2A: Domain-Based Tool Loading ─────────────────────────────────────
# Maps intent_bias modes → tool domains.
# Uses get_intent_bias() return value to determine which tool domains to load.

# Broad fallback — when no signal words match
BROAD_FALLBACK_DOMAINS = {
    "CORE",
    "MASTER_DATA",
    "BANKING",
    "REPORTS",
    "ACTIONS",
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
        "piutang",
        "receivable",
        "faktur jual",
        "sales invoice",
        "invoice",
        "pelanggan",
        "customer",
        "penjualan",
    ]
    if any(w in text_lower for w in ar_words):
        domains |= {"AR_INVOICES", "MASTER_DATA"}

    # ── AP signals (hutang / vendor side) ──
    ap_words = [
        "hutang",
        "payable",
        "tagihan",
        "bill",
        "vendor",
        "supplier",
        "pembelian",
        "faktur beli",
        "purchase",
    ]
    if any(w in text_lower for w in ap_words):
        domains |= {"AP_BILLS", "MASTER_DATA"}

    # ── Obligation words → BOTH AR and AP (ambiguous side) ──
    # "berapa yang belum dibayar si Budi?" — could be customer or vendor
    obligation_words = [
        "bayar",
        "dibayar",
        "belum bayar",
        "belum dibayar",
        "jatuh tempo",
        "overdue",
        "outstanding",
        "tunggakan",
        "lunas",
        "belum lunas",
        "sisa",
        "terutang",
    ]
    if any(w in text_lower for w in obligation_words):
        domains |= {"AR_INVOICES", "AP_BILLS", "MASTER_DATA"}

    # ── Banking signals ──
    bank_words = ["bank", "rekening", "saldo", "transfer", "mutasi", "kas"]
    if any(w in text_lower for w in bank_words):
        domains |= {"BANKING"}

    # ── Accounting signals ──
    acct_words = [
        "jurnal",
        "journal",
        "buku besar",
        "ledger",
        "neraca saldo",
        "trial balance",
        "akun",
        "coa",
    ]
    if any(w in text_lower for w in acct_words):
        domains |= {"ACCOUNTING"}

    # ── Report signals ──
    report_words = [
        "laba rugi",
        "profit",
        "neraca",
        "balance sheet",
        "arus kas",
        "cash flow",
        "laporan",
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
        "stok",
        "stock",
        "persediaan",
        "inventory",
        "gudang",
        "warehouse",
        "barang",
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
        "deposit",
        "uang muka",
        "giro",
        "cheque",
        "recurring",
        "berulang",
        "sales order",
        "pesanan",
        "quote",
        "penawaran",
        "aset tetap",
        "fixed asset",
    ]
    if any(w in text_lower for w in pipe_words):
        domains |= {"PIPELINE"}

    # ── Analytics ──
    analytics_words = [
        "rasio",
        "ratio",
        "budget",
        "anggaran",
        "cost center",
        "payroll",
        "gaji",
    ]
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
    if function_name in (
        "agentic_reconcile",
        "confirm_single_match",
        "categorize_statement",
    ):
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
    for key in (
        "results",
        "data",
        "bills",
        "invoices",
        "items",
        "entries",
        "transactions",
        "accounts",
        "vendors",
        "customers",
        "payments",
    ):
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

        sub_items.append(
            {
                "id": str(item.get("id", f"si-{len(sub_items)}")),
                "title": str(title),
                "subtitle": subtitle,
                "badge": badge_label,
            }
        )

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
    session_id: str = ""  # Session ID for frontend session tracking
    extra_data: Dict[str, Any] = field(
        default_factory=dict
    )  # For CLARIFICATION options


# ─── Phase 3C: Conversation History Pruning ──────────────────────────────────
# Sliding window: keep last RECENT_WINDOW messages verbatim.
# Older messages: summarize with gpt-4o-mini and cache the summary.

RECENT_WINDOW = 4  # Keep last 4 messages verbatim
SUMMARIZE_THRESHOLD = 8000  # Estimate: trigger summarization above this
_CHARS_PER_TOKEN = 4  # Rough estimate for token counting

import hashlib as _hashlib  # noqa: E402


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
    """Truncate history if too long. No rolling summary (DEPRECATED - replaced by Tier 3).

    Returns (needs_summary=False always, older_messages, recent_messages, older_hash)
    """
    if not conversation_history:
        return False, [], [], ""

    total_tokens = _estimate_tokens(conversation_history)

    if total_tokens <= SUMMARIZE_THRESHOLD:
        return False, [], conversation_history, ""

    recent = conversation_history[-RECENT_WINDOW:]
    older = conversation_history[:-RECENT_WINDOW]

    if not older:
        return False, [], conversation_history, ""

    older_text = "".join(m.get("content", "") for m in older)
    older_hash = _hashlib.md5(older_text.encode()).hexdigest()

    # DEPRECATED: Never request new summary. Tier 3 handles cross-session summaries.
    return False, older, recent, older_hash


def _safe_num(val, decimals=0):
    """Convert string/Decimal to int or float for compact data."""
    try:
        f = float(val)
        if decimals == 0 and f == int(f):
            return int(f)
        return round(f, decimals)
    except (TypeError, ValueError):
        return 0


def _strip_draft_void_rows(text: str) -> str:
    """Post-process: remove Draft/Void rows from markdown tables in bot responses.
    Only applies to tables that contain financial data (hutang/piutang/tagihan/faktur)."""
    if not text or "|" not in text:
        return text
    lines = text.split("\n")
    result = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table:
                in_table = True
            # Skip rows containing Draft or Void status
            cells = [c.strip().lower() for c in stripped.split("|")]
            if any(cell in ("draft", "void") for cell in cells):
                continue
        else:
            in_table = False
        result.append(line)
    return "\n".join(result)


class UnifiedAgent:
    """
    Single agent loop. Replaces: intent classifier + action_planner + enrichment.
    Pattern: identical to ragllm orchestrator (proven 12/12 tests).

    Uses LLM abstraction layer for provider-agnostic model calls.
    """

    def __init__(self):
        self.router = LLMRouter.from_env()
        self.guard_arbiter = GuardArbiter()

    async def _handle_chitchat(
        self,
        user_text: str,
        context: TenantContext,
        route_result: "RouteResult",
        resume_hint: str = "",
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
            LLMMessage(role=msg["role"], content=msg["content"]) for msg in sys_msgs
        ]
        # Phase B.2: inject resume context if available
        if resume_hint:
            messages.append(LLMMessage(role="system", content=resume_hint))
        messages.append(LLMMessage(role="user", content=user_text))

        try:
            resp = await self.router.complete(
                task_type="chitchat",
                messages=messages,
                temperature=TEMPERATURE_CHAT,
                max_tokens=300,
            )
            content = (resp.content or "").strip()
            model = getattr(resp, "model", None) or "gemini-2.5-flash-lite"
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
            user_text[:30],
            latency_ms,
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

        await emit(
            "THINKING_STEP",
            {
                "step_id": "pipeline-resolve",
                "text": "Menyiapkan data",
                "status": "running",
                "category": "search",
            },
        )

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
                state = await tool_executor.session_manager.get_state(
                    tool_executor.session_id
                )
                memory_state = {
                    "active_customer_id": getattr(state, "active_customer_id", None),
                    "active_customer_name": getattr(
                        state, "active_customer_name", None
                    ),
                    "active_vendor_id": getattr(state, "active_vendor_id", None),
                    "active_vendor_name": getattr(state, "active_vendor_name", None),
                    "active_invoice_id": getattr(state, "active_invoice_id", None),
                    "active_invoice_number": getattr(
                        state, "active_invoice_number", None
                    ),
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

                am = ActionMemory(
                    pool, context.tenant_id, getattr(context, "user_id", "")
                )
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

        # Phase B.1: Get top patterns for suggestion in clarification
        _top_patterns = []
        if tool_executor and tool_executor.session_manager:
            try:
                from .action_memory import ActionMemory

                _am = ActionMemory(
                    pool, context.tenant_id, getattr(context, "user_id", "")
                )
                _top_patterns = await _am.get_top_patterns_for_intent(
                    extraction.intent, limit=3
                )
            except Exception as e:
                logger.warning("[PIPELINE] Top patterns lookup failed: %s", e)

        merged_entities = dict(extraction.entities)

        # ── Stage 2: Registry-driven field extraction ──
        # If intent has registry fields not in Stage 1 schema, extract them.
        # This fires an additional cheap LLM call (~300ms, ~100 tokens).
        from .direct_action_registry import get_direct_action as _s2_get_config
        from .entity_extractor import EXTRACTION_SCHEMAS as _S1_SCHEMAS

        _da_config = _s2_get_config(extraction.intent)
        if (
            _da_config
            and _da_config.fields
            and not extraction.intent.startswith("query_")
        ):
            _stage1_fields = set(
                _S1_SCHEMAS["general"]["json_schema"]["schema"]["properties"][
                    "entities"
                ]["properties"].keys()
            )
            _registry_fields = {
                f.name for f in _da_config.fields if not f.hidden and not f.display_only
            }
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

                    # ── Price field dedup for create_item ──
                    # Stage 1 often extracts generic "unit_price" from any price mention.
                    # If Stage 2 gave purchase_price but NOT sales_price, the Stage 1
                    # unit_price is a false positive — remove it to avoid wrong Harga Jual.
                    if (
                        extraction.intent == "create_item"
                        and "purchase_price" in _s2_result
                        and "sales_price" not in _s2_result
                        and "unit_price" in merged_entities
                        and "unit_price" not in _s2_result
                    ):
                        del merged_entities["unit_price"]
                        logger.warning(
                            "[PIPELINE] Removed false-positive unit_price "
                            "(Stage 2 has purchase_price only)"
                        )

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
            user_text=user_text,
            session_id=(tool_executor.session_id if tool_executor else "") or "",
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

        await emit(
            "THINKING_STEP",
            {
                "step_id": "pipeline-resolve",
                "text": "Menyiapkan data",
                "status": "done",
                "duration_ms": int((_time.time() - start_time) * 1000),
                "category": "search",
            },
        )

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
                    _wf_db,
                    context.tenant_id,
                    getattr(context, "user_id", ""),
                    getattr(context, "auth_token", ""),
                )
                _active_crud_wf = await _wf_engine.get_state(
                    tool_executor.session_id, "crud_form"
                )
                if _active_crud_wf and _active_crud_wf.status != "active":
                    _active_crud_wf = None
            except Exception as _wf_err:
                logger.warning(
                    "[PIPELINE] Workflow check failed (non-fatal): %s", _wf_err
                )

        if _active_crud_wf and _wf_engine:
            # ── ACTIVE WORKFLOW: this path handles EVERYTHING and RETURNs ──
            _wf_action_key = _active_crud_wf.data.get("action_key", "")
            _wf_intent = _active_crud_wf.data.get("intent", "")
            _wf_payload = _active_crud_wf.data.get("payload", {})

            # ── Pre-check: detect user intent switch away from active CRUD workflow ──
            # Two layers: (1) code classifier for known query patterns,
            # (2) question-pattern heuristic for "ada X?", "apakah Y?", "berapa Z?" etc.
            # Without this, workflow path consumes everything and the LLM router never runs.
            import re as _wf_re
            from .entity_extractor import classify_query_intent as _wf_qci

            _wf_query_intent, _, _ = _wf_qci(user_text)
            _wf_is_question = bool(
                _wf_re.search(
                    r"^(?:ada(?:kah)?|apakah|berapa|kapan|siapa|apa(?:kah)?|bagaimana|gimana|"
                    r"mana|kenapa|mengapa|dimana|kemana|sudah|belum|bisa)\b",
                    user_text.strip().lower(),
                )
            ) or user_text.strip().endswith("?")

            if _wf_query_intent:
                await _wf_engine.cancel(tool_executor.session_id, "crud_form")
                logger.warning(
                    "[PIPELINE] Cancelled crud_form (query classifier): was %s, query=%s",
                    _wf_intent,
                    _wf_query_intent,
                )
                extraction.intent = _wf_query_intent
                extraction.confidence = 1.0
                # Fall through to normal flow below
            elif _wf_is_question:
                # User is asking a question — not providing CRUD field data.
                # Cancel workflow, let LLM router handle intent classification.
                await _wf_engine.cancel(tool_executor.session_id, "crud_form")
                logger.warning(
                    "[PIPELINE] Cancelled crud_form (question heuristic): was %s, text='%s'",
                    _wf_intent,
                    user_text[:60],
                )
                # Don't override extraction.intent — let LLM router classify
                # Fall through to normal flow below

            # Check if user started a DIFFERENT action
            elif (
                extraction.intent
                and extraction.intent not in ("ambiguous", "chitchat", "")
                and _wf_intent
                and extraction.intent != _wf_intent
                and extraction.confidence > 0.7
            ):
                await _wf_engine.cancel(tool_executor.session_id, "crud_form")
                logger.warning(
                    "[PIPELINE] Cancelled stale crud_form: was %s, now %s",
                    _wf_intent,
                    extraction.intent,
                )
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

                    _num_match = _pick_re.search(r"\b(\d+)\b", _user_lower)
                    if _num_match:
                        _pick_idx = int(_num_match.group(1)) - 1
                        if 0 <= _pick_idx < len(_wf_candidates):
                            _matched_candidate = _wf_candidates[_pick_idx]

                    # Try name substring match ("dewasa", "anak", "yang dewasa")
                    if not _matched_candidate:
                        # Check if any significant word from user text appears in candidate name
                        _skip_words = {
                            "yang",
                            "mau",
                            "pilih",
                            "nomor",
                            "no",
                            "item",
                            "barang",
                            "produk",
                            "itu",
                            "ini",
                            "ya",
                            "dong",
                            "deh",
                            "aja",
                            "saja",
                        }
                        _user_words = [
                            w
                            for w in _user_lower.split()
                            if w not in _skip_words and len(w) > 1
                        ]
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
                        logger.warning(
                            "[PIPELINE] Candidate picked: %s -> id=%s",
                            _matched_candidate["name"],
                            str(_matched_candidate["id"])[:8],
                        )
                        await _wf_engine.cancel(tool_executor.session_id, "crud_form")

                        # Directly execute update flow: fetch current data + show
                        _entity_type = _wf_action_key.replace("update_", "").replace(
                            "delete_", ""
                        )
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
                                    _rr = await _cl.get(
                                        f"http://localhost:8000{_get_ep}",
                                        headers={
                                            "Authorization": f"Bearer {_auth}",
                                            "X-Tenant-ID": context.tenant_id,
                                        },
                                    )
                                    if _rr.status_code == 200:
                                        _raw = _rr.json()
                                        _cur_data = (
                                            _raw.get("data", _raw)
                                            if isinstance(_raw, dict)
                                            else _raw
                                        )
                            except Exception as _e:
                                logger.warning(
                                    "[PIPELINE] Fetch after candidate pick failed: %s",
                                    _e,
                                )

                        if _cur_data:
                            _display = self._compact_current_data(
                                _entity_type, _cur_data
                            )
                            _real_name = (
                                _cur_data.get("nama_produk")
                                or _cur_data.get("nama")
                                or _cur_data.get("name")
                                or _entity_name
                            )

                            # Create fresh workflow with resolved entity
                            try:
                                await _wf_engine.process(
                                    tool_executor.session_id,
                                    "crud_form",
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

                            _show = await self._polish_current_data(
                                entity_name=_real_name,
                                entity_type=_entity_type,
                                current_data=_display,
                            )
                            await emit(
                                "THINKING_DONE",
                                {
                                    "summary": "Data ditemukan",
                                    "total_ms": int((_time.time() - start_time) * 1000),
                                },
                            )
                            return AgentResponse(
                                message_type="TEXT",
                                content=_show,
                                iterations=1,
                                model_used="pipeline",
                                total_latency_ms=int(
                                    (_time.time() - start_time) * 1000
                                ),
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
                        await emit(
                            "THINKING_DONE",
                            {
                                "summary": "Pilih salah satu",
                                "total_ms": int((_time.time() - start_time) * 1000),
                            },
                        )
                        return AgentResponse(
                            message_type="TEXT",
                            content="Maaf, saya tidak yakin yang mana. Pilih salah satu:\n"
                            + "\n".join(f"{i+1}. {n}" for i, n in enumerate(_names)),
                            iterations=1,
                            model_used="pipeline",
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
                        if v is not None and (
                            k not in merged_for_wf or merged_for_wf.get(k) is None
                        ):
                            merged_for_wf[k] = v

                # ── Inject fields from persisted OCR text (document_context) ──
                # When user says "data ada di file tadi", extraction returns nothing.
                # But OCR text from Turn 1 is persisted in document_context.ocr_text.
                if (
                    tool_executor
                    and getattr(tool_executor, "session_manager", None)
                    and getattr(tool_executor, "session_id", None)
                ):
                    try:
                        _ocr_state = await tool_executor.session_manager.get_state(
                            tool_executor.session_id
                        )
                        _ocr_dc = getattr(_ocr_state, "document_context", None) or {}
                        _ocr_text_saved = _ocr_dc.get("ocr_text", "")
                        if _ocr_text_saved and _ocr_dc.get("source") == "intent_ocr":
                            import re as _ocr_re

                            # Extract phone (Indonesian: 08xx-xxxx-xxxx, 021-xxx-xxxx)
                            if not merged_for_wf.get("phone"):
                                _phones = _ocr_re.findall(
                                    r"0\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,5}",
                                    _ocr_text_saved,
                                )
                                if _phones:
                                    merged_for_wf["phone"] = _phones[0].strip()

                            # Extract address (Jl./Jalan/Alamat prefix)
                            if not merged_for_wf.get("address"):
                                _addr = _ocr_re.search(
                                    r"(?:Jl\.?|Jalan|Alamat\s*:?\s*)([^\n]{5,})",
                                    _ocr_text_saved,
                                    _ocr_re.IGNORECASE,
                                )
                                if _addr:
                                    merged_for_wf["address"] = _addr.group(0).strip()

                            # Extract email
                            if not merged_for_wf.get("email"):
                                _email = _ocr_re.search(
                                    r"[\w.+-]+@[\w-]+\.[\w.-]+", _ocr_text_saved
                                )
                                if _email:
                                    merged_for_wf["email"] = _email.group(0)

                            logger.warning(
                                "[WF_OCR] Injected fields from persisted OCR (%d chars)",
                                len(_ocr_text_saved),
                            )
                    except Exception as _ocr_inject_err:
                        logger.warning(
                            "[WF_OCR] OCR inject failed (non-blocking): %s",
                            _ocr_inject_err,
                        )

                # Process workflow with merged payload
                _wf_result = await _wf_engine.process(
                    tool_executor.session_id,
                    "crud_form",
                    user_data={
                        "payload": merged_for_wf,
                        "action_key": _wf_action_key,
                        "intent": _wf_intent,
                    },
                )

                if (
                    _wf_result.new_state in ("PROPOSING", "COMPLETED")
                    or _wf_result.completed
                ):
                    # Gate passed: all fields present. Orchestrator handles propose directly.
                    _final_payload = merged_for_wf

                    # ── Entity ID resolution for update_*/delete_* ──
                    if (
                        _wf_action_key.startswith("update_")
                        or _wf_action_key.startswith("delete_")
                    ) and "id" not in _final_payload:
                        _WF_ENTITY_SEARCH = {
                            "update_item": ("item_name", "/api/items", "nama_produk"),
                            "update_customer": (
                                "customer_name",
                                "/api/customers",
                                "nama",
                            ),
                            "update_vendor": ("vendor_name", "/api/vendors", "name"),
                            "update_bank_account": (
                                "bank_name",
                                "/api/bank-accounts",
                                "account_name",
                            ),
                            "update_warehouse": (
                                "warehouse_name",
                                "/api/warehouses",
                                "name",
                            ),
                            "delete_item": ("item_name", "/api/items", "nama_produk"),
                            "delete_customer": (
                                "customer_name",
                                "/api/customers",
                                "nama",
                            ),
                            "delete_vendor": ("vendor_name", "/api/vendors", "name"),
                            "delete_bank_account": (
                                "bank_name",
                                "/api/bank-accounts",
                                "account_name",
                            ),
                            "delete_warehouse": (
                                "warehouse_name",
                                "/api/warehouses",
                                "name",
                            ),
                        }
                        _wf_s_cfg = _WF_ENTITY_SEARCH.get(_wf_action_key)
                        if _wf_s_cfg:
                            _wf_name_key, _wf_api_path, _wf_db_name_key = _wf_s_cfg
                            _wf_search = (
                                _final_payload.get(_wf_name_key)
                                or _final_payload.get("name")
                                or _final_payload.get("item_name")
                                or ""
                            )
                            if _wf_search:
                                try:
                                    import httpx as _httpx

                                    _wf_auth = getattr(context, "auth_token", "") or ""
                                    _wf_headers = (
                                        {"Authorization": f"Bearer {_wf_auth}"}
                                        if _wf_auth
                                        else {}
                                    )
                                    async with _httpx.AsyncClient(
                                        base_url="http://localhost:8000", timeout=5.0
                                    ) as _wf_hc:
                                        _wf_r = await _wf_hc.get(
                                            f"{_wf_api_path}?search={_wf_search}&limit=1",
                                            headers=_wf_headers,
                                        )
                                        _wf_r.raise_for_status()
                                        _wf_d = _wf_r.json()
                                        _wf_list = (
                                            _wf_d.get("items")
                                            or _wf_d.get("data")
                                            or (
                                                _wf_d if isinstance(_wf_d, list) else []
                                            )
                                        )
                                        if _wf_list:
                                            _final_payload["id"] = _wf_list[0].get("id")
                                            logger.warning(
                                                "[PIPELINE-WF] Entity resolved: %s -> id=%s",
                                                _wf_action_key,
                                                str(_final_payload["id"])[:8],
                                            )
                                        else:
                                            await emit(
                                                "THINKING_DONE",
                                                {
                                                    "summary": "Tidak ditemukan",
                                                    "total_ms": int(
                                                        (_time.time() - start_time)
                                                        * 1000
                                                    ),
                                                },
                                            )
                                            return AgentResponse(
                                                message_type="TEXT",
                                                content=f"Maaf, saya tidak menemukan {_wf_search}. Bisa cek kembali namanya?",
                                                iterations=1,
                                                model_used="pipeline",
                                                total_latency_ms=int(
                                                    (_time.time() - start_time) * 1000
                                                ),
                                            )
                                except Exception as _wf_re:
                                    logger.warning(
                                        "[PIPELINE-WF] Entity resolution failed: %s",
                                        _wf_re,
                                    )

                    if _wf_result.auto_results and _wf_result.auto_results.get(
                        "propose_result"
                    ):
                        _propose_data = _wf_result.auto_results["propose_result"]
                    else:
                        # Normalize entity name field (Bug 3 fix)
                        if "name" not in _final_payload or not _final_payload.get(
                            "name"
                        ):
                            _final_payload["name"] = (
                                _final_payload.get("item_name")
                                or _final_payload.get("customer_name")
                                or _final_payload.get("vendor_name")
                                or _final_payload.get("entity_name")
                                or _active_crud_wf.data.get("entity_name", "")
                                or ""
                            )
                        _propose_data = await tool_executor._execute_propose_direct(
                            {
                                "action_key": _wf_action_key,
                                "payload": _final_payload,
                            }
                        )

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
                                    {
                                        "pending_action_id": _direct_data.get(
                                            "pending_action_id"
                                        )
                                    },
                                )
                            except Exception as _hook_err:
                                logger.warning(
                                    "[PIPELINE] WF state hook failed: %s", _hook_err
                                )

                        # Save pending for Edit flow
                        if tool_executor.session_manager:
                            try:
                                _save = {
                                    k: v
                                    for k, v in _final_payload.items()
                                    if v is not None
                                }
                                await tool_executor.session_manager.update_state(
                                    tool_executor.session_id,
                                    pending_payload=_save,
                                    pending_intent=extraction.intent,
                                )
                            except Exception:
                                pass

                        # Cancel workflow (propose reached = done)
                        try:
                            await _wf_engine.cancel(
                                tool_executor.session_id, "crud_form"
                            )
                        except Exception:
                            pass

                        await emit(
                            "THINKING_DONE",
                            {
                                "summary": "Data siap dikonfirmasi",
                                "total_ms": int((_time.time() - start_time) * 1000),
                            },
                        )
                        return AgentResponse(
                            message_type="DIRECT_ACTION_PREVIEW",
                            content=_propose_data.get("content", ""),
                            pending_action_id=_direct_data.get("pending_action_id", ""),
                            preview=_direct_data,
                            expires_at=_direct_data.get("expires_at", ""),
                            iterations=1,
                            tool_calls_made=[
                                {
                                    "name": "entity_extractor",
                                    "args": {"intent": extraction.intent},
                                    "success": True,
                                },
                                {
                                    "name": "propose_direct_action",
                                    "args": {"action_key": _wf_action_key},
                                    "success": True,
                                },
                            ],
                            model_used="pipeline",
                            total_latency_ms=int((_time.time() - start_time) * 1000),
                            thinking_stages=[
                                "Menganalisis pesan",
                                "Mencari data",
                                "Menyiapkan konfirmasi",
                            ],
                        )
                    else:
                        # Propose failed
                        _error_msg = _propose_data.get("error", "")
                        _error_type = _propose_data.get("error_type", "")
                        if _error_type == "VALIDATION_ERROR":
                            _val_missing = _propose_data.get(
                                "missing_fields", [str(_error_msg)]
                            )
                            clarification_text = await self._natural_clarification(
                                intent=extraction.intent,
                                collected=merged_for_wf,
                                missing_labels=_val_missing,
                                resolution=resolution,
                            )
                            await emit(
                                "THINKING_DONE",
                                {
                                    "summary": "Butuh info tambahan",
                                    "total_ms": int((_time.time() - start_time) * 1000),
                                },
                            )
                            return AgentResponse(
                                message_type="TEXT",
                                content=clarification_text,
                                iterations=1,
                                tool_calls_made=[],
                                model_used="pipeline",
                                total_latency_ms=int(
                                    (_time.time() - start_time) * 1000
                                ),
                                thinking_stages=["Menganalisis pesan", "Validasi data"],
                            )
                        else:
                            if isinstance(_error_msg, dict):
                                _error_msg = _error_msg.get("message", str(_error_msg))
                            await emit(
                                "THINKING_DONE",
                                {
                                    "summary": "Terjadi error",
                                    "total_ms": int((_time.time() - start_time) * 1000),
                                },
                            )
                            return AgentResponse(
                                message_type="TEXT",
                                content=str(_error_msg)
                                if _error_msg
                                else "Terjadi error saat menyiapkan data.",
                                iterations=1,
                                tool_calls_made=[],
                                model_used="pipeline",
                                total_latency_ms=int(
                                    (_time.time() - start_time) * 1000
                                ),
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
                            missing_labels=resolution.clarifications
                            if resolution.needs_clarification
                            else [],
                            resolution=resolution,
                        )

                    await emit(
                        "THINKING_DONE",
                        {
                            "summary": "Butuh info tambahan",
                            "total_ms": int((_time.time() - start_time) * 1000),
                        },
                    )
                    return AgentResponse(
                        message_type="TEXT",
                        content=clarification_text,
                        iterations=1,
                        tool_calls_made=[],
                        model_used="pipeline",
                        total_latency_ms=int((_time.time() - start_time) * 1000),
                        thinking_stages=["Menganalisis pesan", "Mencari data"],
                    )

                # Unexpected workflow state: log and fall through
                logger.warning(
                    "[PIPELINE] Unexpected workflow state: %s", _wf_result.new_state
                )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ── NO active workflow: original flow ──
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # ── Bank pills shortcut: if bank is the only/primary missing info, show pills ──
        # Fires for create_expense (paid_through_id) AND for receive_payment / bill_payment /
        # bank_transfer / any intent that needs bank_account_id and has it ambiguous.
        _bank_ambig = (
            "bank_account" in (resolution.resolved or {})
            and len((resolution.resolved["bank_account"]).candidates) > 1
        )
        _bank_intents = {
            "create_expense",
            "create_receive_payment",
            "create_bill_payment",
            "create_bank_transfer",
        }
        if (
            resolution.needs_clarification
            and _bank_ambig
            and extraction.intent in _bank_intents
            and resolution.payload
            and not resolution.payload.get("bank_account_id")
            and not resolution.payload.get("paid_through_id")
            and tool_executor
            and tool_executor.session_manager
            and tool_executor.session_id
        ):
            try:
                from .db_utils import get_session_db_pool as _bp_pool

                _bp_db = await _bp_pool()
                # Prefer resolver candidates (narrowed to matches, e.g. "BCA" → 4 BCA accounts)
                _resolved_bank = (resolution.resolved or {}).get("bank_account")
                if _resolved_bank and len(_resolved_bank.candidates) > 1:
                    _cand_ids = [c["id"] for c in _resolved_bank.candidates]
                    _bank_rows = await _bp_db.fetch(
                        "SELECT id, account_name, bank_name, account_number "
                        "FROM bank_accounts WHERE tenant_id = $1 AND id = ANY($2::uuid[]) "
                        "ORDER BY account_name",
                        context.tenant_id,
                        _cand_ids,
                    )
                else:
                    _bank_rows = await _bp_db.fetch(
                        "SELECT id, account_name, bank_name, account_number "
                        "FROM bank_accounts WHERE tenant_id = $1 AND is_active = true "
                        "ORDER BY account_name",
                        context.tenant_id,
                    )
                if _bank_rows:
                    _bank_options = []
                    for _br in _bank_rows:
                        _bl = _br["account_name"] or _br["bank_name"] or "Unknown"
                        if _br["account_number"]:
                            _bl += " (" + _br["account_number"][-4:] + ")"
                        _bank_options.append(
                            {"label": _bl, "value": str(_br["id"]), "description": ""}
                        )

                    # Save partial payload for re-trigger
                    _save = {
                        k: v
                        for k, v in (resolution.payload or {}).items()
                        if v is not None
                    }
                    await tool_executor.session_manager.update_state(
                        tool_executor.session_id,
                        document_context={
                            "pending_bank_selection": True,
                            "resolved_action_key": extraction.intent,
                            "resolved_payload": _save,
                        },
                    )
                    logger.warning(
                        "[PIPELINE] Bank pills shortcut: %d options for %s",
                        len(_bank_options),
                        extraction.intent,
                    )

                    _desc = (
                        resolution.payload.get("description", "")
                        or resolution.payload.get("customer_name", "")
                        or resolution.payload.get("vendor_name", "")
                    )
                    _amt = resolution.payload.get("amount", 0)
                    try:
                        _amt_fmt = "Rp {:,.0f}".format(float(_amt)).replace(",", ".")
                    except (ValueError, TypeError):
                        _amt_fmt = str(_amt)

                    _q_by_intent = {
                        "create_expense": "Dibayar dari rekening mana?",
                        "create_receive_payment": "Diterima di rekening mana?",
                        "create_bill_payment": "Dibayar dari rekening mana?",
                        "create_bank_transfer": "Dari rekening mana?",
                    }
                    _question_text = _q_by_intent.get(
                        extraction.intent, "Rekening mana?"
                    )
                    _prefix_by_intent = {
                        "create_expense": "Saya siap catat **%s** %s.",
                        "create_receive_payment": "Terima pembayaran %s dari **%s**.",
                        "create_bill_payment": "Bayar tagihan %s ke **%s**.",
                        "create_bank_transfer": "Transfer %s.",
                    }
                    _tpl = _prefix_by_intent.get(
                        extraction.intent, "Siap memproses %s %s."
                    )
                    try:
                        if extraction.intent in (
                            "create_receive_payment",
                            "create_bill_payment",
                        ):
                            _narr = _tpl % (_amt_fmt, _desc or "-")
                        else:
                            _narr = _tpl % (_desc or "-", _amt_fmt)
                    except Exception:
                        _narr = "Siap memproses."

                    await emit(
                        "THINKING_DONE",
                        {
                            "summary": "Pilih rekening",
                            "total_ms": int((_time.time() - start_time) * 1000),
                        },
                    )
                    return AgentResponse(
                        message_type="CLARIFICATION",
                        content=f"{_narr} {_question_text}",
                        iterations=1,
                        tool_calls_made=[],
                        model_used="pipeline",
                        total_latency_ms=int((_time.time() - start_time) * 1000),
                        thinking_stages=["Menganalisis pesan"],
                        extra_data={
                            "question": _question_text,
                            "options": _bank_options,
                            "allow_freetext": False,
                        },
                    )
            except Exception as _bp_err:
                logger.warning("[PIPELINE] Bank pills shortcut failed: %s", _bp_err)

        # Clarification needed? -> Natural LLM-driven question + save pending
        if (
            resolution.needs_clarification
            and not extraction.intent.startswith("update_")
            and not extraction.intent.startswith("delete_")
        ):
            # Save partial payload for next turn
            save_payload = {k: v for k, v in merged_entities.items() if v is not None}
            if resolution.payload:
                for k, v in resolution.payload.items():
                    if v is not None and k not in save_payload:
                        save_payload[k] = v

            if (
                tool_executor
                and tool_executor.session_manager
                and tool_executor.session_id
            ):
                try:
                    await tool_executor.session_manager.update_state(
                        tool_executor.session_id,
                        pending_payload=save_payload,
                        pending_intent=extraction.intent,
                    )
                    logger.warning(
                        "[PIPELINE] Saved pending: intent=%s keys=%s",
                        extraction.intent,
                        list(save_payload.keys()),
                    )
                except Exception as e:
                    logger.warning("[PIPELINE] Save pending failed: %s", e)

            # ── Create crud_form workflow for multi-turn tracking ──
            if _wf_engine and tool_executor and tool_executor.session_id:
                try:
                    await _wf_engine.process(
                        tool_executor.session_id,
                        "crud_form",
                        user_data={
                            "action_key": extraction.intent,
                            "payload": save_payload,
                            "intent": extraction.intent,
                        },
                    )
                    logger.warning(
                        "[PIPELINE] Created crud_form workflow: intent=%s",
                        extraction.intent,
                    )
                except Exception as _wf_err2:
                    logger.warning(
                        "[PIPELINE] Workflow create failed (non-fatal): %s", _wf_err2
                    )
            elif tool_executor and tool_executor.session_id and not _wf_engine:
                try:
                    from .workflow_engine import WorkflowEngine
                    from .db_utils import get_session_db_pool as _wf_pool2

                    _wf_db2 = await _wf_pool2()
                    _wf_engine_new = WorkflowEngine(
                        _wf_db2,
                        context.tenant_id,
                        getattr(context, "user_id", ""),
                        getattr(context, "auth_token", ""),
                    )
                    await _wf_engine_new.process(
                        tool_executor.session_id,
                        "crud_form",
                        user_data={
                            "action_key": extraction.intent,
                            "payload": save_payload,
                            "intent": extraction.intent,
                        },
                    )
                    logger.warning(
                        "[PIPELINE] Created crud_form workflow (new engine): intent=%s",
                        extraction.intent,
                    )
                except Exception as _wf_err2:
                    logger.warning(
                        "[PIPELINE] Workflow create failed (non-fatal): %s", _wf_err2
                    )

            # Build natural clarification via LLM
            clarification_text = await self._natural_clarification(
                intent=extraction.intent,
                collected=merged_entities,
                missing_labels=resolution.clarifications,
                resolution=resolution,
            )

            await emit(
                "THINKING_DONE",
                {
                    "summary": "Butuh info tambahan",
                    "total_ms": int((_time.time() - start_time) * 1000),
                },
            )
            return AgentResponse(
                message_type="TEXT",
                content=clarification_text,
                iterations=1,
                tool_calls_made=[
                    {
                        "name": "entity_extractor",
                        "args": {"user_text": user_text},
                        "success": True,
                        "latency_ms": int((_time.time() - start_time) * 1000),
                    }
                ],
                model_used="pipeline",
                total_latency_ms=int((_time.time() - start_time) * 1000),
                thinking_stages=["Menganalisis pesan", "Mencari data"],
            )

        # Propose via existing _execute_propose_direct
        await emit(
            "THINKING_STEP",
            {
                "step_id": "pipeline-propose",
                "text": "Menyiapkan konfirmasi",
                "status": "running",
                "category": "write",
            },
        )

        # ── Entity Resolution for update_*/delete_* intents ──
        if (
            extraction.intent.startswith("update_")
            or extraction.intent.startswith("delete_")
        ) and (
            "id" not in merged_entities or len(str(merged_entities.get("id", ""))) < 30
        ):
            _ENTITY_SEARCH = {
                "update_item": ("item_name", "/api/items", "nama_produk"),
                "update_customer": ("customer_name", "/api/customers", "nama"),
                "update_vendor": ("vendor_name", "/api/vendors", "name"),
                "update_bank_account": (
                    "bank_name",
                    "/api/bank-accounts",
                    "account_name",
                ),
                "update_warehouse": ("warehouse_name", "/api/warehouses", "name"),
                "delete_item": ("item_name", "/api/items", "nama_produk"),
                "delete_customer": ("customer_name", "/api/customers", "nama"),
                "delete_vendor": ("vendor_name", "/api/vendors", "name"),
                "delete_bank_account": (
                    "bank_name",
                    "/api/bank-accounts",
                    "account_name",
                ),
                "delete_warehouse": ("warehouse_name", "/api/warehouses", "name"),
            }
            _s_cfg = _ENTITY_SEARCH.get(extraction.intent)
            if _s_cfg:
                _name_key, _api_path, _db_name_key = _s_cfg
                _search_name = (
                    merged_entities.get(_name_key)
                    or merged_entities.get("name")
                    or merged_entities.get("item_name")
                    or ""
                )
                if _search_name:
                    try:
                        import httpx as _httpx

                        _auth = getattr(context, "auth_token", "") or ""
                        _headers = {"Authorization": f"Bearer {_auth}"} if _auth else {}
                        async with _httpx.AsyncClient(
                            base_url="http://localhost:8000", timeout=5.0
                        ) as _hc:
                            _r = await _hc.get(
                                f"{_api_path}?search={_search_name}&limit=5",
                                headers=_headers,
                            )
                            _r.raise_for_status()
                            _d = _r.json()
                            _list = (
                                _d.get("items")
                                or _d.get("data")
                                or (_d if isinstance(_d, list) else [])
                            )
                            if len(_list) == 1:
                                _found = _list[0]
                                merged_entities["id"] = _found.get("id")
                                merged_entities.setdefault(
                                    "name",
                                    _found.get(_db_name_key)
                                    or _found.get("name")
                                    or _found.get("nama_produk")
                                    or _search_name,
                                )
                                extraction.entities = merged_entities
                                logger.warning(
                                    "[PIPELINE] Entity resolved: %s -> id=%s",
                                    extraction.intent,
                                    str(merged_entities["id"])[:8],
                                )
                            elif len(_list) > 1:
                                # Multiple matches — save candidates + ask user to pick
                                _candidates_display = []
                                _candidates_data = []
                                for _ci, _c in enumerate(_list, 1):
                                    _cname = (
                                        _c.get(_db_name_key)
                                        or _c.get("name")
                                        or _c.get("nama_produk")
                                        or _c.get("nama")
                                        or "?"
                                    )
                                    _cdetail = ""
                                    if _c.get("item_code"):
                                        _cdetail += f" ({_c['item_code']})"
                                    if _c.get("item_type"):
                                        _cdetail += f" — {_c['item_type']}"
                                    _candidates_display.append(
                                        f"{_ci}. {_cname}{_cdetail}"
                                    )
                                    _candidates_data.append(
                                        {
                                            "id": _c.get("id"),
                                            "name": _cname,
                                            "code": _c.get("item_code", ""),
                                        }
                                    )
                                _pick_text = (
                                    f'Ada {len(_list)} hasil untuk "{_search_name}":\n'
                                    + "\n".join(_candidates_display)
                                    + "\n\nYang mana yang kamu maksud?"
                                )

                                # Save candidates in workflow for next turn resolution
                                if (
                                    _wf_engine
                                    and tool_executor
                                    and tool_executor.session_id
                                ):
                                    try:
                                        await _wf_engine.process(
                                            tool_executor.session_id,
                                            "crud_form",
                                            user_data={
                                                "action_key": extraction.intent,
                                                "intent": extraction.intent,
                                                "payload": {},
                                                "candidates": _candidates_data,
                                                "phase": "picking_candidate",
                                            },
                                        )
                                        logger.warning(
                                            "[PIPELINE] Saved %d candidates in workflow",
                                            len(_candidates_data),
                                        )
                                    except Exception as _wf_e:
                                        logger.warning(
                                            "[PIPELINE] Save candidates failed: %s",
                                            _wf_e,
                                        )

                                await emit(
                                    "THINKING_DONE",
                                    {
                                        "summary": "Beberapa ditemukan",
                                        "total_ms": int(
                                            (_time.time() - start_time) * 1000
                                        ),
                                    },
                                )
                                return AgentResponse(
                                    message_type="TEXT",
                                    content=_pick_text,
                                    iterations=1,
                                    model_used="pipeline",
                                    total_latency_ms=int(
                                        (_time.time() - start_time) * 1000
                                    ),
                                    thinking_stages=[
                                        "Menganalisis pesan",
                                        "Mencari data",
                                    ],
                                )
                            else:
                                await emit(
                                    "THINKING_DONE",
                                    {
                                        "summary": "Tidak ditemukan",
                                        "total_ms": int(
                                            (_time.time() - start_time) * 1000
                                        ),
                                    },
                                )
                                return AgentResponse(
                                    message_type="TEXT",
                                    content=f"Maaf, saya tidak menemukan '{_search_name}'. Bisa cek kembali namanya?",
                                    iterations=1,
                                    model_used="pipeline",
                                    total_latency_ms=int(
                                        (_time.time() - start_time) * 1000
                                    ),
                                )
                    except Exception as _re:
                        logger.warning("[PIPELINE] Entity resolution failed: %s", _re)

        # ── UPDATE FLOW: Show current data + ask what to change ──
        if extraction.intent.startswith("update_") and not _active_crud_wf:
            _entity_type = extraction.intent.replace("update_", "")
            _entity_id = merged_entities.get("id") or (resolution.payload or {}).get(
                "id"
            )

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
                                _current_data = (
                                    _rj.get("data", _rj)
                                    if isinstance(_rj, dict)
                                    else _rj
                                )
                    except Exception as _e:
                        logger.warning("[PIPELINE] Fetch current data failed: %s", _e)

                    if _current_data:
                        _display_data = self._compact_current_data(
                            _entity_type, _current_data
                        )
                        _entity_name = (
                            _current_data.get("name")
                            or _current_data.get("nama_produk")
                            or _current_data.get("nama")
                            or _current_data.get("account_name")
                            or "item"
                        )

                        # ── FAST PATH: if user already provided field changes, skip "mau ubah?" ──
                        _id_fields = {
                            "id",
                            "item_name",
                            "customer_name",
                            "vendor_name",
                            "warehouse_name",
                            "bank_name",
                            "name",
                            "date",
                            "entity_name",
                        }
                        _change_fields = {
                            k: v
                            for k, v in merged_entities.items()
                            if k not in _id_fields and v is not None and v != ""
                        }
                        if _change_fields:
                            # User said "edit X, harga jual 43000" — go straight to propose
                            _fast_payload = {
                                "id": _entity_id,
                                "name": _entity_name,
                                **_change_fields,
                            }
                            logger.warning(
                                "[PIPELINE] Update fast path: %s changes=%s",
                                extraction.intent,
                                list(_change_fields.keys()),
                            )

                            # Normalize name
                            if "name" not in _fast_payload or not _fast_payload.get(
                                "name"
                            ):
                                _fast_payload["name"] = _entity_name

                            propose_result = (
                                await tool_executor._execute_propose_direct(
                                    {
                                        "action_key": extraction.intent,
                                        "payload": _fast_payload,
                                    }
                                )
                            )

                            if (
                                propose_result.get("message_type")
                                == "DIRECT_ACTION_PREVIEW"
                            ):
                                _direct_data = propose_result.get("data", {})
                                await emit(
                                    "THINKING_DONE",
                                    {
                                        "summary": "Data siap dikonfirmasi",
                                        "total_ms": int(
                                            (_time.time() - start_time) * 1000
                                        ),
                                    },
                                )
                                return AgentResponse(
                                    message_type="DIRECT_ACTION_PREVIEW",
                                    content=propose_result.get("content", ""),
                                    pending_action_id=_direct_data.get(
                                        "pending_action_id", ""
                                    ),
                                    preview=_direct_data,
                                    expires_at=_direct_data.get("expires_at", ""),
                                    iterations=1,
                                    tool_calls_made=[],
                                    model_used="pipeline",
                                    total_latency_ms=int(
                                        (_time.time() - start_time) * 1000
                                    ),
                                    thinking_stages=[
                                        "Menganalisis pesan",
                                        "Mencari data",
                                        "Menyiapkan konfirmasi",
                                    ],
                                )

                        # Create workflow with current data
                        if _wf_engine and tool_executor and tool_executor.session_id:
                            try:
                                await _wf_engine.process(
                                    tool_executor.session_id,
                                    "crud_form",
                                    user_data={
                                        "action_key": extraction.intent,
                                        "payload": {"id": _entity_id},
                                        "intent": extraction.intent,
                                        "current_data": _display_data,
                                        "entity_name": _entity_name,
                                        "phase": "showing_current",
                                    },
                                )
                                logger.warning(
                                    "[PIPELINE] Created update workflow: %s phase=showing_current",
                                    extraction.intent,
                                )
                            except Exception as _e:
                                logger.warning(
                                    "[PIPELINE] Create update workflow failed: %s", _e
                                )

                        # Also save pending state for session continuity
                        if (
                            tool_executor
                            and tool_executor.session_manager
                            and tool_executor.session_id
                        ):
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

                        await emit(
                            "THINKING_DONE",
                            {
                                "summary": "Data ditemukan",
                                "total_ms": int((_time.time() - start_time) * 1000),
                            },
                        )

                        return AgentResponse(
                            message_type="TEXT",
                            content=_show_text,
                            iterations=1,
                            model_used="pipeline",
                            total_latency_ms=int((_time.time() - start_time) * 1000),
                            thinking_stages=["Menganalisis pesan", "Mencari data"],
                        )
            else:
                # No entity ID resolved — item not found
                _search_name = (
                    merged_entities.get("item_name")
                    or merged_entities.get("name")
                    or ""
                )
                await emit(
                    "THINKING_DONE",
                    {
                        "summary": "Tidak ditemukan",
                        "total_ms": int((_time.time() - start_time) * 1000),
                    },
                )
                return AgentResponse(
                    message_type="TEXT",
                    content=f"Maaf, saya tidak menemukan '{_search_name}'. Bisa cek kembali namanya?",
                    iterations=1,
                    model_used="pipeline",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Normalize entity name field for propose (Bug 3 fix)
        _propose_payload = {
            **resolution.payload,
            **{k: v for k, v in merged_entities.items() if v is not None},
        }
        if "name" not in _propose_payload or not _propose_payload.get("name"):
            _propose_payload["name"] = (
                _propose_payload.get("item_name")
                or _propose_payload.get("customer_name")
                or _propose_payload.get("vendor_name")
                or _propose_payload.get("warehouse_name")
                or _propose_payload.get("bank_name")
                or _propose_payload.get("account_name")
                or ""
            )

        propose_result = await tool_executor._execute_propose_direct(
            {
                "action_key": extraction.intent,
                "payload": _propose_payload,
            }
        )

        await emit(
            "THINKING_STEP",
            {
                "step_id": "pipeline-propose",
                "text": "Menyiapkan konfirmasi",
                "status": "done",
                "duration_ms": int((_time.time() - start_time) * 1000),
                "category": "write",
            },
        )

        # Return in existing format
        if propose_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
            direct_data = propose_result.get("data", {})
            await emit(
                "THINKING_DONE",
                {
                    "summary": "Data siap dikonfirmasi",
                    "total_ms": int((_time.time() - start_time) * 1000),
                },
            )

            # Fire L2 + L3 hooks
            if (
                tool_executor
                and tool_executor.session_manager
                and tool_executor.session_id
            ):
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
            if (
                tool_executor
                and tool_executor.session_manager
                and tool_executor.session_id
            ):
                try:
                    _save = {
                        k: v for k, v in resolution.payload.items() if v is not None
                    }
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
                    {
                        "name": "entity_extractor",
                        "args": {"intent": extraction.intent},
                        "success": True,
                    },
                    {
                        "name": "entity_resolver",
                        "args": list(extraction.entities.keys()),
                        "success": True,
                    },
                    {
                        "name": "propose_direct_action",
                        "args": {"action_key": extraction.intent},
                        "success": True,
                    },
                ],
                model_used="pipeline",
                total_latency_ms=int((_time.time() - start_time) * 1000),
                thinking_stages=[
                    "Menganalisis pesan",
                    "Mencari data",
                    "Menyiapkan konfirmasi",
                ],
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
            if (
                tool_executor
                and tool_executor.session_manager
                and tool_executor.session_id
            ):
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
                        tool_executor.session_id,
                        "crud_form",
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

            await emit(
                "THINKING_DONE",
                {
                    "summary": "Butuh info tambahan",
                    "total_ms": int((_time.time() - start_time) * 1000),
                },
            )

            return AgentResponse(
                message_type="TEXT",
                content=clarification,
                iterations=1,
                model_used="pipeline",
                total_latency_ms=int((_time.time() - start_time) * 1000),
                thinking_stages=["Menganalisis pesan", "Validasi data"],
            )

        # Non-validation error — show as-is
        if isinstance(error_msg, dict):
            error_msg = error_msg.get("message", str(error_msg))
        elif not isinstance(error_msg, str):
            error_msg = str(propose_result)

        await emit(
            "THINKING_DONE",
            {
                "summary": "Terjadi error",
                "total_ms": int((_time.time() - start_time) * 1000),
            },
        )

        return AgentResponse(
            message_type="TEXT",
            content=str(error_msg),
            iterations=1,
            model_used="pipeline",
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
        pattern_suggestions: list = None,
    ) -> str:
        """Generate natural conversational clarification using LLM."""
        import json as _json

        collected_clean = {
            k: v for k, v in collected.items() if v is not None and k != "date"
        }
        _field_labels = {
            "name": "nama",
            "item_name": "nama produk",
            "customer_name": "pelanggan",
            "vendor_name": "vendor",
            "bank_name": "bank",
            "warehouse_name": "gudang",
            "item_type": "tipe",
            "base_unit": "satuan (pcs, kg, box, dll)",
            "amount": "jumlah",
            "quantity": "qty",
            "unit_price": "harga satuan",
            "description": "deskripsi",
            "phone": "telepon",
            "email": "email",
            "address": "alamat",
            "reason": "alasan",
            "payment_method": "metode bayar",
            "account_type": "tipe akun",
        }
        collected_display = {
            _field_labels.get(k, k): v
            for k, v in collected_clean.items()
            if k in _field_labels
        }

        # Map API values to Indonesian display labels
        _VALUE_DISPLAY = {
            "item_type": {
                "goods": "persediaan",
                "service": "jasa",
                "non_inventory": "non-persediaan",
            },
        }
        for field_key, val_map in _VALUE_DISPLAY.items():
            display_key = _field_labels.get(field_key, field_key)
            if display_key in collected_display and isinstance(
                collected_display[display_key], str
            ):
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
                                fs = next(
                                    (f for f in config.fields if f.name == fn), None
                                )
                                if fs:
                                    field_hints[fn] = fs.label + " (minimal salah satu)"

        # Get action description from registry (scalable — no hardcoded dict)
        _action_desc = "membuat data baru"
        if config:
            _action_desc = config.display_name.lower()

        system_prompt = (
            "Kamu asisten pembukuan. User BARU SAJA minta "
            + _action_desc
            + " via chat.\n"
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
                "User minta: " + _action_desc + "\n\n" + override_instruction + "\n\n"
                "Balas user secara natural, singkat (1-2 kalimat)."
            )
        else:
            # Phase B.1: Pattern suggestion hint
            _pattern_hint = ""
            if pattern_suggestions and not override_instruction:
                _entity_missing = any(
                    k in field_hints
                    for k in (
                        "customer_name",
                        "vendor_name",
                        "customer_id",
                        "vendor_id",
                    )
                )
                if _entity_missing:
                    _hints = []
                    for p in pattern_suggestions[:3]:
                        _hints.append(f"{p['entity_name']} ({p['usage_count']}x)")
                    if _hints:
                        _pattern_hint = (
                            "\nPATTERN SUGGESTION: user sering bertransaksi dengan: "
                            + ", ".join(_hints)
                            + ". Tawarkan sebagai opsi (tanya, JANGAN assume). "
                            'Contoh: "Untuk siapa? Biasanya [nama] ([count]x)."\n'
                        )

            user_prompt = (
                "User minta: " + _action_desc + "\n"
                "Data yang sudah ditangkap: "
                + _json.dumps(collected_display, ensure_ascii=False)
                + "\n"
                "Field WAJIB yang masih kurang: "
                + _json.dumps(field_hints, ensure_ascii=False)
                + "\n"
                + _pattern_hint
                + "Balas user secara natural."
            )

        try:
            response = await self.router.complete(
                task_type="clarification",
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ],
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
            collected_str = ", ".join(
                str(k) + ": " + str(v) for k, v in list(collected_display.items())[:3]
            )
            return "Oke, " + collected_str + ". Masih butuh: " + missing_str
        return "Untuk lanjut, saya butuh: " + missing_str

    # ═══ Response Entity Context (REC) ═══

    _INTENT_DOMAIN_MAP = {
        "query_ar_outstanding": "ar",
        "query_ar_invoices": "ar",
        "query_customer_ar": "ar",
        "query_sales_invoices_list": "ar",
        "query_sales_invoice_detail": "ar",
        "query_sales_invoices_overdue": "ar",
        "query_sales_invoices_unpaid": "ar",
        "query_ap_outstanding": "ap",
        "query_bills_list": "ap",
        "query_bill_detail": "ap",
        "query_bills_overdue": "ap",
        "query_bills_unpaid": "ap",
        "query_vendor_ap": "ap",
        "query_customer_detail": "customer",
        "query_customers_list": "customer",
        "query_customers_summary": "customer",
        "query_customers_with_overdue": "customer",
        "query_vendor_detail": "vendor",
        "query_vendors_list": "vendor",
        "query_vendors_summary": "vendor",
        "query_vendors_with_overdue": "vendor",
        "query_item_detail": "items",
        "query_items_list": "items",
        "query_items_summary": "items",
        "query_items_low_stock": "items",
        "query_items_search": "items",
        "query_items_by_price": "items",
        "query_items_by_stock": "items",
        "query_bank_accounts_list": "bank",
        "query_bank_account_detail": "bank",
        "query_bank_account_balance": "bank",
        "query_bank_transactions": "bank",
        "query_expenses_list": "expense",
        "query_expense_detail": "expense",
        "query_expenses_summary": "expense",
        "query_receive_payments_list": "ar",
        "query_bill_payments_list": "ap",
        "query_journals_list": "journal",
        "query_journal_detail": "journal",
        "query_accounts_list": "coa",
        "query_account_detail": "coa",
    }  # noqa: E701,E702

    _NAME_KEYS = [
        "customer_name",  # noqa: E701,E702
        "vendor_name",
        "nama",
        "name",  # noqa: E701,E702
        "item_name",  # noqa: E701,E702
        "account_name",
        "bank_name",
        "display_name",
    ]  # noqa: E701,E702
    _ID_KEYS = ["id", "uuid"]
    _ENTITY_ID_KEYS = ["customer_id", "vendor_id", "item_id", "account_id"]
    _AMOUNT_KEYS = [
        "outstanding",
        "total_amount",
        "amount",
        "amount_due",
        "harga_jual",
        "sales_price",
        "balance",
        "saldo",
        "current_balance",
        "ap_balance",
        "ar_balance",
        "outstanding_balance",
    ]
    _REF_KEYS = [
        "invoice_number",
        "bill_number",
        "journal_number",
        "reference_number",
        "payment_number",
    ]

    def _extract_rec(self, intent: str, data) -> dict:
        """Extract Response Entity Context from API response. Pure code, 0ms."""
        domain = self._INTENT_DOMAIN_MAP.get(intent)
        if not domain:
            return None

        # Find items list in response
        items_raw = []
        if isinstance(data, list):
            items_raw = data
        elif isinstance(data, dict):
            for key in (
                "items",
                "data",
                "results",
                "invoices",
                "bills",
                "customers",
                "vendors",
                "payments",
                "accounts",
            ):
                if isinstance(data.get(key), list):
                    items_raw = data[key]
                    break
            if not items_raw and not any(isinstance(v, list) for v in data.values()):
                items_raw = [data]

        if not items_raw:
            return None

        # Extract essential fields (generic, max 10, preserve order)
        items = []
        for raw in items_raw[:10]:
            if not isinstance(raw, dict):
                continue
            item = {}
            for k in self._NAME_KEYS:
                if raw.get(k):
                    item["_name"] = str(raw[k])
                    break
            for k in self._ID_KEYS:
                if raw.get(k):
                    item["_id"] = str(raw[k])
                    break
            # Also store entity ID (customer_id/vendor_id) separately for pronoun resolution
            for k in self._ENTITY_ID_KEYS:
                if raw.get(k):
                    item["_entity_id"] = str(raw[k])
                    item["_entity_id_key"] = k  # e.g. "customer_id"
                    break
            for k in self._AMOUNT_KEYS:
                if raw.get(k) is not None:
                    try:
                        item["_amount"] = float(raw[k])
                    except (ValueError, TypeError):
                        pass
                    break
            for k in self._REF_KEYS:
                if raw.get(k):
                    item["_ref"] = str(raw[k])
                    break
            if raw.get("status"):
                item["_status"] = raw["status"]
            if item.get("_name") or item.get("_ref"):
                items.append(item)

        if not items:
            return None

        # Primary entity = most frequent name
        from collections import Counter

        names = [i["_name"] for i in items if i.get("_name")]
        primary_entity = None
        if names:
            primary_name = Counter(names).most_common(1)[0][0]
            # Use _entity_id (customer_id/vendor_id) for primary entity, fall back to _id
            primary_id = next(
                (
                    i.get("_entity_id") or i.get("_id")
                    for i in items
                    if i.get("_name") == primary_name
                    and (i.get("_entity_id") or i.get("_id"))
                ),
                None,
            )
            _type_map = {
                "ar": "customer",
                "ap": "vendor",
                "customer": "customer",
                "vendor": "vendor",
                "items": "item",
                "bank": "bank_account",
                "expense": "expense",
                "journal": "journal",
                "coa": "account",
            }
            primary_entity = {
                "type": _type_map.get(domain, "unknown"),
                "name": primary_name,
                "id": primary_id,
            }

        # Numeric summary
        amounts = [i["_amount"] for i in items if i.get("_amount") is not None]
        numeric = None
        if amounts:
            numeric = {
                "total": sum(amounts),
                "count": len(amounts),
                "max": max(amounts),
                "min": min(amounts),
            }

        return {
            "domain": domain,
            "items": items,
            "primary_entity": primary_entity,
            "numeric": numeric,
        }

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
        import re as _re_dd
        import httpx

        start_time = _time.time()

        # Bug #1.5: AR/AP drill-down redirect — when user asks for invoice/bill
        # detail with an entity name, query_customer_ar / query_vendor_ap returns
        # only summary. Redirect to *_list with customer_id/vendor_id filter so
        # the response includes per-faktur breakdown.
        _bug15_redirect = None  # "customer" | "vendor" | None
        try:
            _t_dd = (user_text or "").lower()
            _drill_signals = (
                r"(?:minta|kasih|tampilkan|lihat|cek|info|data|daftar|list)\s+"
                r"(?:data\s+)?(?:faktur|tagihan|invoice|bill)(?:nya)?"
                r"|(?:faktur|tagihan|invoice|bill)(?:nya)?\s+"
                r"(?:apa(?:\s+(?:saja|aja|sih))?|mana|yang\s+mana)"
                r"|(?:penjualan|pembelian|transaksi)\s+"
                r"(?:apa(?:\s+(?:saja|aja|sih))?|mana|yang\s+mana)"
                r"|\b(?:rincian|rinci|breakdown|detailnya|rinciannya|per\s+faktur)\b"
            )
            if extraction.intent in (
                "query_customer_ar",
                "query_vendor_ap",
            ) and _re_dd.search(_drill_signals, _t_dd):
                _orig = extraction.intent
                if extraction.intent == "query_customer_ar":
                    extraction.intent = "query_sales_invoices_list"
                    _bug15_redirect = "customer"
                else:
                    extraction.intent = "query_bills_list"
                    _bug15_redirect = "vendor"
                extraction.confidence = 1.0
                extraction.needs_escalation = False
                logger.warning(
                    "[BUG15_DRILL_REDIRECT] %s -> %s user='%s'",
                    _orig,
                    extraction.intent,
                    user_text[:80],
                )
        except Exception as _dd_err:
            logger.warning("[BUG15_DRILL_REDIRECT] error: %s", _dd_err)

        async def emit(event_type, data):
            if event_callback:
                try:
                    await event_callback(event_type, data)
                except Exception:
                    pass

        await emit(
            "THINKING_STEP",
            {
                "step_id": "query-resolve",
                "text": "Mencari data",
                "status": "running",
                "category": "search",
            },
        )

        # Get query config from registry
        from .direct_action_registry import get_query_action

        query_config = get_query_action(extraction.intent)
        if not query_config:
            return AgentResponse(
                message_type="TEXT",
                content='Hmm, saya belum paham pertanyaannya. Coba lebih spesifik, misalnya:\n\n\u2022 "Berapa total piutang?"\n\u2022 "Daftar faktur pembelian"\n\u2022 "Saldo kas dan bank"\n',
                iterations=1,
                model_used="gpt-4o-mini",
                total_latency_ms=int((_time.time() - start_time) * 1000),
            )

        # Resolve entities for parameterized endpoints
        endpoint = query_config.rest_endpoint
        query_params = {}

        # Default: exclude draft & void for bills/invoices lists (hutang/piutang accuracy)
        if query_config.action_key in ("query_bills_list", "query_sales_invoices_list"):
            query_params["status"] = "active"

        # Bug #1.5: when redirected from customer_ar / vendor_ap, resolve entity
        # name to ID and inject as query param so the list filters to that entity.
        if _bug15_redirect == "customer":
            _cname_dd = extraction.entities.get(
                "customer_name"
            ) or extraction.entities.get("name")
            if _cname_dd:
                try:
                    from .entity_resolver import EntityResolver
                    from .db_utils import get_session_db_pool

                    _pool_dd = await get_session_db_pool()
                    _res_dd = EntityResolver(_pool_dd, context.tenant_id)
                    _r_c = await _res_dd._resolve_customer(_cname_dd)
                    if _r_c and _r_c.entity_id and _r_c.confidence >= 0.5:
                        query_params["customer_id"] = _r_c.entity_id
                        logger.warning(
                            "[BUG15_DRILL_REDIRECT] resolved customer_name=%s -> id=%s",
                            _cname_dd,
                            _r_c.entity_id,
                        )
                except Exception as _r_err:
                    logger.warning(
                        "[BUG15_DRILL_REDIRECT] customer resolve failed: %s", _r_err
                    )
        elif _bug15_redirect == "vendor":
            _vname_dd = extraction.entities.get(
                "vendor_name"
            ) or extraction.entities.get("name")
            if _vname_dd:
                try:
                    from .entity_resolver import EntityResolver
                    from .db_utils import get_session_db_pool

                    _pool_dd = await get_session_db_pool()
                    _res_dd = EntityResolver(_pool_dd, context.tenant_id)
                    _r_v = await _res_dd._resolve_vendor(_vname_dd)
                    if _r_v and _r_v.entity_id and _r_v.confidence >= 0.5:
                        query_params["vendor_id"] = _r_v.entity_id
                        logger.warning(
                            "[BUG15_DRILL_REDIRECT] resolved vendor_name=%s -> id=%s",
                            _vname_dd,
                            _r_v.entity_id,
                        )
                except Exception as _r_err:
                    logger.warning(
                        "[BUG15_DRILL_REDIRECT] vendor resolve failed: %s", _r_err
                    )

        # Resolve item by name -> get ID for {id} endpoints
        if "{id}" in endpoint and extraction.entities.get("item_name"):
            from .entity_resolver import EntityResolver
            from .db_utils import get_session_db_pool

            pool = await get_session_db_pool()
            resolver = EntityResolver(pool, context.tenant_id)
            resolved_item = await resolver._resolve_item(
                extraction.entities["item_name"]
            )
            if (
                resolved_item
                and resolved_item.entity_id
                and resolved_item.confidence >= 0.5
            ):
                endpoint = endpoint.replace("{id}", resolved_item.entity_id)
            else:
                # Try entity graph focus
                state = None
                if (
                    tool_executor
                    and tool_executor.session_manager
                    and tool_executor.session_id
                ):
                    try:
                        state = await tool_executor.session_manager.get_state(
                            tool_executor.session_id
                        )
                    except Exception:
                        pass
                if state and getattr(state, "entity_graph", None):
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
            resolved_wh = await resolver._resolve_warehouse(
                extraction.entities["warehouse_name"]
            )
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

        # Resolve customer by name -> get ID for {id} endpoints
        # Skip for non-customer detail endpoints (e.g. query_sales_invoice_detail needs invoice ID, not customer ID)
        _CUSTOMER_RESOLVE_INTENTS = {
            "query_customer_detail",
            "query_customer_ar",
            "query_customer_balance",
        }
        if (
            "{id}" in endpoint
            and (
                extraction.entities.get("customer_name")
                or extraction.entities.get("name")
            )
            and (
                not extraction.intent or extraction.intent in _CUSTOMER_RESOLVE_INTENTS
            )
        ):
            _cname = extraction.entities.get(
                "customer_name"
            ) or extraction.entities.get("name")
            from .entity_resolver import EntityResolver
            from .db_utils import get_session_db_pool

            pool = await get_session_db_pool()
            resolver = EntityResolver(pool, context.tenant_id)
            resolved_c = await resolver._resolve_customer(_cname)
            if resolved_c and resolved_c.entity_id and resolved_c.confidence >= 0.5:
                endpoint = endpoint.replace("{id}", resolved_c.entity_id)
            else:
                return AgentResponse(
                    message_type="TEXT",
                    content=f"Pelanggan '{_cname}' tidak ditemukan.",
                    iterations=1,
                    model_used="pipeline",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Resolve vendor by name -> get ID for {id} endpoints
        _VENDOR_RESOLVE_INTENTS = {
            "query_vendor_detail",
            "query_vendor_ap",
            "query_vendor_balance",
        }
        if (
            "{id}" in endpoint
            and extraction.entities.get("vendor_name")
            and (not extraction.intent or extraction.intent in _VENDOR_RESOLVE_INTENTS)
        ):
            from .entity_resolver import EntityResolver
            from .db_utils import get_session_db_pool

            pool = await get_session_db_pool()
            resolver = EntityResolver(pool, context.tenant_id)
            resolved_v = await resolver._resolve_vendor(
                extraction.entities["vendor_name"]
            )
            if resolved_v and resolved_v.entity_id and resolved_v.confidence >= 0.5:
                endpoint = endpoint.replace("{id}", resolved_v.entity_id)
            else:
                return AgentResponse(
                    message_type="TEXT",
                    content=f"Vendor '{extraction.entities['vendor_name']}' tidak ditemukan.",
                    iterations=1,
                    model_used="pipeline",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Resolve bank account by name -> get ID for {id} endpoints (e.g. "saldo BCA")
        # Fallback: extract bank name from user text if LLM did not populate bank_name
        if (
            "{id}" in endpoint
            and extraction.intent == "query_bank_account_balance"
            and not extraction.entities.get("bank_name")
        ):
            import re as _bk_re

            _bk_match = _bk_re.search(r"saldo\s+(.+)", user_text.strip().lower())
            if _bk_match:
                _bk_name = _bk_match.group(1).strip()
                _bk_name = _bk_re.sub(
                    r"\s+(saya|kita|ku|gue|aku|dong|ya)$", "", _bk_name
                )
                if _bk_name:
                    extraction.entities["bank_name"] = _bk_name

        if "{id}" in endpoint and extraction.entities.get("bank_name"):
            from .entity_resolver import EntityResolver
            from .db_utils import get_session_db_pool

            pool = await get_session_db_pool()
            resolver = EntityResolver(pool, context.tenant_id)
            resolved_bank = await resolver._resolve_bank_account(
                extraction.entities["bank_name"]
            )
            if (
                resolved_bank
                and resolved_bank.entity_id
                and resolved_bank.confidence >= 0.5
            ):
                endpoint = endpoint.replace("{id}", resolved_bank.entity_id)
            else:
                bank_name = extraction.entities.get("bank_name", "")
                return AgentResponse(
                    message_type="TEXT",
                    content=f"Rekening {bank_name} tidak ditemukan.",
                    iterations=1,
                    model_used="gpt-4o-mini",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Resolve work order by number -> get ID for {id} endpoints
        _WO_RESOLVE_INTENTS = {
            "query_work_order_detail",
            "query_work_order_cost_analysis",
        }
        if "{id}" in endpoint and extraction.intent in _WO_RESOLVE_INTENTS:
            _wo_name = extraction.entities.get("name") or extraction.entities.get(
                "work_order_number"
            )
            if _wo_name:
                from .db_utils import get_session_db_pool

                _wo_pool = await get_session_db_pool()
                logger.warning("[MFG_RESOLVE_WO] Looking up: %s", _wo_name)
                _wo_rows = await _wo_pool.fetch(
                    "SELECT id::text, order_number FROM production_orders "
                    "WHERE tenant_id = $1 AND order_number ILIKE $2 LIMIT 1",
                    context.tenant_id,
                    str(_wo_name).strip(),
                )
                if not _wo_rows:
                    _wo_rows = await _wo_pool.fetch(
                        "SELECT id::text, order_number FROM production_orders "
                        "WHERE tenant_id = $1 AND order_number ILIKE $2 "
                        "ORDER BY created_at DESC LIMIT 1",
                        context.tenant_id,
                        f"%{str(_wo_name).strip()}%",
                    )
                if _wo_rows:
                    endpoint = endpoint.replace("{id}", _wo_rows[0]["id"])
                    logger.warning(
                        "[MFG_RESOLVE_WO] Found: %s -> %s", _wo_name, _wo_rows[0]["id"]
                    )
                else:
                    return AgentResponse(
                        message_type="TEXT",
                        content=f"Work order '{_wo_name}' tidak ditemukan.",
                        iterations=1,
                        model_used="pipeline",
                        total_latency_ms=int((_time.time() - start_time) * 1000),
                    )
            else:
                return AgentResponse(
                    message_type="TEXT",
                    content="Mohon sebutkan nomor work order, contoh: WO-2026-000001.",
                    iterations=1,
                    model_used="pipeline",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Resolve BOM by code -> get ID for {id} endpoints
        _BOM_RESOLVE_INTENTS = {
            "query_bom_detail",
            "query_bom_cost_breakdown",
            "query_bom_materials_required",
        }
        if "{id}" in endpoint and extraction.intent in _BOM_RESOLVE_INTENTS:
            _bom_name = extraction.entities.get("name") or extraction.entities.get(
                "bom_code"
            )
            if _bom_name:
                from .db_utils import get_session_db_pool

                _bom_pool = await get_session_db_pool()
                logger.warning("[MFG_RESOLVE_BOM] Looking up: %s", _bom_name)
                _bom_rows = await _bom_pool.fetch(
                    "SELECT id::text, bom_code FROM bill_of_materials "
                    "WHERE tenant_id = $1 AND (bom_code ILIKE $2 OR bom_name ILIKE $2) LIMIT 1",
                    context.tenant_id,
                    str(_bom_name).strip(),
                )
                if not _bom_rows:
                    _bom_rows = await _bom_pool.fetch(
                        "SELECT id::text, bom_code FROM bill_of_materials "
                        "WHERE tenant_id = $1 AND (bom_code ILIKE $2 OR bom_name ILIKE $2) "
                        "ORDER BY created_at DESC LIMIT 1",
                        context.tenant_id,
                        f"%{str(_bom_name).strip()}%",
                    )
                if _bom_rows:
                    endpoint = endpoint.replace("{id}", _bom_rows[0]["id"])
                    logger.warning(
                        "[MFG_RESOLVE_BOM] Found: %s -> %s",
                        _bom_name,
                        _bom_rows[0]["id"],
                    )
                else:
                    return AgentResponse(
                        message_type="TEXT",
                        content=f"BOM '{_bom_name}' tidak ditemukan.",
                        iterations=1,
                        model_used="pipeline",
                        total_latency_ms=int((_time.time() - start_time) * 1000),
                    )
            else:
                return AgentResponse(
                    message_type="TEXT",
                    content="Mohon sebutkan kode BOM, contoh: BOMBER-001 atau POLO-001.",
                    iterations=1,
                    model_used="pipeline",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Resolve CoA account by name -> get ID for {id} endpoints (e.g. "detail akun kas")
        if "{account_id}" in endpoint and extraction.intent == "query_account_detail":
            # Extract account name from user text (everything after "akun"/"account"/"coa")
            import re as _acc_re

            _acc_match = _acc_re.search(
                r"(?:akun|account|coa)\s+(.+)",
                user_text.strip().lower(),
            )
            if _acc_match:
                _acc_name = _acc_match.group(1).strip()
                from .db_utils import get_session_db_pool

                pool = await get_session_db_pool()
                async with pool.acquire() as conn:
                    await conn.execute(f"SET app.tenant_id = {context.tenant_id}")
                    row = await conn.fetchrow(
                        "SELECT id FROM chart_of_accounts WHERE tenant_id = $1 AND LOWER(name) ILIKE $2 AND is_active = true ORDER BY LENGTH(name) LIMIT 1",
                        context.tenant_id,
                        f"%{_acc_name}%",
                    )
                    if row:
                        endpoint = endpoint.replace("{account_id}", str(row["id"]))
                    else:
                        return AgentResponse(
                            message_type="TEXT",
                            content=f"Akun {_acc_name} tidak ditemukan.",
                            iterations=1,
                            model_used="gpt-4o-mini",
                            total_latency_ms=int((_time.time() - start_time) * 1000),
                        )
            else:
                return AgentResponse(
                    message_type="TEXT",
                    content="Mohon sebutkan nama akun yang ingin dicek.",
                    iterations=1,
                    model_used="gpt-4o-mini",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        # Resolve item_id query param (for endpoints like /api/item-batches?item_id=UUID)
        if any(
            qp.name == "item_id" for qp in (query_config.query_params or [])
        ) and extraction.entities.get("item_name"):
            if "{id}" not in endpoint:  # Only for non-path-param endpoints
                from .entity_resolver import EntityResolver
                from .db_utils import get_session_db_pool

                pool = await get_session_db_pool()
                resolver = EntityResolver(pool, context.tenant_id)
                resolved_item = await resolver._resolve_item(
                    extraction.entities["item_name"]
                )
                if (
                    resolved_item
                    and resolved_item.entity_id
                    and resolved_item.confidence >= 0.5
                ):
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
        for qp in query_config.query_params or []:
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

        import re as _bail_re

        # ── Generic {id} resolver: try session entity graph + text-based search ──

        if _bail_re.search(r"\{\w+\}", endpoint):
            _resolved_id = None
            _placeholder_match = _bail_re.search(r"\{(\w+)\}", endpoint)
            _placeholder = _placeholder_match.group(1) if _placeholder_match else "id"

            # 1. Try extraction entities for direct ID
            _resolved_id = (
                extraction.entities.get("id")
                or extraction.entities.get("entity_id")
                or extraction.entities.get(_placeholder)
            )

            # 2. Try entity resolver for specific types
            if not _resolved_id:
                # (name_key, resolver_method)
                # Resolvers: "_resolve_invoice" / "_resolve_bill" = dedicated methods
                # "_resolve_by_number" = generic, needs _DOC_NUMBER_CONFIG below
                _DOC_NUMBER_CONFIG = {
                    "query_expense_detail": ("expenses", "expense_number", "expense"),
                    "query_journal_detail": (
                        "journal_entries",
                        "journal_number",
                        "journal",
                    ),
                    "query_bill_payment_detail": (
                        "bill_payments_v2",
                        "payment_number",
                        "bill_payment",
                    ),
                    "query_receive_payment_detail": (
                        "receive_payments",
                        "payment_number",
                        "receive_payment",
                    ),
                    "query_stock_adjustment_detail": (
                        "stock_adjustments",
                        "adjustment_number",
                        "stock_adjustment",
                    ),
                    "query_credit_note_detail": (
                        "credit_notes",
                        "credit_note_number",
                        "credit_note",
                    ),
                    "query_vendor_credit_detail": (
                        "vendor_credits",
                        "vendor_credit_number",
                        "vendor_credit",
                    ),
                    "query_quote_detail": ("quotes", "quote_number", "quote"),
                }
                _intent_entity_map = {
                    "query_sales_invoice_detail": (
                        "invoice_number",
                        "_resolve_invoice",
                    ),
                    "query_bill_detail": ("bill_number", "_resolve_bill"),
                    "query_bill_payment_detail": (
                        "bill_payment_number",
                        "_resolve_by_number",
                    ),
                    "query_receive_payment_detail": (
                        "payment_number",
                        "_resolve_by_number",
                    ),
                    "query_expense_detail": ("expense_number", "_resolve_by_number"),
                    "query_journal_detail": ("journal_number", "_resolve_by_number"),
                    "query_stock_adjustment_detail": (
                        "adjustment_number",
                        "_resolve_by_number",
                    ),
                    "query_credit_note_detail": (
                        "credit_note_number",
                        "_resolve_by_number",
                    ),
                    "query_vendor_credit_detail": (
                        "vendor_credit_number",
                        "_resolve_by_number",
                    ),
                    "query_quote_detail": ("quote_number", "_resolve_by_number"),
                }
                _ie_cfg = _intent_entity_map.get(extraction.intent)
                if _ie_cfg:
                    _name_key, _resolver_method = _ie_cfg
                    _search_val = (
                        extraction.entities.get(_name_key)
                        or extraction.entities.get("name")
                        or extraction.entities.get("number")
                        or extraction.entities.get("entity_name")
                    )
                    if _search_val and _resolver_method:
                        try:
                            from .entity_resolver import EntityResolver
                            from .db_utils import get_session_db_pool

                            _pool = await get_session_db_pool()
                            _resolver = EntityResolver(_pool, context.tenant_id)
                            if _resolver_method == "_resolve_by_number":
                                _dnc = _DOC_NUMBER_CONFIG.get(extraction.intent)
                                if _dnc:
                                    _resolved = await _resolver._resolve_by_number(
                                        _search_val,
                                        table=_dnc[0],
                                        number_column=_dnc[1],
                                        entity_type=_dnc[2],
                                    )
                                else:
                                    _resolved = None
                            else:
                                _resolved = await getattr(_resolver, _resolver_method)(
                                    _search_val
                                )
                            if (
                                _resolved
                                and _resolved.entity_id
                                and _resolved.confidence >= 0.5
                            ):
                                _resolved_id = _resolved.entity_id
                        except Exception as _re_err:
                            logger.warning(
                                "[QUERY_PIPELINE] Entity resolver failed: %s", _re_err
                            )

            # 3. Try session entity graph (last focused entity)
            if (
                not _resolved_id
                and tool_executor
                and getattr(tool_executor, "session_manager", None)
                and getattr(tool_executor, "session_id", None)
            ):
                try:
                    _eg_state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    if _eg_state and getattr(_eg_state, "entity_graph", None):
                        from .entity_graph import get_last_node

                        _graph_type_map = {
                            "query_sales_invoice_detail": "sales_invoice",
                            "query_bill_detail": "bill",
                            "query_expense_detail": "expense",
                            "query_journal_detail": "journal",
                            "query_credit_note_detail": "credit_note",
                            "query_vendor_credit_detail": "vendor_credit",
                            "query_quote_detail": "quote",
                            "query_receive_payment_detail": "receive_payment",
                            "query_bill_payment_detail": "bill_payment",
                            "query_stock_adjustment_detail": "stock_adjustment",
                        }
                        _graph_type = _graph_type_map.get(extraction.intent)
                        if _graph_type:
                            _last = get_last_node(_eg_state.entity_graph, _graph_type)
                            if _last:
                                _resolved_id = _last["id"]
                except Exception:
                    pass

            # 3.5. Try REC document reference from session state
            if (
                not _resolved_id
                and tool_executor
                and getattr(tool_executor, "session_manager", None)
                and getattr(tool_executor, "session_id", None)
            ):
                try:
                    from .entity_resolver import EntityResolver as _RecResolver

                    _rec_state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    _rec_result = _RecResolver.resolve_from_session(
                        user_text, _rec_state
                    )
                    if _rec_result.get("entity_id"):
                        _resolved_id = _rec_result["entity_id"]
                        logger.warning(
                            "[QUERY_PIPELINE] Resolved via REC doc-ref: %s",
                            _resolved_id[:8] if _resolved_id else "?",
                        )
                except Exception:
                    pass

            # 4. Try periode resolution for report endpoints
            if not _resolved_id and _placeholder == "periode":
                import datetime

                _now = datetime.date.today()
                _resolved_id = f"{_now.year}-{_now.month:02d}"

            if _resolved_id:
                endpoint = _bail_re.sub(
                    r"\{\w+\}", str(_resolved_id), endpoint, count=1
                )

        # Bail if still unresolved {id} or {account_id} or other path params

        if _bail_re.search(r"\{\w+\}", endpoint):
            _entity_hint_map = {
                "customer": "pelanggan",
                "vendor": "vendor",
                "bank_account": "rekening bank",
                "bank": "rekening bank",
                "account": "akun",
                "item": "barang",
                "invoice": "faktur",
                "bill": "tagihan",
                "expense": "pengeluaran",
                "journal": "jurnal",
            }
            _entity_hint = "barang"  # fallback
            _ak = query_config.action_key.lower()
            for _key, _label in _entity_hint_map.items():
                if _key in _ak:
                    _entity_hint = _label
                    break
            return AgentResponse(
                message_type="TEXT",
                content=f"Mohon sebutkan nama {_entity_hint} yang ingin dicek.",
                iterations=1,
                model_used="gpt-4o-mini",
                total_latency_ms=int((_time.time() - start_time) * 1000),
            )

        # Call REST endpoint
        try:
            base_url = "http://localhost:8000"
            auth_token = getattr(context, "auth_token", "") or ""
            headers = {
                "Authorization": f"Bearer {auth_token}",
                "Content-Type": "application/json",
                "X-Tenant-ID": context.tenant_id,
            }

            # ECM Phase 2: inject missing entity params into pipeline query
            try:
                if hasattr(self, "_ecm") and query_params is not None:
                    # Build synthetic schema from query_config.query_params
                    _synth_props = {}
                    for _qp in query_config.query_params or []:
                        _synth_props[_qp.name] = {"type": "string"}
                    _synth_schema = {
                        "parameters": {"properties": _synth_props, "required": []}
                    }
                    query_params = self._ecm.inject_missing_params(
                        query_config.action_key, _synth_schema, query_params
                    )
            except Exception as _ecm_inj_err:
                logger.debug("[ECM] pipeline injection failed: %s", _ecm_inj_err)

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{base_url}{endpoint}",
                    params=query_params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(
                f"[QUERY_PIPELINE] REST call failed, falling back to agent loop: {e}"
            )
            raise RuntimeError(f"Query pipeline REST failed: {e}")

        await emit(
            "THINKING_STEP",
            {
                "step_id": "query-resolve",
                "text": "Mencari data",
                "status": "done",
                "duration_ms": int((_time.time() - start_time) * 1000),
                "category": "search",
            },
        )

        # ── REC: Extract and store response entity context ──
        try:
            _rec = self._extract_rec(query_config.action_key, data)
            if _rec and tool_executor and getattr(tool_executor, "session_id", None):
                await tool_executor.session_manager.update_state(
                    tool_executor.session_id,
                    last_domain=_rec["domain"],
                    last_response_items=_rec["items"],
                    active_entity=_rec.get("primary_entity"),
                    last_numeric=_rec.get("numeric"),
                )
        except Exception as _rec_err:
            logger.warning("[REC] Extract/store failed: %s", _rec_err)

        # ECM (Phase 1+2) — pipeline path: seed from session state
        try:
            from .entity_context_manager import (
                EntityContextManager,
                build_schema_needs_cache,
            )
            from .tool_registry import get_tools as _gt_ecm

            if not hasattr(self, "_ecm"):
                self._ecm = EntityContextManager()
                build_schema_needs_cache(_gt_ecm())
            self._ecm.advance_turn()

            # Seed ECM from existing session state (persisted in Redis)
            if (
                tool_executor
                and getattr(tool_executor, "session_manager", None)
                and getattr(tool_executor, "session_id", None)
            ):
                try:
                    _seed_state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    if _seed_state:
                        from .entity_context_manager import Entity

                        if getattr(_seed_state, "active_customer_id", None):
                            self._ecm.push_entity(
                                Entity(
                                    type="customer",
                                    id=_seed_state.active_customer_id,
                                    name=getattr(
                                        _seed_state, "active_customer_name", None
                                    ),
                                    source="session_seed",
                                    turn=0,
                                )
                            )
                        if getattr(_seed_state, "active_vendor_id", None):
                            self._ecm.push_entity(
                                Entity(
                                    type="vendor",
                                    id=_seed_state.active_vendor_id,
                                    name=getattr(
                                        _seed_state, "active_vendor_name", None
                                    ),
                                    source="session_seed",
                                    turn=0,
                                )
                            )
                        if getattr(_seed_state, "active_invoice_id", None):
                            self._ecm.push_entity(
                                Entity(
                                    type="invoice",
                                    id=_seed_state.active_invoice_id,
                                    number=getattr(
                                        _seed_state, "active_invoice_number", None
                                    ),
                                    source="session_seed",
                                    turn=0,
                                )
                            )
                        if getattr(_seed_state, "active_bill_id", None):
                            self._ecm.push_entity(
                                Entity(
                                    type="bill",
                                    id=_seed_state.active_bill_id,
                                    number=getattr(
                                        _seed_state, "active_bill_number", None
                                    ),
                                    source="session_seed",
                                    turn=0,
                                )
                            )
                        # Seed from active_entity (set by calc_rank pipeline)
                        _ae = getattr(_seed_state, "active_entity", None)
                        if (
                            _ae
                            and isinstance(_ae, dict)
                            and _ae.get("type")
                            and _ae.get("name")
                        ):
                            self._ecm.push_entity(
                                Entity(
                                    type=_ae["type"],
                                    id=_ae.get("id"),
                                    name=_ae["name"],
                                    source="session_seed_active_entity",
                                    turn=0,
                                )
                            )
                except Exception as _seed_err:
                    logger.debug("[ECM] session seed failed: %s", _seed_err)
            _ecm_extracted = self._ecm.ingest_tool_result(
                query_config.action_key or "query_endpoint", query_params, data
            )
            if _ecm_extracted:
                logger.warning("[ECM_SHADOW] pipeline stats=%s", self._ecm.get_stats())
        except Exception as _ecm_err:
            logger.debug("[ECM] pipeline ingest failed: %s", _ecm_err)

        # Format + LLM Polish
        await emit(
            "THINKING_STEP",
            {
                "step_id": "query-format",
                "text": "Menyusun jawaban",
                "status": "running",
                "category": "write",
            },
        )

        response_text = await self._polish_query_response(
            query_config=query_config,
            data=data,
            user_text=user_text,
            entity_name=extraction.entities.get("item_name")
            or extraction.entities.get("warehouse_name")
            or "",
        )

        await emit(
            "THINKING_STEP",
            {
                "step_id": "query-format",
                "text": "Menyusun jawaban",
                "status": "done",
                "duration_ms": int((_time.time() - start_time) * 1000),
                "category": "write",
            },
        )

        await emit(
            "THINKING_DONE",
            {
                "summary": "Data ditemukan",
                "total_ms": int((_time.time() - start_time) * 1000),
            },
        )

        # Save last query context for reformat_as_table
        try:
            if (
                tool_executor
                and getattr(tool_executor, "session_manager", None)
                and getattr(tool_executor, "session_id", None)
            ):
                # Build entity context for session persistence
                _entity_updates = {}
                if extraction.entities.get("customer_name"):
                    _entity_updates["active_customer_name"] = extraction.entities[
                        "customer_name"
                    ]
                if extraction.entities.get("vendor_name"):
                    _entity_updates["active_vendor_name"] = extraction.entities[
                        "vendor_name"
                    ]
                if extraction.entities.get("item_name"):
                    _entity_updates["active_items"] = [
                        {"name": extraction.entities["item_name"]}
                    ]
                if extraction.entities.get("bank_name"):
                    _entity_updates["active_customer_name"] = _entity_updates.get(
                        "active_customer_name"
                    )  # no-op, keep existing

                await tool_executor.session_manager.update_state(
                    tool_executor.session_id,
                    last_action_type=extraction.intent,
                    last_action_result={
                        "response_text": response_text[:2000],
                        "query_text": user_text,
                        "last_query_params": query_params,
                    },
                    **_entity_updates,
                )
        except Exception as _save_err:
            logger.warning("[QUERY_PIPELINE] Failed to save last query: %s", _save_err)

        return AgentResponse(
            message_type="TEXT",
            content=response_text,
            iterations=1,
            tool_calls_made=[
                {
                    "name": "entity_extractor",
                    "args": {"intent": extraction.intent},
                    "success": True,
                },
                {
                    "name": "query_endpoint",
                    "args": {"endpoint": endpoint},
                    "success": True,
                },
            ],
            model_used=getattr(self, "_last_polish_model", "pipeline"),
            total_latency_ms=int((_time.time() - start_time) * 1000),
            thinking_stages=["Menganalisis pesan", "Mencari data", "Menyusun jawaban"],
        )

    async def _handle_drilldown_table(
        self,
        user_text,
        context,
        extraction,
        tool_executor=None,
        event_callback=None,
    ) -> "AgentResponse":
        """Contextual drill-down: resolve last query context → route to pipeline list query.
        E.g., after 'hutang berapa' (query_ap_outstanding) → 'per faktur' → query_bills_list.
        ~1.5s via pipeline instead of ~50s via agent loop.
        """
        # start_time removed (unused)

        # Determine drill-down target from session state
        _last_intent = None
        if (
            tool_executor
            and getattr(tool_executor, "session_manager", None)
            and getattr(tool_executor, "session_id", None)
        ):
            try:
                state = await tool_executor.session_manager.get_state(
                    tool_executor.session_id
                )
                if state:
                    _last_intent = getattr(state, "last_action_type", None)
            except Exception:
                pass

        # Drill-down mapping: last_intent → target pipeline intent
        _DRILLDOWN_MAP = {
            "query_ap_outstanding": "query_bills_list",
            "query_ar_outstanding": "query_sales_invoices_list",
            "query_bills_summary": "query_bills_list",
            "query_sales_invoices_summary": "query_sales_invoices_list",
            "query_expenses_summary": "query_expenses_list",
            # Bug #1.5: customer/vendor AR/AP drill-down to invoice/bill list
            "query_customer_ar": "query_sales_invoices_list",
            "query_vendor_ap": "query_bills_list",
            "calc_rank_customers_by_ar": "query_sales_invoices_list",
            "calc_rank_vendors_by_ap": "query_bills_list",
        }

        target_intent = _DRILLDOWN_MAP.get(_last_intent)

        if not target_intent:
            # No context — fallback: try to infer from user text
            t = user_text.lower()
            if any(w in t for w in ("tagihan", "hutang", "bill", "ap", "pembelian")):
                target_intent = "query_bills_list"
            elif any(w in t for w in ("piutang", "invoice", "ar", "penjualan")):
                target_intent = "query_sales_invoices_list"
            elif any(w in t for w in ("pengeluaran", "biaya", "expense")):
                target_intent = "query_expenses_list"
            else:
                # Default: if last intent was AP-related, show bills
                target_intent = "query_bills_list"

        logger.warning(
            "[DRILLDOWN] last_intent=%s → target=%s user='%s'",
            _last_intent,
            target_intent,
            user_text[:50],
        )

        # Override extraction intent and route to query pipeline
        extraction.intent = target_intent
        extraction.confidence = 1.0
        extraction.needs_escalation = False

        # Parse filter/sort hints from user text
        _t = user_text.lower()
        _filter_hint = ""
        if any(
            w in _t for w in ("belum lunas", "belum dibayar", "belum bayar", "unpaid")
        ):
            _filter_hint = " — FILTER: hanya yang belum lunas (unpaid + partial)"
        if any(w in _t for w in ("jatuh tempo", "overdue", "paling dekat")):
            _filter_hint += " — SORT: urutkan berdasarkan tanggal jatuh tempo terdekat"

        # Always enforce table format in polish
        _table_hint = (
            user_text + " (WAJIB tampilkan dalam bentuk tabel markdown)" + _filter_hint
        )

        # Store drilldown context for post-processing (filter paid, pre-compute total)
        self._drilldown_context = {
            "is_hutang": target_intent == "query_bills_list",
            "is_piutang": target_intent == "query_sales_invoices_list",
            "filter_unpaid": "belum lunas" in _t
            or "belum dibayar" in _t
            or "hutang" in _t,
        }

        return await self._handle_query_pipeline(
            user_text=_table_hint,
            context=context,
            extraction=extraction,
            tool_executor=tool_executor,
            event_callback=event_callback,
        )

    async def _handle_contextual_drill_down(
        self,
        user_text,
        context,
        extraction,
        tool_executor=None,
        event_callback=None,
    ) -> "AgentResponse":
        """Contextual drill-down with param forwarding from previous query.
        Maps last_action_type to list query, forwarding params like vendor_id/customer_id.
        """
        import time as _time

        start_time = _time.time()

        # Get session state
        _last_intent = None
        _last_query_params = {}
        if (
            tool_executor
            and getattr(tool_executor, "session_manager", None)
            and getattr(tool_executor, "session_id", None)
        ):
            try:
                state = await tool_executor.session_manager.get_state(
                    tool_executor.session_id
                )
                if state:
                    _last_intent = getattr(state, "last_action_type", None)
                    _last_result = getattr(state, "last_action_result", None)
                    if isinstance(_last_result, dict):
                        _last_query_params = _last_result.get("last_query_params", {})
            except Exception:
                pass

        # Drill-down mapping with param forwarding
        _CONTEXT_DRILL_MAP = {
            "query_ap_outstanding": ("query_bills_list", {"status": "active"}),
            "query_ap_aging": ("query_bills_list", {"status": "active"}),
            "query_ar_outstanding": ("query_sales_invoices_list", {"status": "active"}),
            "query_ar_aging": ("query_sales_invoices_list", {"status": "active"}),
            "query_ar_invoices": ("query_sales_invoices_list", {"status": "active"}),
            "query_customer_ar": ("query_sales_invoices_list", {"status": "active"}),
            "query_vendor_ap": ("query_bills_list", {"status": "active"}),
            "query_expenses_summary": ("query_expenses_list", {}),
            "query_bills_summary": ("query_bills_list", {}),
            "query_sales_invoices_summary": ("query_sales_invoices_list", {}),
            # Calc rank intents drill down to their respective lists
            "calc_rank_customers_by_ar": (
                "query_sales_invoices_list",
                {"status": "active"},
            ),
            "calc_rank_vendors_by_ap": ("query_bills_list", {"status": "active"}),
        }

        mapping = _CONTEXT_DRILL_MAP.get(_last_intent)

        if not mapping:
            # No context — fallback: try to infer from user text
            t = user_text.lower()
            if any(w in t for w in ("tagihan", "hutang", "bill", "ap", "pembelian")):
                mapping = ("query_bills_list", {"status": "active"})
            elif any(w in t for w in ("piutang", "invoice", "ar", "penjualan")):
                mapping = ("query_sales_invoices_list", {"status": "active"})
            elif any(w in t for w in ("pengeluaran", "biaya", "expense")):
                mapping = ("query_expenses_list", {})
            else:
                return AgentResponse(
                    message_type="TEXT",
                    content="Mau detail tentang apa? Coba sebutkan lebih spesifik, misalnya: hutang, piutang, atau pengeluaran.",
                    iterations=1,
                    model_used="pipeline",
                    total_latency_ms=int((_time.time() - start_time) * 1000),
                )

        target_intent, extra_params = mapping

        # Forward relevant params from previous query
        _forwarded_params = {}
        for key in ("customer_id", "vendor_id"):
            if key in _last_query_params:
                _forwarded_params[key] = _last_query_params[key]

        _all_params = dict(extra_params)
        _all_params.update(_forwarded_params)

        logger.warning(
            "[CONTEXTUAL_DRILL_DOWN] last_intent=%s target=%s params=%s user='%s'",
            _last_intent,
            target_intent,
            _all_params,
            user_text[:50],
        )

        # Override extraction and merge params
        extraction.intent = target_intent
        extraction.confidence = 1.0
        extraction.needs_escalation = False

        # Inject forwarded params into entities so _handle_query_pipeline picks them up
        for k, v in _all_params.items():
            extraction.entities[k] = v

        # Store drilldown context
        self._drilldown_context = {
            "is_hutang": target_intent == "query_bills_list",
            "is_piutang": target_intent == "query_sales_invoices_list",
            "filter_unpaid": True,
        }

        _table_hint = user_text + " (WAJIB tampilkan dalam bentuk tabel markdown)"

        return await self._handle_query_pipeline(
            user_text=_table_hint,
            context=context,
            extraction=extraction,
            tool_executor=tool_executor,
            event_callback=event_callback,
        )

    async def _handle_reformat_as_table(
        self,
        user_text,
        context,
        tool_executor=None,
        event_callback=None,
        conversation_history=None,
    ) -> "AgentResponse":
        """Re-format last bot response as a markdown table using Gemini Flash."""
        import time as _time

        start_time = _time.time()

        # Get last response from session state
        last_response = None
        if tool_executor and tool_executor.session_manager and tool_executor.session_id:
            try:
                state = await tool_executor.session_manager.get_state(
                    tool_executor.session_id
                )
                if state and state.last_action_result:
                    result = state.last_action_result
                    if isinstance(result, dict):
                        last_response = result.get("response_text")
                    elif isinstance(result, str):
                        last_response = result
            except Exception as _e:
                logger.warning("[REFORMAT] Failed to get state: %s", _e)

        if not last_response:
            # Fallback: try chat_messages table
            try:
                if tool_executor and tool_executor.session_manager:
                    rows = await tool_executor.session_manager.db.fetch(
                        """
                        SELECT content FROM chat_messages
                        WHERE session_id = $1::uuid AND tenant_id = $2 AND role = 'assistant'
                        ORDER BY created_at DESC LIMIT 1
                    """,
                        tool_executor.session_id,
                        context.tenant_id,
                    )
                    if rows:
                        last_response = rows[0]["content"]
            except Exception as _e2:
                logger.warning("[REFORMAT] Fallback fetch failed: %s", _e2)

        # Fallback 2: extract from conversation_history
        if not last_response and conversation_history:
            for msg in reversed(conversation_history):
                if msg.get("role") == "assistant" and msg.get("content"):
                    _c = msg["content"]
                    if len(_c) > 30:  # skip short acks
                        last_response = _c[:2000]
                        logger.warning(
                            "[REFORMAT] Using conversation_history fallback (%d chars)",
                            len(_c),
                        )
                        break

        if not last_response:
            return AgentResponse(
                message_type="TEXT",
                content="Mau tabel tentang apa? Misalnya: 'tampilkan hutang dalam tabel' atau 'tabel piutang per pelanggan'",
                iterations=1,
                model_used="pipeline",
                total_latency_ms=int((_time.time() - start_time) * 1000),
            )

        reformat_prompt = (
            f"User meminta data berikut ditampilkan dalam bentuk tabel markdown:\n\n"
            f"{last_response}\n\n"
            f"FORMAT ULANG sebagai tabel markdown yang rapi.\n"
            f"Pertahankan semua data dan angka, hanya ubah format ke tabel.\n"
            f"Tambahkan baris total di bawah tabel kalau ada angka.\n"
            f"Kalau data sudah dalam bentuk tabel, perbaiki formatnya saja.\n"
            f"JANGAN tambah data yang tidak ada. JANGAN hilangkan data."
        )

        try:
            response = await self.router.complete(
                task_type="polish",
                messages=[
                    LLMMessage(
                        role="system",
                        content="Kamu formatter data. Format ulang data menjadi tabel markdown yang rapi. Bahasa Indonesia.",
                    ),
                    LLMMessage(role="user", content=reformat_prompt),
                ],
                temperature=0.2,
                max_tokens=800,
            )
            text = (response.content or "").strip()
            if not text:
                text = last_response
        except Exception as e:
            logger.warning("[REFORMAT] LLM failed: %s", e)
            text = last_response

        return AgentResponse(
            message_type="TEXT",
            content=text,
            iterations=1,
            model_used="pipeline",
            total_latency_ms=int((_time.time() - start_time) * 1000),
        )

    async def _polish_query_response(
        self, query_config, data, user_text, entity_name=""
    ):
        """Polish raw API response into natural Bahasa Indonesia via LLM."""
        import json as _json

        response_format = query_config.response_format

        # Simple list — template ONLY for trivial name-only entities
        # Complex data (invoices, bills, transactions) always goes to LLM polish
        if response_format == "list":
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                for _k in (
                    "items",
                    "data",
                    "warehouses",
                    "categories",
                    "default_units",
                    "units",
                    "transfers",
                    "adjustments",
                ):
                    if _k in data and isinstance(data[_k], list):
                        items = data[_k]
                        break
                else:
                    items = []
            else:
                items = []
            _simple_entities = {
                "query_categories_list",
                "query_warehouses",
                "query_items_units",
            }
            if (
                query_config.action_key in _simple_entities
                and isinstance(items, list)
                and 0 < len(items) <= 10
            ):
                names = [
                    i.get("name", i.get("nama", i.get("nama_produk", "?")))
                    if isinstance(i, dict)
                    else str(i)
                    for i in items
                ]
                count = (
                    data.get("total", len(names))
                    if isinstance(data, dict)
                    else len(names)
                )
                return (
                    str(count)
                    + " "
                    + query_config.display_name.lower()
                    + ":\n"
                    + ", ".join(names)
                )
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

        compact_str = _json.dumps(compact, ensure_ascii=False, default=str)[:2500]

        # Pre-compute totals for drilldown — prevent Gemini from hallucinating totals
        _precomputed = ""
        if hasattr(self, "_drilldown_context") and self._drilldown_context:
            _dc = self._drilldown_context
            try:
                _items = []
                if isinstance(data, dict):
                    for _k in ("items", "data"):
                        if _k in data and isinstance(data[_k], list):
                            _items = data[_k]
                            break
                elif isinstance(data, list):
                    _items = data

                # Filter: for hutang drilldown, exclude paid (amount_due=0)
                if _dc.get("filter_unpaid") or _dc.get("is_hutang"):
                    _before = len(_items)
                    _items = [
                        i
                        for i in _items
                        if (i.get("amount", 0) - i.get("amount_paid", 0)) > 0
                    ]
                    if _before != len(_items):
                        # Re-compact with filtered data
                        compact = self._compact_query_data(
                            query_config.action_key,
                            {"items": _items, "total": len(_items)},
                        )
                        compact_str = _json.dumps(
                            compact, ensure_ascii=False, default=str
                        )[:2500]

                # Compute total from data
                _total = sum(
                    (i.get("amount", 0) - i.get("amount_paid", 0)) for i in _items
                )
                _count = len(_items)
                _precomputed = f"\nTOTAL YANG SUDAH DIHITUNG (JANGAN hitung ulang): {_count} faktur, total Rp {int(_total):,}.".replace(
                    ",", "."
                )
            except Exception:
                pass
            self._drilldown_context = None  # Reset

        # ── Insight Engine: rule-based interpretation ──
        _insight_text = ""
        try:
            from .insight_engine import (
                evaluate as _evaluate_insights,
                format_insights_for_prompt as _format_insights,
            )

            _insights = _evaluate_insights(query_config.action_key, data)
            _insight_text = _format_insights(_insights)
            if _insights:
                logger.warning(
                    "[INSIGHT] %d insights for %s (top: %s/%s)",
                    len(_insights),
                    query_config.action_key,
                    _insights[0].severity,
                    _insights[0].insight_type,
                )
        except Exception as _ie:
            logger.warning("[INSIGHT] Failed: %s", _ie)
        polish_user = (
            f'Pertanyaan user: "{user_text}"\n'
            f"Tipe: {response_format}\n"
            f"DATA:\n```json\n{compact_str}\n```{_insight_text}{_precomputed}"
        )

        try:
            response = await self.router.complete(
                task_type="polish",
                messages=[
                    LLMMessage(role="system", content=polish_system),
                    LLMMessage(role="user", content=polish_user),
                ],
                temperature=0.3,
                max_tokens=1500,
            )
            self._last_polish_model = getattr(response, "model", None) or "pipeline"
            return (
                response.content or ""
            ).strip() or "Data ditemukan tapi gagal diformat."
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
        return {
            k: v
            for k, v in data.items()
            if k not in ("id", "tenant_id", "created_at", "updated_at", "deleted_at")
            and isinstance(v, (str, int, float, bool))
        }

    async def _polish_current_data(
        self, entity_name: str, entity_type: str, current_data: dict
    ) -> str:
        """LLM polish: show current data naturally + ask what to change."""
        import json as _json

        display = {
            k: v
            for k, v in current_data.items()
            if v is not None and v != "" and v != 0
        }

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
            response = await self.router.complete(
                task_type="polish",
                messages=[
                    LLMMessage(role="system", content=system_prompt),
                    LLMMessage(role="user", content=user_prompt),
                ],
                temperature=0.4,
                max_tokens=1500,
            )
            return (
                response.content or ""
            ).strip() or f"Data {entity_name} ditemukan. Mau ubah yang mana?"
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
        if (
            isinstance(data, dict)
            and "data" in data
            and isinstance(data["data"], (dict, list))
        ):
            data = data["data"]

        # Special handling: trial-balance (neraca saldo) can have up to 360 CoA
        # rows. LLM polish over the full list is slow (~11s). Filter out
        # zero-balance accounts and cap at top 20 by absolute balance. Preserve
        # top-level totals so LLM can still summarize accurately.
        if action_key == "query_trial_balance" and isinstance(data, dict):
            _accounts = data.get("accounts", []) or []
            _filtered = [
                a
                for a in _accounts
                if float(a.get("total_debit", 0) or 0) != 0
                or float(a.get("total_credit", 0) or 0) != 0
                or float(a.get("balance", 0) or 0) != 0
            ]
            _filtered.sort(
                key=lambda a: abs(float(a.get("balance", 0) or 0)),
                reverse=True,
            )
            _top = _filtered[:20]
            return {
                "as_of_date": data.get("as_of_date"),
                "period_id": data.get("period_id"),
                "total_debit": data.get("total_debit"),
                "total_credit": data.get("total_credit"),
                "is_balanced": data.get("is_balanced"),
                "account_count": data.get("account_count", len(_accounts)),
                "_accounts_nonzero": len(_filtered),
                "_accounts_shown": len(_top),
                "accounts": [
                    {
                        "code": a.get("account_code"),
                        "name": a.get("account_name"),
                        "type": a.get("account_type"),
                        "debit": a.get("total_debit"),
                        "credit": a.get("total_credit"),
                        "balance": a.get("balance"),
                    }
                    for a in _top
                ],
            }

        # If data is a list, wrap it
        if isinstance(data, list):
            return {"total": len(data), "items": self._compact_list(data, max_items=10)}

        # Auto-detect the main list key in response dict
        list_data = None
        list_key = None
        _COMMON_LIST_KEYS = [
            "items",
            "data",
            "products",
            "transactions",
            "entries",
            "categories",
            "units",
            "default_units",
            "custom_units",
            "warehouses",
            "adjustments",
            "transfers",
            "stock",
            "lines",
            "batches",
            "invoices",
            "bills",
            "payments",
            "results",
            "records",
            "movements",
            "activities",
            "related",
            "history",
            "journal_entries",
            "purchase_orders",
            "accounts",
            "brackets",
            "expenses",
            "alerts",
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
            elif (
                isinstance(v, list)
                and len(v) <= 5
                and all(isinstance(x, str) for x in v)
            ):
                # Small string list (e.g. default_units: ["pcs", "kg"])
                compact[self._rename_field(k)] = v
            elif isinstance(v, dict) and len(v) <= 8:
                # Small nested dict (e.g. breakdown) — include stripped
                compact[self._rename_field(k)] = {
                    self._rename_field(sk): self._safe_val(sv)
                    for sk, sv in v.items()
                    if not self._is_noise_field(sk)
                    and isinstance(sv, (str, int, float, bool, type(None)))
                }

        # Process list data
        if list_data:
            compact["total"] = compact.get("total") or data.get(
                "total", data.get("count", len(list_data))
            )
            compact_key = list_key if list_key not in ("data",) else "items"
            compact[compact_key] = self._compact_list(list_data, max_items=10)
        elif not compact:
            # No list, no scalars — return raw (stripped) for LLM to figure out
            return {
                self._rename_field(k): self._safe_val(v)
                for k, v in data.items()
                if not self._is_noise_field(k)
                and isinstance(v, (str, int, float, bool, type(None)))
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

    _NOISE_FIELDS = frozenset(
        {
            # UUIDs and internal IDs
            "id",
            "uuid",
            "tenant_id",
            "user_id",
            "created_by",
            "updated_by",
            "posted_by",
            "voided_by",
            "shipped_by",
            "received_by",
            "cancelled_by",
            "session_id",
            "conversation_id",
            "idempotency_key",
            # Timestamps (keep only human-readable dates)
            "created_at",
            "updated_at",
            "deleted_at",
            "posted_at",
            "voided_at",
            # Internal references
            "journal_id",
            "source_id",
            "coa_id",
            "confidentiality_level",
            "sales_account_id",
            "purchase_account_id",
            "inventory_account_id",
            "cogs_account_id",
            "pph_account_id",
            # Technical
            "is_matrix_parent",
            "matrix_parent_id",
            "content_unit",
            "wholesale_unit",
            "units_per_wholesale",
            "image_url",
            "image_urls",
        }
    )

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
            "product_id",
            "item_id",
            "warehouse_id",
        ):
            return True
        return False

    def _rename_field(self, field_name: str) -> str:
        """Rename field to human-readable Indonesian name."""
        return self._FIELD_RENAMES.get(field_name, field_name)

    async def _classify_via_llm_router(
        self,
        user_text: str,
        context,
        tool_executor,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        db_pool=None,
    ):
        """
        Phase 2 primary classifier. Calls LLM Intent Router and returns an
        ExtractionResult-compatible object, or None to force regex fallback.

        Guardrails:
        1. Anti-loop: if same intent appeared 3+ times in recent history, force fallback.
        2. FALLBACK check: intent=='FALLBACK' or confidence<0.3 => force fallback.
        3. Field validation: drop amount/quantity if not positive numeric.
        4. Entity sanity: log warning if entity keys mismatch intent family (no override).
        """
        from .llm_intent_router import LLMIntentRouter
        from .entity_extractor import ExtractionResult

        # Build workflow context from active crud_form state (if any)
        _wf_ctx = None
        if tool_executor and tool_executor.session_id:
            try:
                from .workflow_engine import WorkflowEngine
                from .db_utils import get_session_db_pool as _wf_pool_fn

                _wf_db = await _wf_pool_fn()
                _wf_eng = WorkflowEngine(
                    _wf_db,
                    context.tenant_id,
                    getattr(context, "user_id", ""),
                    getattr(context, "auth_token", ""),
                )
                _wf_state = await _wf_eng.get_state(
                    tool_executor.session_id, "crud_form"
                )
                if _wf_state and _wf_state.status == "active":
                    _wf_ctx = {
                        "intent": _wf_state.data.get("intent", ""),
                        "phase": getattr(_wf_state, "current_state", ""),
                    }
            except Exception as _wf_err:
                logger.warning(
                    "[LLM_ROUTER_PRIMARY] WF state fetch failed: %s", _wf_err
                )

        # Build OCR/image context from session state if present
        _ocr_text = None
        _last_action = None
        try:
            if (
                tool_executor
                and getattr(tool_executor, "session_manager", None)
                and tool_executor.session_id
            ):
                _s = await tool_executor.session_manager.get_state(
                    tool_executor.session_id
                )
                _doc_ctx = getattr(_s, "document_context", None) if _s else None
                if isinstance(_doc_ctx, dict):
                    _ocr_text = _doc_ctx.get("ocr_text") or _doc_ctx.get("text")
                _last_action = getattr(_s, "last_action_type", None) if _s else None
        except Exception:
            pass

        # Build entity_memory from REC session state (P1.1)
        _entity_memory = None
        if _s:
            _rec_ctx = {}
            if getattr(_s, "last_domain", None):
                _rec_ctx["last_domain"] = _s.last_domain
            if getattr(_s, "active_entity", None):
                _rec_ctx["active_entity"] = _s.active_entity
            if getattr(_s, "last_numeric", None):
                _rec_ctx["last_numeric"] = _s.last_numeric
            if getattr(_s, "last_response_items", None):
                # Only send first 5 items to keep prompt short
                _rec_ctx["last_response_items"] = _s.last_response_items[:5]
            if _rec_ctx:
                _entity_memory = _rec_ctx

        # Call LLM Router
        try:
            _router = LLMIntentRouter(self.router)
            _result = await _router.route(
                user_text=user_text,
                conversation_history=conversation_history[-10:]
                if conversation_history
                else None,
                workflow_state=_wf_ctx,
                entity_memory=_entity_memory,
                ocr_text=_ocr_text,
            )
        except Exception as _rt_err:
            logger.warning("[LLM_ROUTER_PRIMARY] route() failed: %s", _rt_err)
            return None

        _intent_val = (_result.intent or "").strip()
        _conf = float(_result.confidence or 0.0)
        _entities = dict(_result.entities or {})

        # Guardrail 2: FALLBACK / low-confidence
        if _intent_val in ("FALLBACK", "", "ambiguous") or _conf < 0.3:
            logger.warning(
                "[LLM_ROUTER_FALLBACK] intent=%s conf=%.2f -> regex fallback",
                _intent_val,
                _conf,
            )
            return None

        # Guardrail 1: Anti-loop (same intent 3+ times in last 6 user turns)
        if conversation_history:
            try:
                _user_msgs = [
                    m for m in conversation_history[-12:] if m.get("role") == "user"
                ]
                if len(_user_msgs) >= 3:
                    # Count same intent in router memory not available — use text heuristic
                    _same = 0
                    for _m in _user_msgs[-6:]:
                        if (
                            _m.get("content", "").strip().lower()
                            == user_text.strip().lower()
                        ):
                            _same += 1
                    if _same >= 3:
                        logger.warning(
                            "[LLM_ROUTER_FALLBACK] anti-loop: same text %dx -> fallback",
                            _same,
                        )
                        return None
            except Exception:
                pass

        # Guardrail 3: Field validation — drop non-positive numeric amount/quantity
        for _num_key in ("amount", "quantity", "unit_price"):
            if _num_key in _entities:
                try:
                    _v = float(_entities[_num_key])
                    if _v <= 0:
                        _entities.pop(_num_key, None)
                except (TypeError, ValueError):
                    _entities.pop(_num_key, None)

        # Guardrail 4: Sanity check entity-intent match (log only)
        try:
            if _intent_val.startswith("create_vendor") and "customer_name" in _entities:
                logger.warning(
                    "[LLM_ROUTER_SANITY] create_vendor has customer_name entity"
                )
            if _intent_val.startswith("create_customer") and "vendor_name" in _entities:
                logger.warning(
                    "[LLM_ROUTER_SANITY] create_customer has vendor_name entity"
                )
        except Exception:
            pass

        # Build ExtractionResult-compatible object
        extraction = ExtractionResult(
            intent=_intent_val,
            entities=_entities,
            modifiers=[],
            confidence=_conf,
            raw_response=dict(_result.raw_response or {}),
            needs_escalation=False,
        )

        logger.warning(
            "[LLM_ROUTER_PRIMARY] intent=%s conf=%.2f ready=%s entities=%s latency=%dms",
            _intent_val,
            _conf,
            _result.ready,
            list(_entities.keys()),
            _result.latency_ms,
        )
        logger.warning(
            "[CLASSIFY_FINAL] intent=%s confidence=%.2f source=llm_router",
            _intent_val,
            _conf,
        )
        return extraction

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
        session_id: str = None,  # alias for chat_session_id (callers use this name)
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
        # Merge session_id alias
        if session_id and not chat_session_id:
            chat_session_id = session_id
        logger.warning(
            "[PROCESS_MSG] chat_session_id=%s session_id=%s tool_executor=%s te_session=%s",
            chat_session_id,
            session_id,
            bool(tool_executor),
            getattr(tool_executor, "session_id", None) if tool_executor else None,
        )

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

        # Ensure tool_executor has session context for state persistence
        if chat_session_id and not tool_executor.session_id:
            tool_executor.session_id = chat_session_id
        if not tool_executor.session_manager and db_pool:
            try:
                from .session_manager import SessionManager

                tool_executor.session_manager = SessionManager(
                    db_pool, context.tenant_id
                )
            except Exception:
                pass

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
            _intent,
            user_text[:50],
        )

        # CHITCHAT short-circuit — bypass agent loop entirely
        if _intent == "CHITCHAT":
            # P5: Emit intent_classified SSE meta event (test correlation)
            try:
                import uuid as _uuid_ic

                _req_id_ic = str(_uuid_ic.uuid4())
                await emit(
                    "intent_classified",
                    {
                        "request_id": _req_id_ic,
                        "final_intent": "chitchat",
                        "decision_source": "infer_intent_shortcircuit",
                        "confidence": 1.0,
                    },
                )
            except Exception:
                pass
            # Phase B.2: Check for resume prompt + Tier 2 preferences
            _resume_hint = ""
            _pref_hint = ""
            try:
                from .summary_generator import get_last_session_context
                from .preference_manager import PreferenceManager
                from .db_utils import get_session_db_pool
                from datetime import datetime, timezone

                _b2_pool = await get_session_db_pool()
                _resume_ctx = await get_last_session_context(
                    _b2_pool,
                    context.tenant_id,
                    getattr(context, "user_id", ""),
                    datetime.now(timezone.utc),
                )
                if _resume_ctx and "SESI TERTUNDA" in _resume_ctx:
                    _resume_hint = _resume_ctx

                _b2_pref_mgr = PreferenceManager(
                    _b2_pool, context.tenant_id, getattr(context, "user_id", "")
                )
                _pref_hint = await _b2_pref_mgr.get_preference_context()
            except Exception as _b2_err:
                logger.warning("[B2] Resume/pref context failed: %s", _b2_err)

            _combined_hint = "\n\n".join(h for h in [_pref_hint, _resume_hint] if h)
            return await self._handle_chitchat(
                user_text, context, _route, resume_hint=_combined_hint
            )

        # ── Workflow trigger detection ──────────────────────────
        _workflow_triggers = {
            "invoice_and_payment": [
                "faktur dan bayar",
                "invoice dan bayar",
                "buat faktur langsung bayar",
                "faktur sekaligus bayar",
                "langsung lunas",
            ],
            "monthly_closing": [
                "tutup bulan",
                "closing bulan",
                "tutup buku",
                "monthly closing",
                "akhir bulan",
                "closing bulanan",
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
                            "januari": "01",
                            "februari": "02",
                            "maret": "03",
                            "april": "04",
                            "mei": "05",
                            "juni": "06",
                            "juli": "07",
                            "agustus": "08",
                            "september": "09",
                            "oktober": "10",
                            "november": "11",
                            "desember": "12",
                        }
                        for _mn, _mnum in _month_map.items():
                            if _mn in _text_lower:
                                from datetime import date as _date_type

                                _wf_user_data[
                                    "period"
                                ] = f"{_date_type.today().year}-{_mnum}"
                                break

                    wf_result = await tool_executor.execute(
                        "start_workflow",
                        {"workflow_type": _wf_type, "user_data": _wf_user_data},
                    )
                    if isinstance(wf_result, dict):
                        content_text = wf_result.get(
                            "llm_instruction", wf_result.get("message", "")
                        )
                        if wf_result.get("message_type") == "DIRECT_ACTION_PREVIEW":
                            return AgentResponse(
                                message_type="DIRECT_ACTION_PREVIEW",
                                content=content_text,
                                preview=wf_result.get("data", {}),
                                pending_action_id=wf_result.get("data", {}).get(
                                    "pending_action_id", ""
                                ),
                                iterations=1,
                                model_used="gpt-4o-mini",
                                total_latency_ms=int((time.time() - start_time) * 1000),
                                thinking_stages=[
                                    "Menganalisis pesan",
                                    "Memulai workflow",
                                ],
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

        # ═══ PHASE 0: WORKFLOW PRIORITY CHECK (before classification) ═══
        # If active crud_form workflow in COLLECTING phase, check if user is
        # answering a workflow question (slot fill) vs starting a new intent.
        # This prevents "sintia" after "pelanggan siapa?" from being classified
        # as query_customer_detail instead of workflow slot fill.
        _wf_priority_skip = False
        if (
            _intent in ("ACTION", "SIMPLE_READ")
            and tool_executor
            and tool_executor.session_id
        ):
            try:
                from .workflow_engine import WorkflowEngine
                from .db_utils import get_session_db_pool as _wf_pool_p0

                _wf_db_p0 = await _wf_pool_p0()
                _wf_engine_p0 = WorkflowEngine(
                    _wf_db_p0,
                    context.tenant_id,
                    getattr(context, "user_id", ""),
                    getattr(context, "auth_token", ""),
                )
                _active_wf_p0 = await _wf_engine_p0.get_state(
                    tool_executor.session_id, "crud_form"
                )
                logger.warning(
                    "[WF_P0_DBG2] wf=%s status=%s phase=%s",
                    bool(_active_wf_p0),
                    getattr(_active_wf_p0, "status", None),
                    (
                        getattr(_active_wf_p0, "current_state", None)
                        if _active_wf_p0
                        else None
                    ),
                )
                if _active_wf_p0 and _active_wf_p0.status == "active":
                    _wf_phase_p0 = getattr(_active_wf_p0, "current_state", "")
                    _cancel_patterns = [
                        "batal",
                        "cancel",
                        "gajadi",
                        "ga jadi",
                        "stop",
                        "sudah",
                        "lupakan",
                    ]
                    _is_cancel = any(
                        p in user_text.lower().strip() for p in _cancel_patterns
                    )

                    if (
                        _wf_phase_p0 in ("COLLECTING", "picking_candidate")
                        and not _is_cancel
                    ):
                        # ── Same pre-check as pipeline path: query classifier + question heuristic ──
                        import re as _wfp_re
                        from .entity_extractor import classify_query_intent as _wfp_qci

                        _wfp_query, _, _ = _wfp_qci(user_text)
                        _wfp_is_question = bool(
                            _wfp_re.search(
                                r"^(?:ada(?:kah)?|apakah|berapa|kapan|siapa|apa(?:kah)?|bagaimana|gimana|"
                                r"mana|kenapa|mengapa|dimana|kemana|sudah|belum|bisa)\b",
                                user_text.strip().lower(),
                            )
                        ) or user_text.strip().endswith("?")
                        # Also detect document number references (EXP-xxxx, INV-xxxx, etc.)
                        _wfp_has_doc_ref = bool(
                            _wfp_re.search(
                                r"\b(?:EXP|INV|PB|JE|CN|VC|QT|RP|BP|SA|BT|CD|VD)-[\w-]+\b",
                                user_text,
                                _wfp_re.IGNORECASE,
                            )
                        )

                        if _wfp_query or _wfp_is_question or _wfp_has_doc_ref:
                            # User switched away from CRUD — cancel workflow, don't skip classification
                            await _wf_engine_p0.cancel(
                                tool_executor.session_id, "crud_form"
                            )
                            logger.warning(
                                "[WF_PRIORITY] Cancelled workflow (query=%s question=%s doc_ref=%s): %s",
                                _wfp_query or "-",
                                _wfp_is_question,
                                _wfp_has_doc_ref,
                                user_text[:50],
                            )
                            # Don't set _wf_priority_skip — let normal classification proceed
                        else:
                            logger.warning(
                                "[WF_PRIORITY] Active workflow state=%s, skipping classification for: %s",
                                _wf_phase_p0,
                                user_text[:50],
                            )
                            _wf_priority_skip = True
            except Exception as _wf_p0_err:
                logger.warning(
                    "[WF_PRIORITY] Pre-check failed (non-fatal): %s", _wf_p0_err
                )

        _wf_priority_intent = None
        if _wf_priority_skip:
            # Store the workflow intent so we can override after extraction
            try:
                _wf_p0_db = await _wf_pool_p0()
                _wf_p0_eng = WorkflowEngine(
                    _wf_p0_db,
                    context.tenant_id,
                    getattr(context, "user_id", ""),
                    getattr(context, "auth_token", ""),
                )
                _wf_p0_state = await _wf_p0_eng.get_state(
                    tool_executor.session_id, "crud_form"
                )
                _wf_priority_intent = (
                    _wf_p0_state.data.get("intent", "") if _wf_p0_state else None
                )
            except Exception:
                _wf_priority_intent = None
            # Fall through to normal extraction below — it will extract entities
            # from user's slot fill answer. After extraction, we override the intent.

        # ── COMPILER PIPELINE: Side-by-side with agent loop ──
        # Feature-flagged: only enabled intents use pipeline.
        from .entity_extractor import EntityExtractor, is_pipeline_enabled

        # ── Telemetry: init all _tel_* vars (prevent UnboundLocalError on early-exit paths) ──
        import time as _time_mod

        _tel_extraction_start = _tel_extraction_end = None
        _tel_raw_intent = _tel_raw_conf = None
        _tel_raw_entities = {}
        _tel_gemini_ms = 0
        _tel_guard = "none"
        _tel_guard_from = _tel_guard_to = None
        _tel_decision_source = "gemini"
        _tel_guard_matches = {}
        _tel_guard_conflict = False
        _tel_conv_id = _tel_session_id = None
        extraction = (
            None  # prevent UnboundLocalError if _intent not in ACTION/SIMPLE_READ
        )
        _state = None  # prevent UnboundLocalError
        # ── P4: Clarification Slot state (ADR P4 v1.3) ──
        _clar_event = None
        _prefilled_period = None
        _resumed_from_clar = False
        _resumed_clar_intent = None
        _resumed_clar_entities: Dict[str, Any] = {}

        if _intent in ("ACTION", "SIMPLE_READ"):
            # ═══ LLM-FIRST CLASSIFICATION (v3) ═══

            # 1. Build context
            _context_hint = ""
            _ctx_summary = ""
            _state = None
            if (
                tool_executor
                and tool_executor.session_manager
                and tool_executor.session_id
            ):
                try:
                    _state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    if hasattr(_state, "to_context_string"):
                        _ctx_summary = _state.to_context_string() or ""
                except Exception:
                    pass

            # ── P4: pending_clarification check (ADR P4 v1.3) ──────────────
            # Ordering per ADR D2: pending_clarification → REC → guards → LLM.
            # Runs BEFORE LLM extraction so fills short-circuit to parent intent.
            if _state is not None:
                try:
                    from .clarification_slots import (
                        load_pending_clarification,
                        try_fill_period_slot,
                        is_explicit_domain_switch,
                        increment_reask,
                        clear_pending_clarification,
                        MAX_REASK,
                        ABANDON_WORD_THRESHOLD,
                    )
                    from .db_utils import get_session_db_pool as _clar_pool_fn

                    _clar_db = await _clar_pool_fn()

                    _ss_dict = {
                        "pending_clarification": getattr(
                            _state, "pending_clarification", None
                        ),
                        "pending_clarification_expires_at": getattr(
                            _state, "pending_clarification_expires_at", None
                        ),
                    }
                    _pending_clar = load_pending_clarification(_ss_dict)
                    _sess_id_for_clar = (
                        getattr(tool_executor, "session_id", None)
                        if tool_executor
                        else None
                    )
                    if _pending_clar and _sess_id_for_clar:
                        if _pending_clar.is_expired:
                            await clear_pending_clarification(
                                _clar_db, _sess_id_for_clar
                            )
                            _clar_event = "slot_abandoned_expired"
                            _pending_clar = None
                        elif is_explicit_domain_switch(
                            user_text, _pending_clar.parent_intent
                        ):
                            await clear_pending_clarification(
                                _clar_db, _sess_id_for_clar
                            )
                            _clar_event = "slot_abandoned_switch"
                            _pending_clar = None
                        else:
                            _fill = try_fill_period_slot(user_text)
                            if _fill.filled and not _fill.has_residue:
                                await clear_pending_clarification(
                                    _clar_db, _sess_id_for_clar
                                )
                                _clar_event = "slot_filled"
                                _resumed_from_clar = True
                                _resumed_clar_intent = _pending_clar.parent_intent
                                _resumed_clar_entities = dict(
                                    _pending_clar.parent_entities or {}
                                )
                                _resumed_clar_entities["period"] = _fill.resolved_value
                                # P4.1: sticky WRITE on clarification resume.
                                try:
                                    from datetime import (
                                        datetime as _dt_rw,
                                        timedelta as _td_rw,
                                        timezone as _tz_rw,
                                    )
                                    import json as _json_rw

                                    _exp_rw = _dt_rw.now(_tz_rw.utc) + _td_rw(
                                        minutes=30
                                    )
                                    await tool_executor.session_manager.update_state(
                                        _sess_id_for_clar,
                                        current_period=_json_rw.dumps(
                                            _fill.resolved_value
                                        ),
                                        current_period_expires_at=_exp_rw,
                                    )
                                    logger.warning(
                                        "[P4_STICKY] write (resume) session=%s period=%s",
                                        _sess_id_for_clar,
                                        _fill.resolved_value.get("label")
                                        if isinstance(_fill.resolved_value, dict)
                                        else _fill.resolved_value,
                                    )
                                except Exception as _rw_err:
                                    logger.warning(
                                        "[P4_STICKY] write (resume) failed: %s",
                                        _rw_err,
                                    )
                            elif _fill.filled and _fill.has_residue:
                                await clear_pending_clarification(
                                    _clar_db, _sess_id_for_clar
                                )
                                _clar_event = "slot_filled_with_residue"
                                _prefilled_period = _fill.resolved_value
                            else:
                                _word_count = len(user_text.split())
                                if _word_count >= ABANDON_WORD_THRESHOLD:
                                    await clear_pending_clarification(
                                        _clar_db, _sess_id_for_clar
                                    )
                                    _clar_event = "slot_abandoned_switch"
                                    _pending_clar = None
                                elif _pending_clar.reask_count >= MAX_REASK:
                                    await clear_pending_clarification(
                                        _clar_db, _sess_id_for_clar
                                    )
                                    _clar_event = "slot_abandoned_expired"
                                    _pending_clar = None
                                else:
                                    await increment_reask(
                                        _clar_db, _sess_id_for_clar, _pending_clar
                                    )
                                    _clar_event = "slot_fill_failed_first"
                                    # Emit simple reask response, matching AgentResponse shape
                                    return AgentResponse(
                                        message_type="text",
                                        content=(
                                            "Mohon sebutkan periode, misal: bulan ini, "
                                            "30 hari terakhir, April 2026"
                                        ),
                                    )
                except Exception as _clar_err:
                    logger.warning("[P4_CLAR] check failed (non-fatal): %s", _clar_err)
            # ── end P4 check ───────────────────────────────────────────────

            if _state:
                _last_action = getattr(_state, "last_action_type", None)
                if _last_action:
                    from .entity_extractor import SESSION_CONTEXT_HINTS

                    _context_hint = SESSION_CONTEXT_HINTS.get(_last_action, "")
                    _last_result = getattr(_state, "last_action_result", None)
                    if _context_hint and isinstance(_last_result, dict):
                        _entity_mem = _last_result.get("entity_memory")
                        if _entity_mem:
                            _context_hint = f"{_context_hint}\n{_entity_mem}"

            # 2. LLM extraction (PRIMARY classifier)
            _extract_client, _extract_model = self.router.route("extraction")
            extractor = EntityExtractor(_extract_client, _extract_model)

            # ── Telemetry: start timing ──
            import time as _time_mod  # noqa: F811

            _tel_extraction_start = _time_mod.monotonic()

            extraction = await extractor.extract(
                user_text,
                context_summary=_ctx_summary,
                context_hint=_context_hint,
            )

            # ── Telemetry: capture raw Gemini result before guards ──
            _tel_extraction_end = _time_mod.monotonic()
            _tel_raw_intent = extraction.intent
            _tel_raw_conf = extraction.confidence
            _tel_raw_entities = (
                dict(extraction.entities)
                if isinstance(extraction.entities, dict)
                else {}
            )

            # ── P4: Apply clarification-slot resume / pre-fill ──
            if _resumed_from_clar and _resumed_clar_intent:
                extraction.intent = _resumed_clar_intent
                extraction.confidence = 1.0
                if not isinstance(extraction.entities, dict):
                    extraction.entities = {}
                for _k, _v in _resumed_clar_entities.items():
                    if _v is not None:
                        extraction.entities[_k] = _v
                logger.warning(
                    "[P4_CLAR] resumed intent=%s entities=%s",
                    _resumed_clar_intent,
                    list(_resumed_clar_entities.keys()),
                )
            elif _prefilled_period:
                if not isinstance(extraction.entities, dict):
                    extraction.entities = {}
                if not extraction.entities.get("period"):
                    extraction.entities["period"] = _prefilled_period
                    logger.warning("[P4_CLAR] pre-filled period from slot residue")

            # ── P4: Fresh emit for period-dependent intents w/o period ──
            # Fires when (a) no pending slot was active, (b) LLM classified a
            # canonical period-dependent intent, (c) entities lack `period`.
            # Seeds slot + returns clarification question.
            _P4_PERIOD_INTENTS = {
                # Old seeded names (kept for backward compat with seeded tests)
                "calc_sum_ar",
                "calc_sum_ap",
                "query_ar_summary",
                "query_ap_summary",
                # Canonical names (natural LLM output, post-C3 remap targets)
                "calc_sum_invoices_outstanding",
                "calc_sum_bills_outstanding",
                "query_ar_outstanding",
                "query_ap_outstanding",
                # Rank intents (pass through to rank templates)
                "calc_rank_customers_by_ar",
                "calc_rank_vendors_by_ap",
                # Expense period-dependent
                "query_expenses_summary",
                "calc_sum_expenses",
                # Aging variants (Gemini sometimes emits query_ar_aging for bare
                # "piutang total" — same period-dependence as outstanding)
                "query_ar_aging",
                "query_ap_aging",
            }
            try:
                _ext_intent = getattr(extraction, "intent", "") or ""
                _ext_ents = (
                    dict(extraction.entities)
                    if isinstance(extraction.entities, dict)
                    else {}
                )
                _sess_id_fresh = (
                    getattr(tool_executor, "session_id", None)
                    if tool_executor
                    else None
                )
                if (
                    not _resumed_from_clar
                    and not _prefilled_period
                    and _ext_intent in _P4_PERIOD_INTENTS
                    and not _ext_ents.get("period")
                    and _sess_id_fresh
                ):
                    # Batch 3 Bucket 1: try inline period parse BEFORE emitting
                    # clarification. entity_extractor often misses period tokens
                    # like "bulan ini" / "30 hari terakhir"; reuse the slot-fill
                    # resolver as the canonical fallback. Skip clarification when
                    # period is detectable inline (no residue beyond threshold).
                    from .clarification_slots import try_fill_period_slot

                    _inline_fill = try_fill_period_slot(user_text)

                    # P4.1 (ADR addendum): sticky read.
                    # If user_text doesn't mention a period (inline-fill missed),
                    # check session-level sticky period (30-min TTL). Honors R4:
                    # never short-circuit when user provided period tokens — let
                    # inline-fill / D6 residue handling arbitrate.
                    _sticky_hit = False
                    if (
                        not _inline_fill.filled
                        and _state is not None
                        and getattr(_state, "current_period", None)
                        and getattr(_state, "current_period_expires_at", None)
                    ):
                        try:
                            from datetime import (
                                datetime as _dt_mod,
                                timezone as _tz_mod,
                            )
                            import json as _json_p41

                            _exp = _state.current_period_expires_at
                            _now_p41 = _dt_mod.now(_tz_mod.utc)
                            if _exp and _exp > _now_p41:
                                _sticky_raw = _state.current_period
                                _sticky_dict = (
                                    _sticky_raw
                                    if isinstance(_sticky_raw, dict)
                                    else _json_p41.loads(_sticky_raw)
                                )
                                extraction.entities["period"] = _sticky_dict
                                _ext_ents["period"] = _sticky_dict
                                _prefilled_period = _sticky_dict
                                _sticky_hit = True
                                logger.warning(
                                    "[P4_STICKY] hit session=%s period=%s",
                                    _sess_id_fresh,
                                    _sticky_dict.get("label")
                                    if isinstance(_sticky_dict, dict)
                                    else _sticky_dict,
                                )
                        except Exception as _sticky_err:
                            logger.warning(
                                "[P4_STICKY] read failed (non-fatal): %s",
                                _sticky_err,
                            )

                    if _sticky_hit:
                        pass  # period injected from sticky; skip emit
                    elif _inline_fill.filled and not _inline_fill.has_residue:
                        extraction.entities["period"] = _inline_fill.resolved_value
                        _ext_ents["period"] = _inline_fill.resolved_value
                        _label = (
                            _inline_fill.resolved_value.get("label")
                            if isinstance(_inline_fill.resolved_value, dict)
                            else _inline_fill.resolved_value
                        )
                        # P4.1: sticky WRITE — persist resolved period for
                        # subsequent turns (30-min TTL).
                        try:
                            from datetime import (
                                datetime as _dt_w,
                                timedelta as _td_w,
                                timezone as _tz_w,
                            )
                            import json as _json_w

                            _expires_w = _dt_w.now(_tz_w.utc) + _td_w(minutes=30)
                            await tool_executor.session_manager.update_state(
                                _sess_id_fresh,
                                current_period=_json_w.dumps(
                                    _inline_fill.resolved_value
                                ),
                                current_period_expires_at=_expires_w,
                            )
                            logger.warning(
                                "[P4_STICKY] write session=%s period=%s ttl=30min",
                                _sess_id_fresh,
                                _label,
                            )
                        except Exception as _w_err:
                            logger.warning(
                                "[P4_STICKY] write failed (non-fatal): %s",
                                _w_err,
                            )
                        logger.warning(
                            "[P4_CLAR] inline period skip-emit intent=%s session=%s period=%s",
                            _ext_intent,
                            _sess_id_fresh,
                            _label,
                        )
                    else:
                        from .clarification_slots import emit_period_clarification
                        from .db_utils import get_session_db_pool as _fresh_pool_fn

                        _fresh_db = await _fresh_pool_fn()
                        await emit_period_clarification(
                            db=_fresh_db,
                            session_id=_sess_id_fresh,
                            parent_intent=_ext_intent,
                            parent_entities={
                                k: v for k, v in _ext_ents.items() if k != "period"
                            },
                        )
                        _clar_event = "slot_emitted"
                        logger.warning(
                            "[P4_CLAR] fresh emit intent=%s session=%s",
                            _ext_intent,
                            _sess_id_fresh,
                        )
                        return AgentResponse(
                            message_type="text",
                            content=(
                                "Untuk periode kapan? Misal: bulan ini, "
                                "30 hari terakhir, April 2026."
                            ),
                        )
            except Exception as _fresh_err:
                logger.warning(
                    "[P4_CLAR] fresh emit failed (non-fatal): %s", _fresh_err
                )
            # ── end P4 fresh emit ──

            # ── P4 Downstream: Remap canonical clarification intents to executable ──
            # P4 emit whitelist uses canonical names (calc_sum_ar, query_ar_summary, etc.)
            # that are not registered in CALCULATION_TEMPLATES / PIPELINE_ENABLED_INTENTS.
            # After resume (period filled), remap to the real pipeline intent so dispatch
            # hits the AR/AP handler instead of falling through to the generic agent loop.
            # Only applies when P4 resumed; fresh emits returned already.
            if _resumed_from_clar and isinstance(extraction.intent, str):
                # Map P4 canonical clarification intents (unregistered in
                # templates/pipeline) to registered executable intents. AR/AP
                # outstanding totals map to single-numeric calc templates;
                # rank intents already exist so pass through unchanged.
                _P4_INTENT_REMAP = {
                    "calc_sum_ar": "calc_sum_invoices_outstanding",
                    "calc_sum_ap": "calc_sum_bills_outstanding",
                    "query_ar_summary": "query_ar_outstanding",
                    "query_ap_summary": "query_ap_outstanding",
                }
                _remapped = _P4_INTENT_REMAP.get(extraction.intent)
                if _remapped:
                    logger.warning(
                        "[P4_CLAR] downstream remap %s -> %s (period=%s)",
                        extraction.intent,
                        _remapped,
                        (extraction.entities or {}).get("period"),
                    )
                    extraction.intent = _remapped
            # ── end P4 downstream remap ──

        # ═══ PHASE 1: LLM Router Shadow (async, zero latency impact) ═══
        try:
            from .llm_intent_router import LLMIntentRouter

            _shadow_router = LLMIntentRouter(self.router)
            _shadow_wf_state = None
            if tool_executor and tool_executor.session_id:
                try:
                    from .workflow_engine import WorkflowEngine
                    from .db_utils import get_session_db_pool as _sh_pool

                    _sh_db = await _sh_pool()
                    _sh_wf = WorkflowEngine(
                        _sh_db,
                        context.tenant_id,
                        getattr(context, "user_id", ""),
                        getattr(context, "auth_token", ""),
                    )
                    _sh_wf_state_obj = await _sh_wf.get_state(
                        tool_executor.session_id, "crud_form"
                    )
                    if _sh_wf_state_obj and _sh_wf_state_obj.status == "active":
                        _shadow_wf_state = {
                            "intent": _sh_wf_state_obj.data.get("intent", ""),
                            "phase": getattr(_sh_wf_state_obj, "current_state", ""),
                        }
                except Exception:
                    pass

            # Capture extraction state before async closure to avoid scope issues
            _sh_regex_intent = extraction.intent if extraction else "unknown"
            _sh_regex_conf = extraction.confidence if extraction else 0.0

            async def _run_shadow():
                try:
                    _sh_result = await _shadow_router.route(
                        user_text=user_text,
                        conversation_history=conversation_history[-10:]
                        if conversation_history
                        else None,
                        workflow_state=_shadow_wf_state,
                    )
                    _agrees = _sh_result.intent == _sh_regex_intent
                    logger.warning(
                        "[SHADOW] llm=%s(%.2f) regex=%s(%.2f) agree=%s ready=%s [%dms]",
                        _sh_result.intent,
                        _sh_result.confidence,
                        _sh_regex_intent,
                        _sh_regex_conf,
                        _agrees,
                        _sh_result.ready,
                        _sh_result.latency_ms,
                    )

                except Exception as _sh_err:
                    import traceback as _tb

                    logger.warning("[SHADOW] Failed: %s\n%s", _sh_err, _tb.format_exc())

            import asyncio

            asyncio.create_task(_run_shadow())
        except Exception as _shadow_init_err:
            logger.warning("[SHADOW] Init failed: %s", _shadow_init_err)

        # ─── Post-shadow: telemetry + REC + guards (runs UNCONDITIONALLY) ───
        # NOTE: Previously this block was nested inside the `except` clause above,
        # meaning REC resolver + ARAP guard only ran when shadow init failed (rare).
        # Moved out so multi-turn follow-ups (pronoun "dia", ordinal "yang pertama") work.
        if extraction is not None:
            _tel_gemini_ms = (
                int((_tel_extraction_end - _tel_extraction_start) * 1000)
                if _tel_extraction_end and _tel_extraction_start
                else 0
            )
            _tel_guard = "none"
            _tel_guard_from = None
            _tel_guard_to = None
            _tel_decision_source = "gemini"
            _tel_guard_matches = {}

            if not isinstance(extraction.entities, dict):
                extraction.entities = {}

            # 2.5 REC: Domain-aware follow-up routing (conservative: <4 words, no domain keyword)
            if _state:
                _rec_last_domain = getattr(_state, "last_domain", None)
                if _rec_last_domain:
                    _rec_words = user_text.strip().split()
                    _rec_domain_kws = {
                        "piutang",
                        "hutang",
                        "utang",
                        "stok",
                        "bank",
                        "biaya",
                        "faktur",
                        "vendor",
                        "pelanggan",
                        "customer",
                        "barang",
                        "item",
                        "saldo",
                        "pengeluaran",
                        "penjualan",
                        "pembelian",
                        "rekening",
                        "gudang",
                        "invoice",
                        "bill",
                        "expense",
                        "payment",
                        "journal",
                    }
                    _rec_has_kw = any(
                        w.lower().rstrip("?.,!") in _rec_domain_kws for w in _rec_words
                    )
                    if len(_rec_words) < 4 and not _rec_has_kw:
                        _REC_FOLLOWUP = {
                            "ar": "query_ar_invoices",
                            "ap": "query_ap_outstanding",
                            "customer": "query_customers_list",
                            "vendor": "query_vendors_list",
                            "items": "query_items_list",
                            "bank": "query_bank_accounts_list",
                            "expense": "query_expenses_list",
                        }
                        _rec_followup = _REC_FOLLOWUP.get(_rec_last_domain)
                        if _rec_followup:
                            extraction.intent = _rec_followup
                            extraction.confidence = 0.9
                            _tel_decision_source = "rec_followup"
                            logger.warning(
                                "[REC_FOLLOWUP] '%s' (%d words) → %s (domain=%s)",
                                user_text[:30],
                                len(_rec_words),
                                _rec_followup,
                                _rec_last_domain,
                            )

            # 2.6 REC: Pronoun + ordinal resolution from session
            if _state:
                try:
                    from .entity_resolver import EntityResolver
                    from .llm_intent_router import (
                        _proper_noun_matches as _bug1_pnm_2,
                    )

                    _rec_resolved = EntityResolver.resolve_from_session(
                        user_text, _state
                    )
                    if _rec_resolved:
                        # BUG #1 FIX: skip name+id pair on proper noun mismatch.
                        _name_to_id_2 = {
                            "customer_name": "customer_id",
                            "vendor_name": "vendor_id",
                            "item_name": "item_id",
                        }
                        _skip_ids_2 = set()
                        _skipped_any_2 = False
                        for _name_key, _id_key in _name_to_id_2.items():
                            _candidate = _rec_resolved.get(_name_key)
                            if _candidate and not _bug1_pnm_2(
                                user_text or "", str(_candidate)
                            ):
                                _skip_ids_2.add(_name_key)
                                _skip_ids_2.add(_id_key)
                                _skipped_any_2 = True
                                logger.warning(
                                    "[BUG1_GUARD] orchestrator skipped REC merge %s=%r user_text=%r (proper noun mismatch)",
                                    _name_key,
                                    str(_candidate)[:50],
                                    (user_text or "")[:120],
                                )
                        for _rk, _rv in _rec_resolved.items():
                            if _rk in _skip_ids_2:
                                continue
                            if _rk != "_resolved_item" and not extraction.entities.get(
                                _rk
                            ):
                                extraction.entities[_rk] = _rv
                        logger.warning(
                            "[REC_RESOLVE] Merged: %s%s",
                            list(_rec_resolved.keys()),
                            " (some skipped by BUG1_GUARD)" if _skipped_any_2 else "",
                        )
                except Exception as _rec_err:
                    logger.warning("[REC_RESOLVE] Failed: %s", _rec_err)

            # ── P2 GUARD ARBITRATION (matrix v1.0) ─────────────────────────
            # Replaces legacy sequential cascade with GuardArbiter primitive.
            # CRUD_GUARD deferred — legacy block retained below.
            # Ref: docs/plans/2026-04-22-guard-arbiter-phase-a-diffs.md
            from .entity_extractor import classify_query_intent
            from .guard_arbiter import GuardMatch

            _qci_guard, _qci_entity_name, _ = classify_query_intent(user_text)

            _ARAP_CRITICAL = {
                "query_ar_outstanding",
                "query_ar_invoices",
                "query_ap_outstanding",
                "query_customer_ar",
                "query_vendor_ap",
            }
            _MFG_INTENTS = {
                "query_bom_list",
                "query_bom_detail",
                "query_bom_cost_breakdown",
                "query_bom_materials_required",
                "query_work_order_list",
                "query_work_order_detail",
                "query_work_order_cost_analysis",
                "query_production_active",
                "query_production_schedule",
                "query_material_issues",
                "query_fg_receipts",
                "query_work_center_list",
            }
            _LIST_VS_OVERDUE = {
                "query_customers_list": "query_customers_with_overdue",
                "query_vendors_list": "query_vendors_with_overdue",
            }
            _SUMMARY_INTENTS = {"query_ap_outstanding", "query_ar_outstanding"}
            _ENTITY_INTENTS = {"query_vendor_ap", "query_customer_ar"}
            # Duplicated from _handle_contextual_drill_down _DRILLDOWN_MAP
            # (Phase-B correction: real membership check, not mere last_intent presence)
            _DRILLDOWN_MAP_KEYS = {
                "query_ap_outstanding",
                "query_ar_outstanding",
                "query_bills_summary",
                "query_sales_invoices_summary",
                "query_expenses_summary",
                # Bug #1.5: customer/vendor AR/AP drill-down
                "query_customer_ar",
                "query_vendor_ap",
                "calc_rank_customers_by_ar",
                "calc_rank_vendors_by_ap",
            }

            # Build session_state-like dict for arbiter
            _arb_state = {}
            # Bug #1.5: ensure _state is loaded — earlier code only loads when
            # _intent in (ACTION, SIMPLE_READ); for COMPLEX_READ etc. _state stays None.
            if (
                _state is None
                and tool_executor
                and getattr(tool_executor, "session_manager", None)
                and getattr(tool_executor, "session_id", None)
            ):
                try:
                    _state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                except Exception:
                    _state = None
            try:
                _arb_state["last_domain"] = (
                    (_state or {}).get("last_domain")
                    if isinstance(_state, dict)
                    else getattr(_state, "last_domain", None)
                    if _state is not None
                    else None
                )
            except Exception:
                _arb_state["last_domain"] = None
            try:
                if isinstance(_state, dict):
                    _arb_state["pending_clarification"] = _state.get(
                        "pending_clarification"
                    )
                    _arb_last_intent = _state.get("last_intent") or _state.get(
                        "last_action_type"
                    )
                elif _state is not None:
                    # Bug #1.5: SessionState object (not dict) — read attrs
                    _arb_state["pending_clarification"] = getattr(
                        _state, "pending_clarification", None
                    )
                    _arb_last_intent = getattr(_state, "last_intent", None) or getattr(
                        _state, "last_action_type", None
                    )
                else:
                    _arb_last_intent = None
            except Exception:
                _arb_last_intent = None

            _gmatches: dict = {}

            # REFORMAT
            if (
                _qci_guard == "reformat_as_table"
                and extraction.intent != "reformat_as_table"
            ):
                _gmatches["REFORMAT_GUARD"] = GuardMatch(
                    "REFORMAT_GUARD", "reformat_as_table"
                )

            # DRILL
            if _qci_guard in ("contextual_drill_down", "drilldown_table"):
                _ctx_ok = _arb_last_intent in _DRILLDOWN_MAP_KEYS
                if extraction.intent not in (
                    "contextual_drill_down",
                    "drilldown_table",
                    "reformat_as_table",
                ):
                    _gmatches["DRILL_GUARD"] = GuardMatch(
                        "DRILL_GUARD", _qci_guard, metadata={"context_ok": _ctx_ok}
                    )

            # CALC
            if (
                _qci_guard
                and _qci_guard.startswith("calc_")
                and extraction.intent != _qci_guard
            ):
                _gmatches["CALC_GUARD"] = GuardMatch(
                    "CALC_GUARD", _qci_guard, metadata={"same_family": False}
                )

            # MFG
            if (
                _qci_guard
                and _qci_guard in _MFG_INTENTS
                and extraction.intent != _qci_guard
            ):
                _gmatches["MFG_GUARD"] = GuardMatch("MFG_GUARD", _qci_guard)

            # ARAP (+ nested SUMMARY)
            if _qci_guard in _ARAP_CRITICAL:
                _same_prefix = (
                    extraction.intent.startswith("query_ar_")
                    and _qci_guard.startswith("query_ar_")
                ) or (
                    extraction.intent.startswith("query_ap_")
                    and _qci_guard.startswith("query_ap_")
                )
                _gmatches["ARAP_GUARD"] = GuardMatch(
                    "ARAP_GUARD", _qci_guard, metadata={"same_family": _same_prefix}
                )
                if (
                    _qci_guard in _SUMMARY_INTENTS
                    and extraction.intent in _ENTITY_INTENTS
                    and not (extraction.entities or {}).get("vendor_name")
                    and not (extraction.entities or {}).get("customer_name")
                    and not (extraction.entities or {}).get("name")
                ):
                    _gmatches["ARAP_SUMMARY_GUARD"] = GuardMatch(
                        "ARAP_SUMMARY_GUARD", _qci_guard
                    )

            # LIST
            for _li, _oi in _LIST_VS_OVERDUE.items():
                if _qci_guard == _li and extraction.intent == _oi:
                    _gmatches["LIST_GUARD"] = GuardMatch("LIST_GUARD", _li)
                    break

            # QUERY_BOOST (weak-fallback; skipped when stronger guard present handled by arbiter)
            if (
                _qci_guard
                and _qci_guard not in _ARAP_CRITICAL
                and not _qci_guard.startswith("calc_")
                and _qci_guard not in _MFG_INTENTS
                and _qci_guard
                not in ("reformat_as_table", "contextual_drill_down", "drilldown_table")
            ):
                _gmatches["QUERY_BOOST"] = GuardMatch("QUERY_BOOST", _qci_guard)

            _decision = self.guard_arbiter.decide(
                llm_intent=extraction.intent,
                llm_confidence=extraction.confidence,
                llm_domain=getattr(extraction, "domain", None),
                llm_needs_escalation=extraction.needs_escalation,
                guard_matches=_gmatches,
                session_state=_arb_state,
                user_text=user_text,
                context_hint=bool(_context_hint),
            )

            _llm_intent_orig = extraction.intent
            _llm_conf_orig = extraction.confidence

            if _decision.winner not in ("LLM", "REC", "NO_GUARD", "PENDING_CLAR"):
                extraction.intent = _decision.final_intent
                extraction.confidence = _decision.final_confidence
                extraction.needs_escalation = False
                if _qci_entity_name and not (extraction.entities or {}).get("name"):
                    if not isinstance(extraction.entities, dict):
                        extraction.entities = {}
                    extraction.entities["name"] = _qci_entity_name
                _tel_guard = _decision.winner.lower()
                _tel_guard_from = _decision.guard_from
                _tel_guard_to = _decision.guard_to
                _tel_decision_source = _decision.winner.lower()
                _tel_guard_matches.update(_decision.guard_matches)
                logger.warning(
                    "[GUARD_ARBITER] winner=%s %s -> %s policy=%s conflict=%s",
                    _decision.winner,
                    _llm_intent_orig,
                    _decision.final_intent,
                    _decision.policy_applied,
                    _decision.conflict,
                )

            _tel_guard_arbitration = {
                "winner": _decision.winner,
                "final_intent": _decision.final_intent,
                "final_confidence": _decision.final_confidence,
                "guard_matches": _decision.guard_matches,
                "policy_applied": _decision.policy_applied,
                "conflict": _decision.conflict,
                "llm_intent_original": _llm_intent_orig,
                "llm_confidence_original": _llm_conf_orig,
            }

            # 4. CRUD GUARD (deferred — legacy block retained verbatim)
            from .entity_extractor import classify_crud_intent

            _code_intent, _code_entity_name, _code_name_field = classify_crud_intent(
                user_text
            )

            if _code_intent:
                is_crud = extraction.intent.startswith(
                    ("create_", "update_", "delete_", "void_", "reverse_")
                )
                if not is_crud or extraction.intent != _code_intent:
                    _tel_guard = "crud_guard"
                    _tel_guard_from = extraction.intent
                    _tel_guard_to = _code_intent
                    _tel_decision_source = "crud_guard"
                    _tel_guard_matches["crud_guard"] = _code_intent
                    logger.warning(
                        "[CRUD_GUARD] %s → %s", extraction.intent, _code_intent
                    )
                    extraction.intent = _code_intent
                    extraction.confidence = 1.0
                    extraction.needs_escalation = False
                if _code_entity_name and _code_name_field:
                    if not isinstance(extraction.entities, dict):
                        extraction.entities = {}
                    if not extraction.entities.get(_code_name_field):
                        extraction.entities[_code_name_field] = _code_entity_name

            # 6. DE-ESCALATION via arbiter helper
            _new_esc, _de_fired = self.guard_arbiter.apply_de_escalate(
                intent=extraction.intent,
                needs_escalation=extraction.needs_escalation,
                context_hint=bool(_context_hint),
                is_pipeline_enabled_fn=is_pipeline_enabled,
            )
            if _de_fired:
                _tel_guard = "de_escalate"
                _tel_guard_from = extraction.intent
                _tel_decision_source = "de_escalate"
                logger.warning("[DE_ESCALATE] %s escalation cleared", extraction.intent)
            extraction.needs_escalation = _new_esc

            # ── DOC_DETAIL_GUARD: code classifier doc number → override to detail query ──
            if _qci_guard and _qci_guard.endswith("_detail") and _qci_entity_name:
                if extraction.intent != _qci_guard:
                    logger.warning(
                        "[DOC_DETAIL_GUARD] %s -> %s (doc_ref=%s)",
                        extraction.intent,
                        _qci_guard,
                        _qci_entity_name,
                    )
                    _tel_guard = "doc_detail_guard"
                    _tel_guard_from = extraction.intent
                    _tel_guard_to = _qci_guard
                    _tel_guard_matches["doc_detail_guard"] = _qci_guard
                extraction.intent = _qci_guard
                extraction.confidence = 1.0
                extraction.entities["name"] = _qci_entity_name

            logger.warning(
                "[CLASSIFY_FINAL] intent=%s confidence=%.2f",
                extraction.intent,
                extraction.confidence,
            )

            # P5: Emit intent_classified SSE meta event (test correlation).
            # Fires AFTER all guards lock final_intent, BEFORE response generation.
            # Fire-and-forget: never blocks response even on failure.
            try:
                import uuid as _uuid_ic

                _req_id_ic = str(_uuid_ic.uuid4())
                await emit(
                    "intent_classified",
                    {
                        "request_id": _req_id_ic,
                        "final_intent": str(extraction.intent or ""),
                        "decision_source": str(_tel_decision_source or "unknown"),
                        "confidence": float(extraction.confidence or 0.0),
                    },
                )
            except Exception:
                pass

            # ── Telemetry: fire-and-forget log ──
            _tel_guard_conflict = len(_tel_guard_matches) > 1
            try:
                from .telemetry import IntentTelemetry, estimate_cost
                from .db_utils import get_session_db_pool as _tel_get_pool

                _tel_pool = await _tel_get_pool()
                _tel = IntentTelemetry(_tel_pool, context.tenant_id)

                _tel_session_id = (
                    getattr(tool_executor, "session_id", None)
                    if tool_executor
                    else None
                )
                _tel_conv_id = (
                    getattr(tool_executor, "conversation_id", None)
                    if tool_executor
                    else None
                )

                if _tel_session_id:
                    asyncio.create_task(_tel.detect_correction(_tel_session_id))

                asyncio.create_task(
                    _tel.log_decision(
                        user_text=user_text,
                        conversation_id=_tel_conv_id,
                        session_id=_tel_session_id,
                        gemini_intent=_tel_raw_intent,
                        gemini_confidence=_tel_raw_conf,
                        gemini_entities=_tel_raw_entities,
                        gemini_latency_ms=_tel_gemini_ms,
                        guard_triggered=_tel_guard,
                        guard_from=_tel_guard_from,
                        guard_to=_tel_guard_to,
                        guard_conflict=_tel_guard_conflict,
                        guard_conflict_detail=_tel_guard_matches
                        if _tel_guard_conflict
                        else None,
                        final_intent=extraction.intent,
                        final_confidence=extraction.confidence,
                        decision_source=_tel_decision_source,
                        context_hint_used=bool(_context_hint),
                        last_action_type=getattr(_state, "last_action_type", None)
                        if _state
                        else None,
                        pipeline_or_agent="pending",
                        model_used=_extract_model or "gemini-2.5-flash-lite",
                        total_latency_ms=_tel_gemini_ms,
                        estimated_cost_usd=estimate_cost(
                            _extract_model or "gemini-2.5-flash-lite", 2000, 200
                        ),
                        input_tokens=2000,
                        output_tokens=200,
                        response_type="pending",
                        response_length=0,
                        guard_arbitration=locals().get("_tel_guard_arbitration"),
                        clarification_event=_clar_event,
                    )
                )
            except Exception:
                pass  # NEVER block on telemetry

            # ═══ END LLM-FIRST CLASSIFICATION ═══

            # Calculation pipeline — code-driven numerics (zero LLM compute)
            # NOTE: When USE_LLM_ROUTER is enabled, calc/query pipelines below are SKIPPED.
            # LLM_ROUTER_PRIMARY handles dispatch with better accuracy. The guards above
            # (ARAP, CALC, CRUD, QUERY_BOOST) still apply and correct extraction.intent,
            # but actual pipeline dispatch defers to LLM_ROUTER_PRIMARY.
            if extraction.intent.startswith("calc_") and is_pipeline_enabled(
                extraction.intent
            ):
                from .calculation_engine import (
                    is_calculation_intent,
                    get_calculation_template,
                    execute_calculation,
                    format_calculation_result,
                )

                if is_calculation_intent(extraction.intent):
                    logger.warning(
                        "[CALC_PIPELINE] Routing to calculation engine: intent=%s",
                        extraction.intent,
                    )
                    _calc_template = get_calculation_template(extraction.intent)
                    _calc_result = await execute_calculation(
                        _calc_template,
                        auth_token=getattr(context, "auth_token", "") or "",
                        tenant_id=context.tenant_id,
                    )
                    if _calc_result.get("type") != "error":
                        _calc_text = format_calculation_result(_calc_result)
                        # Save state for follow-up context
                        if (
                            tool_executor
                            and hasattr(tool_executor, "session_manager")
                            and getattr(tool_executor, "session_id", None)
                        ):
                            try:
                                # REC population: persist ranked items + active_entity
                                # so pronoun ("dia") + ordinal ("yang pertama") follow-ups resolve.
                                _rec_kw = {}
                                _rec_items = _calc_result.get("rec_items") or []
                                if _rec_items:
                                    _rec_kw["last_response_items"] = _rec_items[:10]
                                    # Infer domain from intent
                                    _intent = extraction.intent
                                    _dom = None
                                    _etype = None
                                    if "customer" in _intent or "_ar" in _intent:
                                        _dom, _etype = "ar", "customer"
                                    elif "vendor" in _intent or "_ap" in _intent:
                                        _dom, _etype = "ap", "vendor"
                                    elif "item" in _intent:
                                        _dom, _etype = "items", "item"
                                    elif "expense" in _intent:
                                        _dom, _etype = "expenses", "expense"
                                    if _dom:
                                        _rec_kw["last_domain"] = _dom
                                    # active_entity = top-ranked item (for "dia", "nya")
                                    _top = _rec_items[0]
                                    if _etype and _top.get("_name"):
                                        _rec_kw["active_entity"] = {
                                            "type": _etype,
                                            "name": _top["_name"],
                                            "id": _top.get("_id"),
                                        }
                                await tool_executor.session_manager.update_state(
                                    tool_executor.session_id,
                                    last_action_type=extraction.intent,
                                    last_action_result={
                                        "response_text": _calc_text[:2000]
                                    },
                                    **_rec_kw,
                                )
                            except Exception:
                                pass
                        # ECM: ingest calc_rank result
                        try:
                            if hasattr(self, "_ecm") and _rec_items:
                                for _ci in _rec_items[:5]:
                                    if isinstance(_ci, dict):
                                        for (
                                            _cei
                                        ) in self._ecm._extract_entities_from_dict(
                                            _ci, extraction.intent
                                        ):
                                            self._ecm.push_entity(_cei)
                                # Also push active_entity directly
                                if _rec_kw.get("active_entity"):
                                    _ae2 = _rec_kw["active_entity"]
                                    from .entity_context_manager import Entity

                                    self._ecm.push_entity(
                                        Entity(
                                            type=_ae2["type"],
                                            id=_ae2.get("id"),
                                            name=_ae2.get("name"),
                                            source=extraction.intent,
                                            turn=self._ecm.current_turn,
                                        )
                                    )
                                logger.warning(
                                    "[ECM] calc_rank ingested: %s",
                                    self._ecm.get_stats(),
                                )
                        except Exception:
                            pass

                        return AgentResponse(
                            message_type="TEXT",
                            content=_calc_text,
                            iterations=1,
                            model_used="calc_engine",
                            total_latency_ms=int(
                                (_time.monotonic() - _process_start) * 1000
                            ),
                        )
                    else:
                        logger.warning("[CALC_PIPELINE] Failed: %s", _calc_result)
                        extraction.needs_escalation = True

            # Query pipeline — before write pipeline
            # Follow-up guard: if escalation=True + session context + no code classifier match,
            # check for domain mismatch → route to agent loop instead of pipeline
            _skip_query_pipeline = False
            if (
                extraction.needs_escalation
                and not _code_intent  # code classifier did not match
                and tool_executor
                and getattr(tool_executor, "session_manager", None)
                and getattr(tool_executor, "session_id", None)
            ):
                try:
                    _fu_state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    _fu_last = getattr(_fu_state, "last_action_type", None)
                    if _fu_last:
                        # Domain mismatch check
                        _DOMAIN = {
                            "query_ar_outstanding": "ar",
                            "query_ar_invoices": "ar",
                            "query_sales_invoices_list": "ar",
                            "query_sales_invoice_detail": "ar",
                            "query_ap_outstanding": "ap",
                            "query_bills_list": "ap",
                            "query_bill_detail": "ap",
                            "query_bills_summary": "ap",
                            "query_item_detail": "items",
                            "query_items_list": "items",
                            "query_items_summary": "items",
                            "query_items_search": "items",
                            "query_customer_detail": "customer",
                            "query_customers_list": "customer",
                            "query_vendor_detail": "vendor",
                            "query_vendors_list": "vendor",
                            "query_expenses_list": "expenses",
                            "query_expense_detail": "expenses",
                            "query_bank_accounts_list": "bank",
                            "query_bank_account_detail": "bank",
                        }
                        _last_domain = _DOMAIN.get(_fu_last)
                        _new_domain = _DOMAIN.get(extraction.intent)
                        if _last_domain and _new_domain and _last_domain != _new_domain:
                            # Follow-up pattern check (pronouns, implicit references)
                            import re as _fu_re

                            _fu_patterns = [
                                r"\b(tersebut|itu|nya|mereka|dia)\b",
                                r"\b(yang tadi|yang barusan|tadi|sebelumnya)\b",
                                r"\b(dari siapa|yang mana|siapa\s+(yang|saja))\b",
                                r"^(minta|kasih|berikan)\b(?!.*(faktur|tagihan|piutang|hutang|barang|biaya))",
                                r"\b(data lengkap|detail|info|informasi)\b(?!.*\b[A-Z])",
                            ]
                            _is_followup = any(
                                _fu_re.search(p, user_text.lower())
                                for p in _fu_patterns
                            )
                            if _is_followup:
                                _skip_query_pipeline = True
                                logger.warning(
                                    "[FOLLOW_UP_GUARD] Domain mismatch: session=%s, extraction=%s → agent loop",
                                    _fu_last,
                                    extraction.intent,
                                )
                except Exception:
                    pass

            if (
                not USE_LLM_ROUTER
                and not _skip_query_pipeline
                and extraction.intent.startswith("query_")
                and is_pipeline_enabled(extraction.intent)
            ):
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
            if (
                extraction.intent in ("ambiguous", "chitchat", "unknown", "SIMPLE_READ")
                and tool_executor
                and tool_executor.session_manager
                and tool_executor.session_id
            ):
                try:
                    _route_state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    _route_pending = getattr(_route_state, "pending_intent", "") or ""
                    _route_payload = getattr(_route_state, "pending_payload", {}) or {}
                    if (
                        _route_pending
                        and _route_payload
                        and is_pipeline_enabled(_route_pending)
                    ):
                        logger.warning(
                            "[PIPELINE] Ambiguous override: extraction=%s -> pending=%s",
                            extraction.intent,
                            _route_pending,
                        )
                        extraction.intent = _route_pending
                        extraction.needs_escalation = False
                except Exception as _re:
                    logger.warning("[PIPELINE] Routing pending check failed: %s", _re)

            # ── Active workflow override: force pipeline routing ──
            if (
                not is_pipeline_enabled(extraction.intent)
                or extraction.needs_escalation
            ):
                if tool_executor and tool_executor.session_id:
                    try:
                        from .workflow_engine import WorkflowEngine
                        from .db_utils import get_session_db_pool as _rp_pool

                        _rp_db = await _rp_pool()
                        _rp_engine = WorkflowEngine(
                            _rp_db,
                            context.tenant_id,
                            getattr(context, "user_id", ""),
                            getattr(context, "auth_token", ""),
                        )
                        _rp_wf = await _rp_engine.get_state(
                            tool_executor.session_id, "crud_form"
                        )
                        if _rp_wf and _rp_wf.status == "active":
                            _rp_intent = _rp_wf.data.get("intent", "")
                            if _rp_intent and is_pipeline_enabled(_rp_intent):
                                logger.warning(
                                    "[PIPELINE] Active workflow override: %s -> %s",
                                    extraction.intent,
                                    _rp_intent,
                                )
                                extraction.intent = _rp_intent
                                extraction.needs_escalation = False
                    except Exception as _rp_err:
                        logger.warning(
                            "[PIPELINE] Workflow routing check failed: %s", _rp_err
                        )

            # ── Reformat-as-table: re-format last response ──
            # When USE_LLM_ROUTER is enabled, these dispatches are handled by LLM_ROUTER_PRIMARY
            # which has better context-aware classification. Skip code-classifier dispatch.
            if not USE_LLM_ROUTER:
                if extraction.intent == "contextual_drill_down":
                    logger.warning(
                        "[CONTEXTUAL_DRILL_DOWN] Routing to contextual drill-down handler"
                    )
                    return await self._handle_contextual_drill_down(
                        user_text=user_text,
                        context=context,
                        extraction=extraction,
                        tool_executor=tool_executor,
                        event_callback=event_callback,
                    )

                if extraction.intent == "drilldown_table":
                    logger.warning("[DRILLDOWN] Routing to drilldown handler")
                    return await self._handle_drilldown_table(
                        user_text=user_text,
                        context=context,
                        extraction=extraction,
                        tool_executor=tool_executor,
                        event_callback=event_callback,
                    )

                if extraction.intent == "reformat_as_table":
                    logger.warning("[REFORMAT] Routing to reformat handler")
                    return await self._handle_reformat_as_table(
                        user_text=user_text,
                        context=context,
                        tool_executor=tool_executor,
                        event_callback=event_callback,
                        conversation_history=conversation_history,
                    )

                if (
                    is_pipeline_enabled(extraction.intent)
                    and not extraction.needs_escalation
                ):
                    logger.warning(
                        "[PIPELINE] Routing to compiler pipeline: intent=%s confidence=%.2f",
                        extraction.intent,
                        extraction.confidence,
                    )

                    # ═══ WF_PRIORITY: Override intent to match active workflow ═══
                    if _wf_priority_intent:
                        extraction.intent = _wf_priority_intent
                        extraction.confidence = 0.5
                        logger.warning(
                            "[WF_PRIORITY] Overriding intent to %s for slot fill",
                            _wf_priority_intent,
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
                    extraction.intent,
                    extraction.confidence,
                    extraction.needs_escalation,
                )
        # ── END COMPILER PIPELINE ──

        # ═══ REC: Pronoun + Ordinal resolution (runs BEFORE dispatch) ═══
        # Resolves "dia", "nya", "yang pertama" etc. from session state
        # into concrete entity names/IDs merged into extraction.entities.
        if _state and extraction:
            try:
                from .entity_resolver import EntityResolver as _RecResolverPre
                from .llm_intent_router import (
                    _proper_noun_matches as _bug1_pnm_pre,
                )

                _rec_pre = _RecResolverPre.resolve_from_session(user_text, _state)
                if _rec_pre:
                    if not isinstance(extraction.entities, dict):
                        extraction.entities = {}
                    # BUG #1 FIX: skip name+id pair if user_text has a different
                    # explicit proper-noun.
                    _name_to_id_pre = {
                        "customer_name": "customer_id",
                        "vendor_name": "vendor_id",
                        "item_name": "item_id",
                    }
                    _skip_ids_pre = set()
                    _skipped_any_pre = False
                    for _name_key, _id_key in _name_to_id_pre.items():
                        _candidate = _rec_pre.get(_name_key)
                        if _candidate and not _bug1_pnm_pre(
                            user_text or "", str(_candidate)
                        ):
                            _skip_ids_pre.add(_name_key)
                            _skip_ids_pre.add(_id_key)
                            _skipped_any_pre = True
                            logger.warning(
                                "[BUG1_GUARD] orchestrator skipped REC_PRE merge %s=%r user_text=%r (proper noun mismatch)",
                                _name_key,
                                str(_candidate)[:50],
                                (user_text or "")[:120],
                            )
                    for _rk, _rv in _rec_pre.items():
                        if _rk in _skip_ids_pre:
                            continue
                        if _rk != "_resolved_item" and not extraction.entities.get(_rk):
                            extraction.entities[_rk] = _rv
                    logger.warning(
                        "[REC_RESOLVE_PRE] Merged: %s%s",
                        list(_rec_pre.keys()),
                        " (some skipped by BUG1_GUARD)" if _skipped_any_pre else "",
                    )
            except Exception as _rec_pre_err:
                logger.warning("[REC_RESOLVE_PRE] Failed: %s", _rec_pre_err)

        # ═══ PHASE 2: LLM ROUTER PRIMARY PATH (feature flagged) ═══
        # When USE_LLM_ROUTER=true, classify via LLM Router and dispatch to
        # calc/query/create pipelines. Falls through to existing agent loop on
        # fallback/failure (never breaks existing behavior).
        if USE_LLM_ROUTER and _intent in ("ACTION", "SIMPLE_READ"):
            try:
                _llm_extraction = await self._classify_via_llm_router(
                    user_text=user_text,
                    context=context,
                    tool_executor=tool_executor,
                    conversation_history=conversation_history,
                    db_pool=db_pool,
                )
            except Exception as _llm_primary_err:
                logger.warning(
                    "[LLM_ROUTER_PRIMARY] dispatch failed: %s", _llm_primary_err
                )
                _llm_extraction = None

            if _llm_extraction is not None:
                # REC: Merge pronoun/ordinal resolution into LLM extraction too
                if _state:
                    try:
                        from .entity_resolver import EntityResolver as _RecLLM
                        from .llm_intent_router import (
                            _proper_noun_matches as _bug1_pnm_rec,
                        )

                        _rec_llm = _RecLLM.resolve_from_session(user_text, _state)
                        if _rec_llm:
                            if not isinstance(_llm_extraction.entities, dict):
                                _llm_extraction.entities = {}
                            # BUG #1 FIX: if user_text has an explicit different
                            # proper-noun name reference, do NOT merge stale name
                            # fields from session resolver. Treat name fields and
                            # their corresponding *_id together so we don't end up
                            # with a name-id mismatch.
                            _name_to_id = {
                                "customer_name": "customer_id",
                                "vendor_name": "vendor_id",
                                "item_name": "item_id",
                            }
                            _skip_ids = set()
                            _skipped_any = False
                            for _name_key, _id_key in _name_to_id.items():
                                _candidate = _rec_llm.get(_name_key)
                                if _candidate and not _bug1_pnm_rec(
                                    user_text or "", str(_candidate)
                                ):
                                    _skip_ids.add(_name_key)
                                    _skip_ids.add(_id_key)
                                    _skipped_any = True
                                    logger.warning(
                                        "[BUG1_GUARD] orchestrator skipped REC merge %s=%r user_text=%r (proper noun mismatch)",
                                        _name_key,
                                        str(_candidate)[:50],
                                        (user_text or "")[:120],
                                    )
                            for _rk, _rv in _rec_llm.items():
                                if _rk in _skip_ids:
                                    continue
                                if (
                                    _rk != "_resolved_item"
                                    and not _llm_extraction.entities.get(_rk)
                                ):
                                    _llm_extraction.entities[_rk] = _rv
                            logger.warning(
                                "[REC_RESOLVE_LLM] Merged into llm_extraction: %s%s",
                                list(_rec_llm.keys()),
                                " (some skipped by BUG1_GUARD)" if _skipped_any else "",
                            )
                    except Exception as _rec_llm_err:
                        logger.warning("[REC_RESOLVE_LLM] Failed: %s", _rec_llm_err)

                from .entity_extractor import is_pipeline_enabled as _llm_is_pipe

                _lr_intent = _llm_extraction.intent or ""

                # ── Query regex override: trust deterministic regex over LLM ──
                # When regex classify_query_intent matched (confidence 1.0) and
                # LLM Router disagrees, trust regex for item-specific queries.
                if (
                    _qci_guard
                    and _qci_guard != _lr_intent
                    and _qci_guard.startswith(("query_item_", "query_item_sales"))
                ):
                    logger.warning(
                        "[QUERY_REGEX_OVERRIDE] Trusting regex %s over LLM %s",
                        _qci_guard,
                        _lr_intent,
                    )
                    _lr_intent = _qci_guard
                    _llm_extraction.intent = _qci_guard
                    if _qci_entity_name and not _llm_extraction.entities.get(
                        "item_name"
                    ):
                        _llm_extraction.entities["item_name"] = _qci_entity_name

                # ── Manufacturing intent trust: code classifier always wins ──
                _MFG_QUERY_INTENTS = {
                    "query_bom_list",
                    "query_bom_detail",
                    "query_bom_cost_breakdown",
                    "query_bom_materials_required",
                    "query_work_order_list",
                    "query_work_order_detail",
                    "query_work_order_cost_analysis",
                    "query_production_active",
                    "query_production_schedule",
                    "query_material_issues",
                    "query_fg_receipts",
                    "query_work_center_list",
                    "calc_count_work_orders_active",
                    "calc_count_bom_active",
                    "calc_count_work_orders_draft",
                    "calc_count_work_centers",
                    "calc_rank_work_orders_by_quantity",
                }
                if (
                    _qci_guard
                    and _qci_guard in _MFG_QUERY_INTENTS
                    and _qci_guard != _lr_intent
                ):
                    logger.warning(
                        "[MFG_OVERRIDE] Trusting code %s over LLM %s",
                        _qci_guard,
                        _lr_intent,
                    )
                    _lr_intent = _qci_guard
                    _llm_extraction.intent = _qci_guard
                    _llm_extraction.confidence = 1.0
                    _llm_extraction.needs_escalation = False
                    if _qci_entity_name:
                        if not isinstance(_llm_extraction.entities, dict):
                            _llm_extraction.entities = {}
                        _llm_extraction.entities["name"] = _qci_entity_name

                # ── Regex-trust override for sales document confusion ──
                # LLM often conflates pesanan/faktur/penawaran. Regex classifier is
                # deterministic on these Indonesian keywords — trust it when they disagree.
                _REGEX_TRUST_PAIRS = {
                    ("create_sales_order", "create_sales_invoice"),
                    ("create_quote", "create_sales_invoice"),
                    ("create_quote", "create_sales_order"),
                    ("create_sales_invoice", "create_sales_order"),
                    ("create_sales_invoice", "create_quote"),
                    ("create_sales_order", "create_quote"),
                }
                try:
                    from .entity_extractor import classify_crud_intent as _cci_override

                    _ovr_code_intent, _, _ = _cci_override(user_text)
                except Exception:
                    _ovr_code_intent = None
                if (
                    _ovr_code_intent
                    and _lr_intent
                    and (_ovr_code_intent, _lr_intent) in _REGEX_TRUST_PAIRS
                ):
                    logger.warning(
                        "[ROUTER_OVERRIDE] Trusting regex %s over LLM %s",
                        _ovr_code_intent,
                        _lr_intent,
                    )
                    _lr_intent = _ovr_code_intent
                    _llm_extraction.intent = _ovr_code_intent

                # ── Entity merge: code classifier entities → LLM router ──
                # LLM Router often returns correct intent but empty entities.
                # Code classifier (extraction) extracts entities reliably.
                # Merge code classifier entities into LLM extraction if missing.
                # BUG #1 FIX: gate name-field merges with proper-noun guard so
                # that a stale entity from history/state cannot override an
                # explicit different proper noun in the current user message.
                if extraction and extraction.entities:
                    from .llm_intent_router import (
                        _proper_noun_matches as _bug1_pnm_merge,
                    )

                    _name_fields = {"customer_name", "vendor_name", "item_name"}
                    for _ek, _ev in extraction.entities.items():
                        if _ek not in _llm_extraction.entities and _ev:
                            if _ek in _name_fields and not _bug1_pnm_merge(
                                user_text or "", str(_ev)
                            ):
                                logger.warning(
                                    "[BUG1_GUARD] orchestrator skipped merge %s=%r user_text=%r (proper noun mismatch)",
                                    _ek,
                                    str(_ev)[:50],
                                    (user_text or "")[:120],
                                )
                                continue
                            _llm_extraction.entities[_ek] = _ev
                            logger.warning(
                                "[LLM_ROUTER_PRIMARY] merged entity %s=%s from code classifier",
                                _ek,
                                str(_ev)[:50],
                            )

                # ── Entity inject from session state (pronoun follow-ups) ──
                # If extraction still has no entity but intent needs one, inject from session
                if (
                    not _llm_extraction.entities
                    and tool_executor
                    and tool_executor.session_id
                ):
                    try:
                        _sess = (
                            await tool_executor.session_manager.get_state(
                                tool_executor.session_id
                            )
                            if tool_executor.session_manager
                            else None
                        )
                        if _sess:
                            _lr_i = _llm_extraction.intent or ""
                            # BUG #1 FIX: proper-noun guard — if user_text contains a
                            # 2+ word Capitalized phrase that does NOT overlap with the
                            # active entity name, do NOT inject (user is referencing a
                            # different entity).
                            from .llm_intent_router import (
                                _proper_noun_matches as _bug1_pnm,
                            )

                            # Customer context for AR/customer queries
                            if _lr_i in (
                                "query_customer_ar",
                                "query_customer_detail",
                                "query_ar_invoices",
                                "query_sales_invoice_detail",
                                "query_sales_invoices_list",
                                "query_customer_invoices",
                            ) and getattr(_sess, "active_customer_name", None):
                                if _bug1_pnm(
                                    user_text or "", _sess.active_customer_name or ""
                                ):
                                    _llm_extraction.entities[
                                        "customer_name"
                                    ] = _sess.active_customer_name
                                    logger.warning(
                                        "[LLM_ROUTER_PRIMARY] injected customer_name=%s from session",
                                        _sess.active_customer_name,
                                    )
                                else:
                                    logger.warning(
                                        "[BUG1_GUARD] orchestrator skipped customer_name inject: active=%r user_text=%r (proper noun mismatch)",
                                        _sess.active_customer_name,
                                        (user_text or "")[:120],
                                    )
                            # Vendor context for AP/vendor queries
                            elif _lr_i in (
                                "query_vendor_ap",
                                "query_vendor_detail",
                                "query_ap_outstanding",
                                "query_bills_list",
                            ) and getattr(_sess, "active_vendor_name", None):
                                if _bug1_pnm(
                                    user_text or "", _sess.active_vendor_name or ""
                                ):
                                    _llm_extraction.entities[
                                        "vendor_name"
                                    ] = _sess.active_vendor_name
                                    logger.warning(
                                        "[LLM_ROUTER_PRIMARY] injected vendor_name=%s from session",
                                        _sess.active_vendor_name,
                                    )
                                else:
                                    logger.warning(
                                        "[BUG1_GUARD] orchestrator skipped vendor_name inject: active=%r user_text=%r (proper noun mismatch)",
                                        _sess.active_vendor_name,
                                        (user_text or "")[:120],
                                    )
                            # Item context for item queries
                            elif _lr_i in (
                                "query_item_detail",
                                "query_warehouse_stock",
                                "query_items_search",
                            ) and getattr(_sess, "active_items", None):
                                _items = _sess.active_items
                                if (
                                    isinstance(_items, list)
                                    and _items
                                    and isinstance(_items[0], dict)
                                ):
                                    _llm_extraction.entities["item_name"] = _items[
                                        0
                                    ].get("name", "")
                                    logger.warning(
                                        "[LLM_ROUTER_PRIMARY] injected item_name=%s from session",
                                        _items[0].get("name"),
                                    )
                    except Exception as _sess_err:
                        logger.warning(
                            "[LLM_ROUTER_PRIMARY] session entity inject failed: %s",
                            _sess_err,
                        )

                # ── DRILL_GUARD for LLM router: code classifier drill-down trumps LLM ──
                # The code classifier has session context and detects drill-downs
                # (e.g. "per faktur" after AP summary). The LLM router lacks session
                # context and may misroute (e.g. to AR invoices). Trust code classifier.
                if extraction and extraction.intent in (
                    "contextual_drill_down",
                    "drilldown_table",
                ):
                    if extraction.intent == "contextual_drill_down":
                        logger.warning(
                            "[LLM_ROUTER_DRILL_GUARD] code classifier drill-down trumps LLM intent=%s",
                            _lr_intent,
                        )
                        return await self._handle_contextual_drill_down(
                            user_text=user_text,
                            context=context,
                            extraction=extraction,
                            tool_executor=tool_executor,
                            event_callback=event_callback,
                        )
                    else:
                        logger.warning(
                            "[LLM_ROUTER_DRILL_GUARD] code classifier drilldown_table trumps LLM intent=%s",
                            _lr_intent,
                        )
                        return await self._handle_drilldown_table(
                            user_text=user_text,
                            context=context,
                            extraction=extraction,
                            tool_executor=tool_executor,
                            event_callback=event_callback,
                        )
                # ── Reformat + drill-down (dispatch to existing handlers) ──
                if _lr_intent == "reformat_as_table":
                    # Guard: if regex classifier found a specific query/calc intent,
                    # the user wants a NEW query formatted as table, not a reformat of last response.
                    from .entity_extractor import classify_query_intent as _rfg_qci

                    _rfg_intent, _rfg_entity, _ = _rfg_qci(user_text)
                    if _rfg_intent and _rfg_intent not in (
                        "reformat_as_table",
                        "contextual_drill_down",
                        "drilldown_table",
                    ):
                        logger.warning(
                            "[LLM_ROUTER_PRIMARY] reformat override -> %s (regex found specific query)",
                            _rfg_intent,
                        )
                        _llm_extraction.intent = _rfg_intent
                        _lr_intent = _rfg_intent
                        if _rfg_entity:
                            _llm_extraction.entities["name"] = _rfg_entity
                    else:
                        logger.warning("[LLM_ROUTER_PRIMARY] reformat_as_table")
                        return await self._handle_reformat_as_table(
                            user_text=user_text,
                            context=context,
                            tool_executor=tool_executor,
                            event_callback=event_callback,
                            conversation_history=conversation_history,
                        )
                if _lr_intent == "contextual_drill_down":
                    logger.warning("[LLM_ROUTER_PRIMARY] contextual_drill_down")
                    return await self._handle_contextual_drill_down(
                        user_text=user_text,
                        context=context,
                        extraction=_llm_extraction,
                        tool_executor=tool_executor,
                        event_callback=event_callback,
                    )

                # ── Calc pipeline ──
                if _lr_intent.startswith("calc_") and _llm_is_pipe(_lr_intent):
                    try:
                        from .calculation_engine import (
                            is_calculation_intent,
                            get_calculation_template,
                            execute_calculation,
                            format_calculation_result,
                        )

                        if is_calculation_intent(_lr_intent):
                            logger.warning(
                                "[LLM_ROUTER_PRIMARY] calc pipeline: %s", _lr_intent
                            )
                            _tpl = get_calculation_template(_lr_intent)
                            _calc_res = await execute_calculation(
                                _tpl,
                                auth_token=getattr(context, "auth_token", "") or "",
                                tenant_id=context.tenant_id,
                            )
                            if _calc_res.get("type") != "error":
                                _txt = format_calculation_result(_calc_res)
                                if (
                                    tool_executor
                                    and getattr(tool_executor, "session_manager", None)
                                    and tool_executor.session_id
                                ):
                                    try:
                                        # REC: populate session with ranked items for follow-up resolution
                                        _rec_kw2 = {}
                                        _rec_items2 = _calc_res.get("rec_items") or []
                                        if _rec_items2:
                                            _rec_kw2[
                                                "last_response_items"
                                            ] = _rec_items2[:10]
                                            _dom2 = None
                                            _etype2 = None
                                            if (
                                                "customer" in _lr_intent
                                                or "_ar" in _lr_intent
                                            ):
                                                _dom2, _etype2 = "ar", "customer"
                                            elif (
                                                "vendor" in _lr_intent
                                                or "_ap" in _lr_intent
                                            ):
                                                _dom2, _etype2 = "ap", "vendor"
                                            elif "item" in _lr_intent:
                                                _dom2, _etype2 = "items", "item"
                                            elif "expense" in _lr_intent:
                                                _dom2, _etype2 = "expenses", "expense"
                                            if _dom2:
                                                _rec_kw2["last_domain"] = _dom2
                                            _top2 = _rec_items2[0]
                                            if _etype2 and _top2.get("_name"):
                                                _rec_kw2["active_entity"] = {
                                                    "type": _etype2,
                                                    "name": _top2["_name"],
                                                    "id": _top2.get("_id"),
                                                }
                                        await (
                                            tool_executor.session_manager.update_state(
                                                tool_executor.session_id,
                                                last_action_type=_lr_intent,
                                                last_action_result={
                                                    "response_text": _txt[:2000]
                                                },
                                                **_rec_kw2,
                                            )
                                        )
                                    except Exception:
                                        pass
                                return AgentResponse(
                                    message_type="TEXT",
                                    content=_txt,
                                    iterations=1,
                                    model_used="llm_router+calc_engine",
                                    total_latency_ms=int(
                                        (_time.monotonic() - _process_start) * 1000
                                    ),
                                )
                    except Exception as _calc_err:
                        logger.warning(
                            "[LLM_ROUTER_PRIMARY] calc pipeline failed: %s", _calc_err
                        )

                # ── Query pipeline ──
                if _lr_intent.startswith("query_") and _llm_is_pipe(_lr_intent):
                    try:
                        from .direct_action_registry import get_query_action

                        if get_query_action(_lr_intent):
                            logger.warning(
                                "[LLM_ROUTER_PRIMARY] query pipeline: %s", _lr_intent
                            )
                            return await self._handle_query_pipeline(
                                user_text=user_text,
                                context=context,
                                extraction=_llm_extraction,
                                tool_executor=tool_executor,
                                event_callback=event_callback,
                            )
                    except Exception as _q_err:
                        import traceback as _tb_q

                        logger.warning(
                            "[LLM_ROUTER_PRIMARY] query pipeline failed: %s\n%s",
                            _q_err,
                            _tb_q.format_exc(),
                        )

                # ── CRUD / DirectAction pipeline ──
                if _lr_intent.startswith(
                    ("create_", "update_", "delete_", "void_", "reverse_")
                ) and _llm_is_pipe(_lr_intent):
                    try:
                        logger.warning(
                            "[LLM_ROUTER_PRIMARY] crud pipeline: %s", _lr_intent
                        )
                        return await self._handle_pipeline(
                            user_text=user_text,
                            context=context,
                            extraction=_llm_extraction,
                            conversation_history=conversation_history,
                            tool_executor=tool_executor,
                            event_callback=event_callback,
                        )
                    except Exception as _crud_err:
                        logger.warning(
                            "[LLM_ROUTER_PRIMARY] crud pipeline failed: %s", _crud_err
                        )

                # No pipeline match — fall through to agent loop with LLM Router intent
                logger.warning(
                    "[LLM_ROUTER_PRIMARY] no pipeline match for %s, falling to agent loop",
                    _lr_intent,
                )
        # ═══ END LLM ROUTER PRIMARY PATH ═══

        # Build messages — segmented system prompt (Phase 3A)
        # Segments loaded based on intent: CHITCHAT=~500tok, SIMPLE_READ=~2.5K, etc.
        system_msgs = build_system_messages(
            tenant_name=context.tenant_name,
            today=date.today().isoformat(),
            user_text=user_text,
            intent=_intent,
        )

        messages: List[LLMMessage] = [
            LLMMessage(role=msg["role"], content=msg["content"]) for msg in system_msgs
        ]

        # ── Bucket B1: inject L3 recent events on ACTION/ambiguous turns ──
        # Skip QUERY_* / CHITCHAT / MFG_* / REFORMAT per L3_INJECT_DENY_LIST.
        # Non-fatal: any failure must not break the LLM call.
        try:
            from .l3_prompt import (
                L3_INJECT_DENY_LIST,
                build_l3_context_block,
            )

            _b1_intent_lower = (_intent or "").lower()
            _b1_sm = (
                getattr(tool_executor, "session_manager", None)
                if tool_executor
                else None
            )
            _b1_sid = (
                getattr(tool_executor, "session_id", None) if tool_executor else None
            )
            if _b1_intent_lower in L3_INJECT_DENY_LIST:
                logger.debug(
                    "[B1] l3_skipped session=%s intent=%s reason=deny_list",
                    _b1_sid,
                    _b1_intent_lower,
                )
            elif _b1_sm and _b1_sid:
                _b1_events = await _b1_sm.get_recent_events(_b1_sid, limit=5)
                _b1_block = build_l3_context_block(_b1_events, max_age_seconds=1800)
                if _b1_block:
                    messages.append(
                        LLMMessage(
                            role="system",
                            content=f"## Recent session context\n{_b1_block}",
                        )
                    )
                    logger.warning(
                        "[B1] l3_injected session=%s intent=%s event_count=%d",
                        _b1_sid,
                        _b1_intent_lower,
                        _b1_block.count("\n") + 1,
                    )
                else:
                    logger.debug(
                        "[B1] l3_skipped session=%s intent=%s reason=no_events",
                        _b1_sid,
                        _b1_intent_lower,
                    )
        except (KeyError, TypeError, ValueError, AttributeError, ImportError) as _b1_e:
            logger.error(
                "l3_injection_failed session=%s intent=%s err=%s",
                getattr(tool_executor, "session_id", None) if tool_executor else None,
                (_intent or "").lower(),
                _b1_e,
                exc_info=True,
            )
            # Non-fatal — proceed without L3 context
        except Exception as _b1_e:  # asyncpg errors + anything unexpected
            logger.error(
                "l3_injection_failed_unexpected session=%s intent=%s err=%s",
                getattr(tool_executor, "session_id", None) if tool_executor else None,
                (_intent or "").lower(),
                _b1_e,
                exc_info=True,
            )
            # Non-fatal — proceed without L3 context

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

        # EntityContextManager (Phase 1+2: shadow ingest + active injection)
        from .entity_context_manager import (
            EntityContextManager,
            Entity,
            build_schema_needs_cache,
        )
        from .tool_registry import get_tools as get_all_tools

        _ecm = EntityContextManager()
        _ecm.advance_turn()
        build_schema_needs_cache(get_all_tools())  # cached after first call

        # Seed ECM from existing session state (persisted in Redis)
        if (
            tool_executor
            and getattr(tool_executor, "session_manager", None)
            and getattr(tool_executor, "session_id", None)
        ):
            try:
                _seed = await tool_executor.session_manager.get_state(
                    tool_executor.session_id
                )
                if _seed:
                    if getattr(_seed, "active_customer_id", None):
                        _ecm.push_entity(
                            Entity(
                                type="customer",
                                id=_seed.active_customer_id,
                                name=getattr(_seed, "active_customer_name", None),
                                source="session_seed",
                                turn=0,
                            )
                        )
                    if getattr(_seed, "active_vendor_id", None):
                        _ecm.push_entity(
                            Entity(
                                type="vendor",
                                id=_seed.active_vendor_id,
                                name=getattr(_seed, "active_vendor_name", None),
                                source="session_seed",
                                turn=0,
                            )
                        )
                    if getattr(_seed, "active_invoice_id", None):
                        _ecm.push_entity(
                            Entity(
                                type="invoice",
                                id=_seed.active_invoice_id,
                                number=getattr(_seed, "active_invoice_number", None),
                                source="session_seed",
                                turn=0,
                            )
                        )
                    if getattr(_seed, "active_bill_id", None):
                        _ecm.push_entity(
                            Entity(
                                type="bill",
                                id=_seed.active_bill_id,
                                number=getattr(_seed, "active_bill_number", None),
                                source="session_seed",
                                turn=0,
                            )
                        )
                    # Seed from active_entity (set by calc_rank pipeline)
                    _ae = getattr(_seed, "active_entity", None)
                    if (
                        _ae
                        and isinstance(_ae, dict)
                        and _ae.get("type")
                        and _ae.get("name")
                    ):
                        _ecm.push_entity(
                            Entity(
                                type=_ae["type"],
                                id=_ae.get("id"),
                                name=_ae["name"],
                                source="session_seed_active_entity",
                                turn=0,
                            )
                        )
            except Exception:
                pass

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
        logger.warning(
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

            # Context-aware domain injection: if session state has AP/AR context,
            # inject relevant domains so agent loop has access to bills/invoices tools
            if (
                tool_executor
                and getattr(tool_executor, "session_manager", None)
                and getattr(tool_executor, "session_id", None)
            ):
                try:
                    _ds = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    _last = getattr(_ds, "last_action_type", "") or ""
                    _DOMAIN_INJECT = {
                        "query_ap_outstanding": {"AP_BILLS", "MASTER_DATA"},
                        "query_bills_list": {"AP_BILLS", "MASTER_DATA"},
                        "query_bills_summary": {"AP_BILLS", "MASTER_DATA"},
                        "query_ar_outstanding": {"AR_INVOICES", "MASTER_DATA"},
                        "query_sales_invoices_list": {"AR_INVOICES", "MASTER_DATA"},
                        "query_sales_invoices_summary": {"AR_INVOICES", "MASTER_DATA"},
                        "query_expenses_list": {"EXPENSES"},
                        "query_expenses_summary": {"EXPENSES"},
                    }
                    _injected = _DOMAIN_INJECT.get(_last, set())
                    if _injected:
                        _active_domains |= _injected
                        logger.warning(
                            "[DOMAIN] Injected %s from session last_action=%s",
                            _injected,
                            _last,
                        )
                except Exception:
                    pass

            tools = get_tools_for_domains(_active_domains)
            logger.warning(
                "[Phase2] domains=%d tools=%d active=%s",
                len(_active_domains),
                len(tools),
                sorted(_active_domains),
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
            # ECM: Log shadow stats
            if _ecm.entity_stack:
                logger.warning("[ECM_SHADOW] stats=%s", _ecm.get_stats())

            if llm_response.finish_reason == "stop" or not llm_response.tool_calls:
                # ── NUDGE: If ACTION intent, LLM has data but didn't call propose_direct_action ──
                _tool_names_used = {tc.get("name", "") for tc in tool_calls_log}
                _has_data_tools = bool(
                    _tool_names_used
                    & {
                        "get_bills",
                        "search_bank_accounts",
                        "get_sales_invoices",
                        "search_customers",
                        "search_vendors",
                        "update_document_context",
                    }
                )
                _has_action_tool = bool(
                    _tool_names_used & {"propose_direct_action", "propose_action"}
                )
                _has_doc_update = "update_document_context" in _tool_names_used

                # ── DOC-REPROPOSE: Build DIRECT_ACTION_PREVIEW deterministically from updated doc ──
                if (
                    _has_doc_update
                    and not _has_action_tool
                    and tool_executor.session_manager
                    and tool_executor.session_id
                ):
                    try:
                        _rp_state = await tool_executor.session_manager.get_state(
                            tool_executor.session_id
                        )
                        _rp_doc = getattr(_rp_state, "document_context", None)
                        if _rp_doc and _rp_doc.get("document_id"):
                            logger.warning(
                                "[DOC-REPROPOSE] Building re-proposal from corrected document_context"
                            )
                            # Apply edits to base data
                            _rp_edits = _rp_doc.get("edits", {})
                            _rp_vendor = _rp_edits.get(
                                "vendor_name", _rp_doc.get("vendor_name", "")
                            )
                            _rp_total = float(
                                _rp_edits.get(
                                    "total_amount", _rp_doc.get("total_amount", 0)
                                )
                            )
                            _rp_tax = float(
                                _rp_edits.get(
                                    "tax_amount", _rp_doc.get("tax_amount", 0)
                                )
                            )
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

                            _rp_expires = datetime.now(timezone.utc) + timedelta(
                                seconds=300
                            )
                            try:
                                from .db_utils import (
                                    get_session_db_pool as _rp_get_pool,
                                )

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
                                logger.warning(
                                    f"[DOC-REPROPOSE] Failed to store pending: {_rp_db_err}"
                                )

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
                                thinking_stages=thinking_stages
                                + ["Menyiapkan konfirmasi ulang"],
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
                    logger.warning(
                        f"[NUDGE] LLM has data but didn't call propose_direct_action. Injecting nudge at iter={iteration}"
                    )
                    messages.append(
                        LLMMessage(role="assistant", content=llm_response.content or "")
                    )
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=(
                                "Jangan tanya konfirmasi via text. "
                                "LANGSUNG panggil propose_direct_action() dengan data yang sudah kamu dapatkan. "
                                "Kamu sudah punya semua data yang dibutuhkan dari tool calls sebelumnya."
                            ),
                        )
                    )
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
                        logger.warning(
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
            _doc_intercept_map = {
                "search_vendors": "vendor_name",
                "search_customers": "vendor_name",
            }
            if (
                tool_executor.session_manager
                and tool_executor.session_id
                and any(tc.function_name in _doc_intercept_map for tc in all_tool_calls)
            ):
                try:
                    _di_state = await tool_executor.session_manager.get_state(
                        tool_executor.session_id
                    )
                    _di_doc_ctx = getattr(_di_state, "document_context", None)
                    if _di_doc_ctx and _di_doc_ctx.get("document_id"):
                        _new_tool_calls = []
                        _intercepted = False
                        for tc in all_tool_calls:
                            if (
                                tc.function_name in _doc_intercept_map
                                and not _intercepted
                            ):
                                _edit_field = _doc_intercept_map[tc.function_name]
                                _search_q = (
                                    (tc.arguments or {}).get("q")
                                    or (tc.arguments or {}).get("query")
                                    or (tc.arguments or {}).get("search")
                                    or (tc.arguments or {}).get("name", "")
                                )
                                if _search_q:
                                    logger.warning(
                                        f"[DOC-INTERCEPT] Redirecting {tc.function_name}('{_search_q}') -> update_document_context(edits={{{_edit_field}: '{_search_q}'}})"
                                    )
                                    tc.function_name = "update_document_context"
                                    tc.arguments = {"edits": {_edit_field: _search_q}}
                                    _intercepted = True
                            _new_tool_calls.append(tc)
                        all_tool_calls = _new_tool_calls
                except Exception as _di_err:
                    logger.warning(
                        f"[DOC-INTERCEPT] Failed to check document_context: {_di_err}"
                    )
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
                    # ECM Phase 2: inject missing entity params from context
                    try:
                        from .tool_registry import get_tools as _gat

                        _tool_schema = next(
                            (t for t in _gat() if t["name"] == tc_item.function_name),
                            {},
                        )
                        if _tool_schema and tc_item.arguments:
                            tc_item.arguments = _ecm.inject_missing_params(
                                tc_item.function_name, _tool_schema, tc_item.arguments
                            )
                    except Exception:
                        pass
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
                logger.warning(
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

                    # ECM shadow ingest (Phase 1)
                    try:
                        if result.get("success") and result.get("data"):
                            _ecm.ingest_tool_result(
                                tc.function_name, tc.arguments or {}, result.get("data")
                            )
                    except Exception:
                        pass

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

                # ECM Phase 2: inject missing entity params from context
                try:
                    from .tool_registry import get_tools as _gat2

                    _tool_schema2 = next(
                        (t for t in _gat2() if t["name"] == tool_name), {}
                    )
                    if _tool_schema2 and tool_args:
                        tool_args = _ecm.inject_missing_params(
                            tool_name, _tool_schema2, tool_args
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

                # ECM shadow ingest (Phase 1)
                try:
                    if result.get("success") and result.get("data"):
                        _ecm.ingest_tool_result(
                            tool_name, tool_args or {}, result.get("data")
                        )
                except Exception:
                    pass

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
                            logger.warning(
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
                            logger.warning(
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
                            logger.warning(
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
                logger.warning(
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
            content='Maaf, saya belum paham maksudnya. Coba ceritakan lebih spesifik, misalnya:\n\n\u2022 "Catat pembelian kain 800rb"\n\u2022 "Cek piutang pelanggan Budi"\n\u2022 "Berapa total pengeluaran bulan ini"\n'
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
