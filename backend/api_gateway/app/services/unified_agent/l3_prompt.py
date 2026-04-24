"""
Bucket B1 — L3 recent events injection into turn-level LLM prompt.

Wires session_manager.get_recent_events() into the orchestrator system
prompt so the LLM can reference recent actions across turns
("tadi saya buat apa?", "kemarin tagihan PLN sudah saya bayar").

Design principles (parallel to hook_gates.py A2):
- DENY list, exact lowercase match, explicit enumeration.
- Skip QUERY_*, CHITCHAT, MFG_*, REFORMAT, unknown/ambiguous.
- Inject only on ACTION/CRUD/ambiguous-agentic turns.
- Bounded: 5 events, ≤120 chars per line, 30-minute age window.
- Non-fatal: injection failure MUST NOT break the LLM call.
- Observability: INFO on inject, DEBUG on skip (Bucket 0 discipline).

Enumeration source (2026-04-24 grep across app/services/unified_agent/):
  AFTER_RESOLVE_DENY_LIST (Bucket A2) ∪ {all query_* intents found}.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from .hook_gates import AFTER_RESOLVE_DENY_LIST


# ---------------------------------------------------------------------------
# B1 deny list — superset of A2 plus every query_* intent in the codebase.
# ---------------------------------------------------------------------------
# A2 covers: chitchat / ambiguous / unknown / "" / MFG_* / reformat_as_table.
# B1 adds: all non-MFG query_* intents (MFG query_* already in A2 list).
_B1_QUERY_INTENTS: frozenset = frozenset(
    {
        # Accounts / ledger
        "query_account_detail",
        "query_account_ledger",
        "query_accounts_list",
        "query_general_ledger",
        "query_journal_detail",
        "query_journals_list",
        "query_trial_balance",
        "query_balance_sheet",
        "query_profit_loss",
        "query_cash_flow",
        "query_periods",
        # AR / AP
        "query_ap_aging",
        "query_ap_outstanding",
        "query_ap_summary",
        "query_ar_aging",
        "query_ar_invoices",
        "query_ar_outstanding",
        "query_ar_summary",
        "query_overdue_all",
        # Bills
        "query_bill_detail",
        "query_bill_payment_detail",
        "query_bill_payments_list",
        "query_bills_by_vendor",
        "query_bills_list",
        "query_bills_outstanding",
        "query_bills_overdue",
        "query_bills_summary",
        "query_bills_unpaid",
        "query_recurring_bills_list",
        # Sales invoices / quotes
        "query_sales_invoice_detail",
        "query_sales_invoices_list",
        "query_sales_invoices_overdue",
        "query_sales_invoices_summary",
        "query_sales_invoices_unpaid",
        "query_invoice_summary",
        "query_quote_detail",
        "query_quotes_list",
        "query_quotes_summary",
        # Bank / kasbank
        "query_bank_account_balance",
        "query_bank_account_detail",
        "query_bank_accounts_list",
        "query_bank_transactions",
        "query_bank_transactions_by_date",
        "query_bank_transfers_list",
        "query_cash_balance",
        # Customers / vendors
        "query_customer_ar",
        "query_customer_balance",
        "query_customer_deposits_list",
        "query_customer_detail",
        "query_customer_invoices",
        "query_customers_list",
        "query_customers_summary",
        "query_customers_with_overdue",
        "query_vendor_ap",
        "query_vendor_balance",
        "query_vendor_credit_detail",
        "query_vendor_credits_list",
        "query_vendor_credits_summary",
        "query_vendor_deposits_list",
        "query_vendor_detail",
        "query_vendors_list",
        "query_vendors_summary",
        "query_vendors_with_overdue",
        # Items / inventory / warehouses
        "query_inventory_health",
        "query_inventory_summary",
        "query_item_activity",
        "query_item_batches",
        "query_item_detail",
        "query_item_journal",
        "query_item_related",
        "query_item_sales",
        "query_item_sales_summary",
        "query_item_stock_card",
        "query_item_transactions",
        "query_items_by_price",
        "query_items_by_stock",
        "query_items_expired",
        "query_items_expiring_soon",
        "query_items_inactive",
        "query_items_list",
        "query_items_low_stock",
        "query_items_margins",
        "query_items_no_stock",
        "query_items_quarantine",
        "query_items_search",
        "query_items_slow_moving",
        "query_items_stats",
        "query_items_summary",
        "query_items_top_products",
        "query_items_units",
        "query_stock_adjustment_detail",
        "query_stock_adjustments",
        "query_stock_adjustments_summary",
        "query_stock_in_transit",
        "query_stock_transfers",
        "query_warehouse_stock",
        "query_warehouse_stock_value",
        "query_warehouses",
        # Payments / credit notes / expenses
        "query_credit_note_detail",
        "query_credit_notes_list",
        "query_credit_notes_summary",
        "query_receive_payment_detail",
        "query_receive_payments_list",
        "query_expense_detail",
        "query_expense_summary",
        "query_expenses_by_account",
        "query_expenses_by_date_range",
        "query_expenses_list",
        "query_expenses_summary",
        "query_top_expenses",
        # Categories
        "query_categories_list",
        # Dashboard
        "query_dashboard_summary",
    }
)

L3_INJECT_DENY_LIST: frozenset = frozenset(AFTER_RESOLVE_DENY_LIST) | _B1_QUERY_INTENTS


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_MAX_SUMMARY_CHARS = 80
_MAX_LINE_CHARS = 120


def _humanize_relative(ts: Any) -> str:
    """Return 'Nmin ago' / 'Nh ago' / 'just now' given an ISO string or datetime."""
    if ts is None:
        return "recently"
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return "recently"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60} min ago"
        if seconds < 86400:
            return f"{seconds // 3600} h ago"
        return f"{seconds // 86400} d ago"
    except (ValueError, TypeError, OSError):
        return "recently"


def _truncate(s: str, limit: int = _MAX_SUMMARY_CHARS) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _event_age_seconds(ts: Any) -> float:
    """Return age in seconds; inf if unparseable."""
    if ts is None:
        return float("inf")
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return float("inf")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError, OSError):
        return float("inf")


def format_l3_event_for_prompt(event: Dict[str, Any]) -> str:
    """Compact one-liner for LLM prompt context.

    Returns empty string for uninteresting event types (so callers can skip).
    Total line length capped at _MAX_LINE_CHARS.
    """
    ts = event.get("timestamp") or event.get("created_at")
    rel = _humanize_relative(ts)
    etype = (event.get("event_type") or "").strip()
    action_type = (event.get("action_type") or "").strip()
    summary = _truncate(event.get("result_summary") or "")

    # Event-type → narrative
    if etype == "propose":
        body = f"proposed {action_type}".strip()
        if summary:
            body += f" ({summary})"
    elif etype == "confirm":
        body = f"confirmed {action_type}".strip()
        if summary:
            body += f" → {summary}"
    elif etype == "reject":
        body = f"rejected {action_type}".strip()
    elif etype in ("search", "tool"):
        # Usually noisy — skip.
        return ""
    elif etype == "resolve":
        body = f"resolved entity {summary}" if summary else "resolved entity"
    else:
        body = etype or "event"
        if action_type:
            body += f" {action_type}"
        if summary:
            body += f" — {summary}"

    line = f"{rel}: {body}"
    if len(line) > _MAX_LINE_CHARS:
        line = line[: _MAX_LINE_CHARS - 1] + "…"
    return line


def build_l3_context_block(
    events: list[Dict[str, Any]],
    *,
    max_age_seconds: int = 1800,
) -> str:
    """Turn a list of raw events into a bounded prompt block.

    - Drops events older than max_age_seconds (session boundary + time decay).
    - Skips empty formatter results (search/tool noise).
    - Returns '' if nothing useful remains (caller should not inject).
    """
    if not events:
        return ""
    lines: list[str] = []
    for e in events:
        ts = e.get("timestamp") or e.get("created_at")
        if _event_age_seconds(ts) > max_age_seconds:
            continue
        line = format_l3_event_for_prompt(e)
        if line:
            lines.append(line)
    return "\n".join(lines)
