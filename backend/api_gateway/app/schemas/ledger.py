"""
Pydantic schemas for General Ledger module.

Response models for /api/ledger endpoints (read-only).
"""

from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import date
from decimal import Decimal


# =============================================================================
# RESPONSE MODELS
# =============================================================================


class LedgerEntryResponse(BaseModel):
    """Single entry in account ledger."""

    date: date
    journal_number: str
    journal_id: str
    description: str
    debit: Decimal
    credit: Decimal
    running_balance: Decimal
    source_type: str
    source_number: Optional[str] = None


class AccountInfoResponse(BaseModel):
    """Account information for ledger header."""

    id: str
    code: str
    name: str
    account_type: str
    normal_balance: str


class AccountLedgerResponse(BaseModel):
    """Response for single account ledger view."""

    success: bool = True
    data: dict  # Contains account, entries, totals


class AccountLedgerData(BaseModel):
    """Data structure for account ledger."""

    account: AccountInfoResponse
    opening_balance: Decimal
    entries: List[LedgerEntryResponse]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    net_movement: Decimal


class AccountBalanceResponse(BaseModel):
    """Response for account balance query."""

    success: bool = True
    data: dict


class AccountBalanceData(BaseModel):
    """Account balance data."""

    account_id: str
    account_code: str
    account_name: str
    as_of_date: date
    debit_balance: Decimal
    credit_balance: Decimal
    net_balance: Decimal


class LedgerAccountSummary(BaseModel):
    """Summary for single account in ledger list."""

    id: str
    code: str
    name: str
    account_type: str
    normal_balance: str
    debit_balance: Decimal
    credit_balance: Decimal
    net_balance: Decimal


class LedgerListResponse(BaseModel):
    """Response for ledger list (all accounts with balances)."""

    success: bool = True
    data: List[LedgerAccountSummary]
    as_of_date: date


class TypeSummary(BaseModel):
    """Summary for account type."""

    total_debit: Decimal
    total_credit: Decimal
    balance: Decimal
    account_count: int


class LedgerSummaryResponse(BaseModel):
    """Response for ledger summary by account type."""

    success: bool = True
    data: dict


class LedgerSummaryData(BaseModel):
    """Ledger summary data structure."""

    by_type: Dict[str, TypeSummary]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    total_revenue: Decimal
    total_expenses: Decimal
    is_balanced: bool
