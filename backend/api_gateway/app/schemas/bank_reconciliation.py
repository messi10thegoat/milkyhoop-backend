"""
Bank Reconciliation Schemas
Reconcile bank transactions with bank statements.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import date


# ============================================================================
# REQUEST SCHEMAS
# ============================================================================

class StartReconciliationRequest(BaseModel):
    """Schema for starting a new reconciliation."""
    bank_account_id: str = Field(..., description="Bank account UUID")
    statement_date: date = Field(..., description="Statement date")
    statement_start_date: date = Field(..., description="Statement period start")
    statement_end_date: date = Field(..., description="Statement period end")
    statement_opening_balance: int = Field(..., description="Opening balance from statement")
    statement_closing_balance: int = Field(..., description="Closing balance from statement")


class UpdateReconciliationRequest(BaseModel):
    """Schema for updating reconciliation balances."""
    statement_opening_balance: Optional[int] = None
    statement_closing_balance: Optional[int] = None


class MatchTransactionsRequest(BaseModel):
    """Schema for matching transactions."""
    transaction_ids: List[str] = Field(..., min_length=1, description="Bank transaction IDs to match")


class UnmatchTransactionsRequest(BaseModel):
    """Schema for unmatching transactions."""
    transaction_ids: List[str] = Field(..., min_length=1, description="Bank transaction IDs to unmatch")


class AdjustmentRequest(BaseModel):
    """Schema for creating an adjustment entry."""
    description: str = Field(..., min_length=1, max_length=255, description="Adjustment description")
    amount: int = Field(..., description="Adjustment amount (positive = increase bank, negative = decrease)")
    adjustment_account_id: str = Field(..., description="Account for adjustment (expense/income)")


# ============================================================================
# RESPONSE SCHEMAS
# ============================================================================

class ReconciliationSummary(BaseModel):
    """Reconciliation summary with balance check."""
    statement_closing_balance: int
    system_closing_balance: int
    reconciled_deposits: int
    reconciled_withdrawals: int
    unreconciled_deposits: int
    unreconciled_withdrawals: int
    outstanding_deposits_count: int
    outstanding_withdrawals_count: int
    difference: int
    is_balanced: bool


class UnreconciledTransaction(BaseModel):
    """Unreconciled transaction item."""
    id: str
    transaction_date: str
    transaction_type: str
    amount: int
    description: Optional[str] = None
    reference_number: Optional[str] = None
    is_deposit: bool


class ReconciliationItem(BaseModel):
    """Reconciliation item."""
    id: str
    bank_transaction_id: str
    transaction_date: str
    transaction_type: str
    amount: int
    description: Optional[str] = None
    is_matched: bool
    matched_at: Optional[str] = None
    adjustment_amount: int = 0


class ReconciliationListItem(BaseModel):
    """Reconciliation list item."""
    id: str
    reconciliation_number: str
    bank_account_id: str
    bank_account_name: str
    statement_date: str
    statement_closing_balance: int
    system_closing_balance: int
    difference: int
    status: str
    created_at: str
    completed_at: Optional[str] = None


class ReconciliationDetail(BaseModel):
    """Full reconciliation detail."""
    id: str
    reconciliation_number: str
    bank_account_id: str
    bank_account_name: str
    statement_date: str
    statement_start_date: str
    statement_end_date: str
    statement_opening_balance: int
    statement_closing_balance: int
    system_opening_balance: int
    system_closing_balance: int
    reconciled_deposits: int
    reconciled_withdrawals: int
    unreconciled_deposits: int
    unreconciled_withdrawals: int
    difference: int
    is_balanced: bool
    status: str
    items: List[ReconciliationItem] = []
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


class ReconciliationListResponse(BaseModel):
    """Response for reconciliation list."""
    items: List[ReconciliationListItem]
    total: int
    has_more: bool = False


class ReconciliationDetailResponse(BaseModel):
    """Response for reconciliation detail."""
    success: bool
    data: ReconciliationDetail


class ReconciliationResponse(BaseModel):
    """Generic reconciliation operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class ReconciliationSummaryResponse(BaseModel):
    """Response for reconciliation summary."""
    success: bool
    data: ReconciliationSummary


class UnreconciledTransactionsResponse(BaseModel):
    """Response for unreconciled transactions."""
    success: bool
    data: List[UnreconciledTransaction]
    total: int


class ReconciliationHistoryResponse(BaseModel):
    """Response for bank account reconciliation history."""
    success: bool
    data: List[ReconciliationListItem]
    last_reconciliation_date: Optional[str] = None
    last_reconciliation_balance: Optional[int] = None
