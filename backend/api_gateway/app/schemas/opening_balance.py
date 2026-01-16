"""
Opening Balance Schemas
=======================
Pydantic schemas for Opening Balance Mechanism (Saldo Awal).

Used for initial setup during tenant onboarding or fiscal year transitions.
"""

from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field, validator


# =============================================================================
# ACCOUNT BALANCE LINE ITEMS
# =============================================================================

class AccountBalanceLine(BaseModel):
    """Single account balance line for opening balance entry."""
    account_code: str = Field(..., description="CoA account code (e.g., '1-10100')")
    account_name: Optional[str] = Field(None, description="Account name (optional, for display)")
    debit: int = Field(0, ge=0, description="Debit amount in smallest currency unit")
    credit: int = Field(0, ge=0, description="Credit amount in smallest currency unit")

    @validator('debit', 'credit')
    def validate_amounts(cls, v):
        if v < 0:
            raise ValueError('Amount cannot be negative')
        return v


class AROpeningBalance(BaseModel):
    """Opening balance for Accounts Receivable subledger."""
    customer_id: str = Field(..., description="Customer UUID")
    customer_name: Optional[str] = Field(None, description="Customer name")
    invoice_number: Optional[str] = Field(None, description="Original invoice number (if known)")
    invoice_date: Optional[date] = Field(None, description="Original invoice date")
    due_date: Optional[date] = Field(None, description="Due date for the balance")
    amount: int = Field(..., gt=0, description="Outstanding AR amount")
    description: Optional[str] = Field(None, description="Description/notes")


class APOpeningBalance(BaseModel):
    """Opening balance for Accounts Payable subledger."""
    vendor_id: str = Field(..., description="Vendor UUID")
    vendor_name: Optional[str] = Field(None, description="Vendor name")
    bill_number: Optional[str] = Field(None, description="Original bill number (if known)")
    bill_date: Optional[date] = Field(None, description="Original bill date")
    due_date: Optional[date] = Field(None, description="Due date for the balance")
    amount: int = Field(..., gt=0, description="Outstanding AP amount")
    description: Optional[str] = Field(None, description="Description/notes")


class InventoryOpeningBalance(BaseModel):
    """Opening balance for inventory items."""
    item_id: str = Field(..., description="Item/Product UUID")
    item_code: Optional[str] = Field(None, description="Item code")
    item_name: Optional[str] = Field(None, description="Item name")
    quantity: float = Field(..., gt=0, description="Opening quantity")
    unit_cost: int = Field(..., gt=0, description="Unit cost in smallest currency unit")
    total_value: Optional[int] = Field(None, description="Total value (quantity * unit_cost)")
    storage_location_id: Optional[str] = Field(None, description="Storage location UUID")


# =============================================================================
# REQUEST SCHEMAS
# =============================================================================

class CreateOpeningBalanceRequest(BaseModel):
    """Request to create opening balance entries."""
    opening_date: date = Field(..., description="Opening balance date (typically start of fiscal period)")
    description: Optional[str] = Field(None, description="Description for the opening balance entry")

    # GL account balances (required)
    accounts: List[AccountBalanceLine] = Field(
        ...,
        min_items=1,
        description="List of account balances. Any imbalance will be posted to Opening Balance Equity (3-50000)"
    )

    # Optional subledger balances
    ar_balances: Optional[List[AROpeningBalance]] = Field(
        None,
        description="AR subledger opening balances (must match AR control account total)"
    )
    ap_balances: Optional[List[APOpeningBalance]] = Field(
        None,
        description="AP subledger opening balances (must match AP control account total)"
    )
    inventory_balances: Optional[List[InventoryOpeningBalance]] = Field(
        None,
        description="Inventory opening balances (must match Inventory control account total)"
    )

    @validator('accounts')
    def validate_accounts_not_empty(cls, v):
        if not v:
            raise ValueError('At least one account balance is required')
        return v


class UpdateOpeningBalanceRequest(BaseModel):
    """Request to update/supersede opening balance entries."""
    opening_date: date = Field(..., description="New opening balance date")
    description: Optional[str] = Field(None, description="Description for the update")
    reason: str = Field(..., description="Reason for updating opening balance")

    # Updated balances
    accounts: List[AccountBalanceLine] = Field(..., min_items=1)
    ar_balances: Optional[List[AROpeningBalance]] = None
    ap_balances: Optional[List[APOpeningBalance]] = None
    inventory_balances: Optional[List[InventoryOpeningBalance]] = None


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class AccountBalanceItem(BaseModel):
    """Account balance in response."""
    account_code: str
    account_name: str
    account_type: str
    debit: int
    credit: int


class OpeningBalanceData(BaseModel):
    """Opening balance record data."""
    id: str
    tenant_id: str
    opening_date: date
    description: Optional[str]
    status: str  # ACTIVE or SUPERSEDED

    # Linked journals
    gl_journal_id: Optional[str]
    ar_journal_id: Optional[str]
    ap_journal_id: Optional[str]
    inventory_journal_id: Optional[str]

    # Balance snapshot
    accounts: List[AccountBalanceItem]
    total_debit: int
    total_credit: int
    equity_adjustment: int  # Amount posted to Opening Balance Equity

    # Subledger summaries
    ar_count: Optional[int]
    ar_total: Optional[int]
    ap_count: Optional[int]
    ap_total: Optional[int]
    inventory_count: Optional[int]
    inventory_total: Optional[int]

    # Audit
    created_at: str
    created_by: str
    superseded_at: Optional[str]
    superseded_by: Optional[str]


class OpeningBalanceResponse(BaseModel):
    """Response for single opening balance record."""
    success: bool = True
    data: OpeningBalanceData


class OpeningBalanceListResponse(BaseModel):
    """Response for listing opening balance history."""
    success: bool = True
    data: List[OpeningBalanceData]
    total: int


class OpeningBalanceSummary(BaseModel):
    """Summary of current opening balance state."""
    has_opening_balance: bool
    opening_date: Optional[date]
    total_debit: int
    total_credit: int
    equity_adjustment: int
    ar_total: int
    ap_total: int
    inventory_total: int
    last_updated: Optional[str]


class OpeningBalanceSummaryResponse(BaseModel):
    """Response for opening balance summary."""
    success: bool = True
    data: OpeningBalanceSummary


class CreateOpeningBalanceResponse(BaseModel):
    """Response after creating opening balance."""
    success: bool = True
    message: str
    data: dict  # Contains id, journal_ids, summary
    warnings: Optional[List[str]] = None


class ValidationResult(BaseModel):
    """Result of opening balance validation."""
    is_valid: bool
    total_debit: int
    total_credit: int
    imbalance: int
    equity_adjustment_needed: int
    ar_control_match: Optional[bool]  # Does AR subledger match control account?
    ap_control_match: Optional[bool]  # Does AP subledger match control account?
    inventory_control_match: Optional[bool]  # Does inventory subledger match control account?
    errors: List[str]
    warnings: List[str]


class ValidateOpeningBalanceResponse(BaseModel):
    """Response for validation endpoint."""
    success: bool = True
    data: ValidationResult
