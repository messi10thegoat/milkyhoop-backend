"""
Tool Metadata Registry for MilkyHoop.

Static metadata per tool that drives runtime behavior:
  - idempotent: Controls retry strategy (auto-retry vs verify-first)
  - financial_sensitive: Controls model routing (always flagship)
  - requires_confirmation: Controls FSM path (direct vs CONFIRMATION_PENDING)

This module is the single source of truth for tool behavior flags.
Consumed by: RetryController (H4), ModelRouter (M2), FSM Engine (H3).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class ToolMetadata:
    """Static metadata for a tool. Defined at registration, never changes at runtime."""
    name: str
    idempotent: bool
    financial_sensitive: bool
    requires_confirmation: bool
    max_retries: int = 2
    base_backoff_ms: int = 500


@dataclass(frozen=True)
class ActionTypeMetadata:
    """Metadata per action_type inside propose_action."""
    action_type: str
    risk_level: RiskLevel
    creates_journal: bool
    verify_on_timeout: bool
    max_retries: int
    requires_flagship: bool


# ============================================================
# TOOL REGISTRY — Static, defined once
# ============================================================

_READ_TOOL_NAMES = [
    # Master Data — Search
    "search_customers", "search_vendors", "search_items", "search_accounts",
    # Master Data — Detail
    "get_customer_detail", "get_vendor_detail", "get_item_detail",
    # Master Data — List
    "get_customers", "get_vendors", "get_items",
    # Financial Documents
    "get_invoices", "get_invoice_detail", "get_bills", "get_bill_detail",
    "get_expenses", "get_expense_detail", "get_credit_notes", "get_purchase_orders",
    # Payments
    "get_receive_payments", "get_bill_payments",
    # Accounting
    "get_journal_entries", "get_general_ledger", "get_trial_balance",
    "get_accounting_periods", "get_chart_of_accounts",
    # Reports
    "get_profit_loss", "get_balance_sheet", "get_cash_flow",
    "get_ar_aging", "get_ap_aging", "get_bank_accounts",
    # Banking
    "get_bank_reconciliation",
    # Dashboard & Overdue
    "get_dashboard_summary", "get_overdue_invoices", "get_overdue_bills",
    # Agent Memory
    "get_session_events", "search_chat_history",
    # Dry Run
    "simulate_action",
]

TOOL_METADATA: dict[str, ToolMetadata] = {}

# Register all read tools
for _name in _READ_TOOL_NAMES:
    TOOL_METADATA[_name] = ToolMetadata(
        name=_name,
        idempotent=True,
        financial_sensitive=False,
        requires_confirmation=False,
        max_retries=2,
        base_backoff_ms=500,
    )

# Register propose_action (the ONLY write-path tool)
TOOL_METADATA["propose_action"] = ToolMetadata(
    name="propose_action",
    idempotent=False,
    financial_sensitive=True,
    requires_confirmation=True,
    max_retries=1,
    base_backoff_ms=1000,
)

# Register propose_direct_action (lightweight direct action tool)
TOOL_METADATA["propose_direct_action"] = ToolMetadata(
    name="propose_direct_action",
    idempotent=False,
    financial_sensitive=False,
    requires_confirmation=True,
    max_retries=1,
    base_backoff_ms=500,
)


# ============================================================
# ACTION TYPE REGISTRY
# ============================================================

ACTION_TYPE_METADATA: dict[str, ActionTypeMetadata] = {
    # Financial Documents — HIGH risk, creates journals
    "CREATE_SALES_INVOICE": ActionTypeMetadata(
        action_type="CREATE_SALES_INVOICE", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "CREATE_PURCHASE_INVOICE": ActionTypeMetadata(
        action_type="CREATE_PURCHASE_INVOICE", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "CREATE_EXPENSE": ActionTypeMetadata(
        action_type="CREATE_EXPENSE", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "RECEIVE_PAYMENT": ActionTypeMetadata(
        action_type="RECEIVE_PAYMENT", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "MAKE_PAYMENT": ActionTypeMetadata(
        action_type="MAKE_PAYMENT", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "BANK_TRANSFER": ActionTypeMetadata(
        action_type="BANK_TRANSFER", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "CREATE_CREDIT_NOTE": ActionTypeMetadata(
        action_type="CREATE_CREDIT_NOTE", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "POST_GENERAL_JOURNAL": ActionTypeMetadata(
        action_type="POST_GENERAL_JOURNAL", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "REVERSE_JOURNAL": ActionTypeMetadata(
        action_type="REVERSE_JOURNAL", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    # Period Management — HIGH risk
    "CLOSE_PERIOD": ActionTypeMetadata(
        action_type="CLOSE_PERIOD", risk_level=RiskLevel.HIGH,
        creates_journal=True, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    "REOPEN_PERIOD": ActionTypeMetadata(
        action_type="REOPEN_PERIOD", risk_level=RiskLevel.HIGH,
        creates_journal=False, verify_on_timeout=True, max_retries=1, requires_flagship=True,
    ),
    # Master Data — LOW risk
    "CREATE_CUSTOMER": ActionTypeMetadata(
        action_type="CREATE_CUSTOMER", risk_level=RiskLevel.LOW,
        creates_journal=False, verify_on_timeout=False, max_retries=2, requires_flagship=False,
    ),
    "CREATE_VENDOR": ActionTypeMetadata(
        action_type="CREATE_VENDOR", risk_level=RiskLevel.LOW,
        creates_journal=False, verify_on_timeout=False, max_retries=2, requires_flagship=False,
    ),
    "CREATE_PRODUCT": ActionTypeMetadata(
        action_type="CREATE_PRODUCT", risk_level=RiskLevel.LOW,
        creates_journal=False, verify_on_timeout=False, max_retries=2, requires_flagship=False,
    ),

    # Direct Actions — LOW risk, no journal
    "CREATE_BANK_ACCOUNT": ActionTypeMetadata(
        action_type="CREATE_BANK_ACCOUNT", risk_level=RiskLevel.LOW,
        creates_journal=False, verify_on_timeout=False, max_retries=2, requires_flagship=False,
    ),
}


# ============================================================
# LOOKUP HELPERS
# ============================================================

def get_tool_metadata(tool_name: str) -> ToolMetadata:
    """Get metadata for a tool. Returns default read-tool metadata if unknown."""
    return TOOL_METADATA.get(tool_name, ToolMetadata(
        name=tool_name, idempotent=True, financial_sensitive=False,
        requires_confirmation=False, max_retries=2, base_backoff_ms=500,
    ))


def get_action_metadata(action_type: str) -> Optional[ActionTypeMetadata]:
    """Get metadata for an action type. Returns None if unknown."""
    return ACTION_TYPE_METADATA.get(action_type)


def is_financial_turn(tool_calls: list) -> bool:
    """Check if ANY tool call in this turn is financial-sensitive."""
    for call in tool_calls:
        tool_name = call.get("name", "")
        meta = TOOL_METADATA.get(tool_name)
        if meta and meta.financial_sensitive:
            return True
        if tool_name == "propose_action":
            action_type = call.get("arguments", {}).get("action_type", "")
            action_meta = ACTION_TYPE_METADATA.get(action_type)
            if action_meta and action_meta.requires_flagship:
                return True
    return False
