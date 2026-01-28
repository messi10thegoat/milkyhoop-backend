"""
Pydantic schemas for Bank Reconciliation module.

Bank reconciliation allows matching bank statement lines with system transactions
to verify that recorded transactions match the actual bank statement.

Flow: in_progress -> completed / cancelled

Key concepts:
- Session: A reconciliation session for a specific period
- Statement Lines: Imported from bank statement (CSV/manual)
- Transactions: System transactions (payments, receipts, transfers)
- Match: Links statement lines to system transactions
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import date


# =============================================================================
# REQUEST MODELS - Session
# =============================================================================


class CreateSessionRequest(BaseModel):
    """Request body for starting a new reconciliation session."""

    account_id: str = Field(..., description="Bank account UUID")
    statement_date: date = Field(..., description="Statement date")
    statement_start_date: date = Field(..., description="Statement period start date")
    statement_end_date: date = Field(..., description="Statement period end date")
    statement_beginning_balance: int = Field(
        ..., description="Opening balance from bank statement (IDR)"
    )
    statement_ending_balance: int = Field(
        ..., description="Closing balance from bank statement (IDR)"
    )

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, v):
        if not v or not v.strip():
            raise ValueError("account_id is required")
        return v.strip()


# =============================================================================
# REQUEST MODELS - Import
# =============================================================================


class ImportConfigCSV(BaseModel):
    """CSV import configuration for bank statement."""

    format: Literal["csv"] = "csv"
    date_column: str = Field(..., description="Column name for transaction date")
    description_column: str = Field(..., description="Column name for description")
    amount_column: Optional[str] = Field(
        None,
        description="Column name for single amount (credit positive, debit negative)",
    )
    debit_column: Optional[str] = Field(
        None, description="Column name for debit amount"
    )
    credit_column: Optional[str] = Field(
        None, description="Column name for credit amount"
    )
    reference_column: Optional[str] = Field(
        None, description="Column name for reference number"
    )
    balance_column: Optional[str] = Field(
        None, description="Column name for running balance"
    )
    date_format: str = Field("DD/MM/YYYY", description="Date format in CSV")
    decimal_separator: str = Field(",", description="Decimal separator (, or .)")
    skip_rows: int = Field(0, ge=0, description="Number of header rows to skip")


# =============================================================================
# REQUEST MODELS - Matching
# =============================================================================


class MatchRequest(BaseModel):
    """Request body for matching a statement line to transactions."""

    statement_line_id: str = Field(..., description="Statement line UUID to match")
    transaction_ids: List[str] = Field(
        ..., min_length=1, description="System transaction UUIDs to match with"
    )


class AutoMatchRequest(BaseModel):
    """Request body for auto-matching configuration."""

    confidence_threshold: Literal["exact", "high", "medium", "low"] = Field(
        "high", description="Minimum confidence level for auto-match"
    )
    date_tolerance_days: int = Field(
        3, ge=0, le=30, description="Days tolerance for date matching (0-30)"
    )


# =============================================================================
# REQUEST MODELS - Create Transaction
# =============================================================================


class CreateTransactionFromLineRequest(BaseModel):
    """Request body for creating a transaction from an unmatched statement line."""

    statement_line_id: str = Field(..., description="Statement line UUID")
    type: Literal["expense", "income", "transfer"] = Field(
        ..., description="Transaction type to create"
    )
    account_id: str = Field(
        ..., description="Chart of Accounts UUID for categorization"
    )
    contact_id: Optional[str] = Field(None, description="Customer/Vendor UUID")
    description: Optional[str] = Field(None, description="Transaction description")
    auto_match: bool = Field(
        True, description="Automatically match created transaction to statement line"
    )


# =============================================================================
# REQUEST MODELS - Adjustments & Completion
# =============================================================================


class AdjustmentItem(BaseModel):
    """Single adjustment entry for reconciliation."""

    type: Literal["bank_fee", "interest", "correction", "other"] = Field(
        ..., description="Type of adjustment"
    )
    amount: int = Field(..., description="Adjustment amount in IDR")
    description: str = Field(
        ..., min_length=1, max_length=500, description="Adjustment description"
    )
    account_id: str = Field(..., description="Chart of Accounts UUID for adjustment")


class CompleteSessionRequest(BaseModel):
    """Request body for completing a reconciliation session."""

    adjustments: List[AdjustmentItem] = Field(
        default_factory=list, description="Adjustment entries to create"
    )


# =============================================================================
# RESPONSE MODELS - Nested Objects
# =============================================================================


class AccountSummary(BaseModel):
    """Bank account with reconciliation status."""

    id: str
    name: str
    account_number: Optional[str] = None
    current_balance: int
    last_reconciled_date: Optional[str] = None
    last_reconciled_balance: Optional[int] = None
    statement_balance: Optional[int] = None
    statement_date: Optional[str] = None
    unreconciled_difference: Optional[int] = None
    needs_reconciliation: bool = True
    days_since_reconciliation: Optional[int] = None
    active_session_id: Optional[str] = None
    active_session_status: Optional[str] = None


class SessionStatistics(BaseModel):
    """Statistics for a reconciliation session."""

    total_statement_lines: int = 0
    matched_lines: int = 0
    unmatched_lines: int = 0
    total_transactions: int = 0
    matched_transactions: int = 0
    unmatched_transactions: int = 0
    statement_total: int = 0
    matched_total: int = 0
    difference: Optional[int] = 0
    is_balanced: bool = False
    # Fields returned by router
    matched_count: int = 0
    unmatched_count: int = 0
    excluded_count: int = 0
    total_cleared: int = 0
    total_uncleared: int = 0


class SessionListItem(BaseModel):
    """Reconciliation session for list display."""

    id: str
    session_number: Optional[str] = None
    account_id: str
    account_name: str
    statement_date: str
    statement_start_date: str
    statement_end_date: str
    statement_beginning_balance: int
    statement_ending_balance: int
    status: Literal["in_progress", "completed", "cancelled"]
    matched_count: int = 0
    total_lines: int = 0
    difference: Optional[int] = None
    created_at: str
    completed_at: Optional[str] = None


class StatementLineItem(BaseModel):
    """Bank statement line item."""

    id: str
    line_number: int
    transaction_date: str
    description: str
    reference: Optional[str] = None
    amount: int
    is_credit: bool
    running_balance: Optional[int] = None
    status: Literal["unmatched", "matched", "excluded"]
    match_id: Optional[str] = None
    matched_transaction_ids: List[str] = []
    confidence: Optional[str] = None
    created_at: str


class TransactionItem(BaseModel):
    """System transaction available for matching."""

    id: str
    transaction_type: str
    transaction_date: str
    description: Optional[str] = None
    reference: Optional[str] = None
    amount: int
    is_credit: bool
    source_type: str
    source_id: Optional[str] = None
    source_number: Optional[str] = None
    contact_name: Optional[str] = None
    is_matched: bool = False
    match_id: Optional[str] = None


class MatchSuggestion(BaseModel):
    """Auto-match suggestion for a statement line."""

    statement_line_id: str
    suggested_transaction_ids: List[str]
    confidence: Literal["exact", "high", "medium", "low"]
    confidence_score: float
    match_reasons: List[str] = []


class ImportError(BaseModel):
    """Import error detail."""

    row_number: int
    column: Optional[str] = None
    value: Optional[str] = None
    error: str


class HistoryItem(BaseModel):
    """Reconciliation history entry."""

    id: str
    session_number: Optional[str] = None
    statement_date: str
    statement_start_date: str
    statement_end_date: str
    statement_beginning_balance: int
    statement_ending_balance: int
    system_balance: int
    difference: Optional[int] = None
    matched_count: int
    adjustment_count: int
    status: str
    completed_at: Optional[str] = None
    completed_by: Optional[str] = None
    created_at: str


class SessionDetail(BaseModel):
    """Full reconciliation session detail."""

    id: str
    session_number: Optional[str] = None
    account_id: str
    account_name: str
    account_number: Optional[str] = None
    bank_name: Optional[str] = None

    # Statement info
    statement_date: str
    statement_start_date: str
    statement_end_date: str
    statement_beginning_balance: int
    statement_ending_balance: int

    # System balances - optional to match router
    system_beginning_balance: Optional[int] = None
    system_ending_balance: Optional[int] = None
    cleared_balance: Optional[int] = None
    difference: Optional[int] = None

    # Status
    status: Literal["in_progress", "completed", "cancelled"]
    statistics: SessionStatistics

    # Adjustments
    adjustments: List[AdjustmentItem] = []

    # Audit
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    completed_at: Optional[str] = None
    completed_by: Optional[str] = None
    cancelled_at: Optional[str] = None
    cancelled_by: Optional[str] = None


# =============================================================================
# RESPONSE WRAPPERS - Accounts
# =============================================================================


class AccountsListResponse(BaseModel):
    """Response for bank accounts list with reconciliation status."""

    data: List[AccountSummary]
    total: int


# =============================================================================
# RESPONSE WRAPPERS - Sessions
# =============================================================================


class SessionsListResponse(BaseModel):
    """Response for reconciliation sessions list."""

    data: List[SessionListItem]
    total: int
    hasMore: bool = False


class SessionCreateResponse(BaseModel):
    """Response for session creation."""

    id: str
    status: str
    created_at: str


class SessionDetailResponse(BaseModel):
    """Response for session detail."""

    success: bool = True
    data: SessionDetail


# =============================================================================
# RESPONSE WRAPPERS - Import
# =============================================================================


class ImportDateRange(BaseModel):
    """Date range from imported statement."""

    start_date: str
    end_date: str


class ImportResponse(BaseModel):
    """Response for statement import."""

    lines_imported: int
    lines_skipped: int
    total_credits: int
    total_debits: int
    date_range: ImportDateRange
    errors: List[ImportError] = []


# =============================================================================
# RESPONSE WRAPPERS - Statement Lines & Transactions
# =============================================================================


class StatementLinesResponse(BaseModel):
    """Response for statement lines list."""

    data: List[StatementLineItem]
    total: int
    hasMore: bool = False


class TransactionsResponse(BaseModel):
    """Response for transactions list."""

    data: List[TransactionItem]
    total: int
    hasMore: bool = False


# =============================================================================
# RESPONSE WRAPPERS - Matching
# =============================================================================


class MatchResponse(BaseModel):
    """Response for match operation."""

    match_id: str
    match_type: Literal["one_to_one", "one_to_many", "many_to_one"]
    confidence: str
    cleared_amount: int
    session_stats: SessionStatistics


class UnmatchResponse(BaseModel):
    """Response for unmatch operation."""

    success: bool
    session_stats: SessionStatistics


class AutoMatchResponse(BaseModel):
    """Response for auto-match operation."""

    matches_created: int
    suggestions: List[MatchSuggestion]
    session_stats: SessionStatistics


# =============================================================================
# RESPONSE WRAPPERS - Create Transaction
# =============================================================================


class CreateTransactionResponse(BaseModel):
    """Response for create transaction from statement line."""

    transaction_id: str
    match_id: Optional[str] = None
    session_stats: SessionStatistics


# =============================================================================
# RESPONSE WRAPPERS - Complete & Cancel
# =============================================================================


class FinalStats(BaseModel):
    """Final reconciliation statistics."""

    total_matched: int
    total_adjustments: int
    opening_difference: int
    closing_difference: int
    final_difference: int


class CompleteResponse(BaseModel):
    """Response for session completion."""

    success: bool
    completed_at: str
    final_stats: FinalStats
    journal_entries_created: int


class CancelResponse(BaseModel):
    """Response for session cancellation."""

    success: bool
    cleared_transactions_reset: int


# =============================================================================
# RESPONSE WRAPPERS - History
# =============================================================================


class HistoryResponse(BaseModel):
    """Response for reconciliation history."""

    data: List[HistoryItem]
    total: int
    hasMore: bool = False
