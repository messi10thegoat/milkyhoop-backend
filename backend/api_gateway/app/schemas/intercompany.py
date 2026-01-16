"""
Schemas for Intercompany Transactions (Transaksi Antar Cabang)
Record and reconcile transactions between entities within a group
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# INTERCOMPANY TRANSACTIONS
# ============================================================================

class CreateIntercompanyTransactionRequest(BaseModel):
    """Request to create intercompany transaction"""
    transaction_date: date
    description: Optional[str] = None
    from_entity_tenant_id: str = Field(..., min_length=1)
    to_entity_tenant_id: str = Field(..., min_length=1)
    transaction_type: Literal["sale", "purchase", "loan", "expense_allocation", "transfer"]
    amount: int = Field(..., gt=0)
    currency_id: Optional[UUID] = None
    exchange_rate: Decimal = Field(Decimal("1"), gt=0)
    from_document_type: Optional[str] = None
    from_document_id: Optional[UUID] = None
    from_document_number: Optional[str] = None

    @field_validator('to_entity_tenant_id')
    @classmethod
    def validate_different_entities(cls, v, info):
        if info.data.get('from_entity_tenant_id') == v:
            raise ValueError('from_entity_tenant_id and to_entity_tenant_id must be different')
        return v


class UpdateIntercompanyTransactionRequest(BaseModel):
    """Request to update intercompany transaction (pending only)"""
    description: Optional[str] = None
    amount: Optional[int] = Field(None, gt=0)
    exchange_rate: Optional[Decimal] = Field(None, gt=0)


class IntercompanyTransactionListItem(BaseModel):
    """Intercompany transaction in list view"""
    id: str
    transaction_number: str
    transaction_date: date
    from_entity_tenant_id: str
    from_entity_name: Optional[str]
    to_entity_tenant_id: str
    to_entity_name: Optional[str]
    transaction_type: str
    amount: int
    currency_code: Optional[str]
    from_status: str
    to_status: str
    is_reconciled: bool
    created_at: datetime


class IntercompanyTransactionListResponse(BaseModel):
    """Response for listing intercompany transactions"""
    items: List[IntercompanyTransactionListItem]
    total: int
    has_more: bool


class IntercompanyTransactionDetail(BaseModel):
    """Detailed intercompany transaction"""
    id: str
    transaction_number: str
    transaction_date: date
    description: Optional[str]
    from_entity_tenant_id: str
    from_entity_name: Optional[str]
    to_entity_tenant_id: str
    to_entity_name: Optional[str]
    transaction_type: str
    amount: int
    currency_id: Optional[str]
    currency_code: Optional[str]
    exchange_rate: Decimal
    from_document_type: Optional[str]
    from_document_id: Optional[str]
    from_document_number: Optional[str]
    to_document_type: Optional[str]
    to_document_id: Optional[str]
    to_document_number: Optional[str]
    from_status: str
    to_status: str
    from_journal_id: Optional[str]
    to_journal_id: Optional[str]
    is_reconciled: bool
    reconciled_at: Optional[datetime]
    variance_amount: int
    created_at: datetime
    updated_at: datetime


class IntercompanyTransactionDetailResponse(BaseModel):
    """Response for transaction detail"""
    success: bool = True
    data: IntercompanyTransactionDetail


# ============================================================================
# CONFIRM / REJECT
# ============================================================================

class ConfirmTransactionRequest(BaseModel):
    """Request to confirm receipt of IC transaction"""
    to_document_type: Optional[str] = None
    to_document_id: Optional[UUID] = None
    to_document_number: Optional[str] = None
    notes: Optional[str] = None


class RejectTransactionRequest(BaseModel):
    """Request to reject/dispute IC transaction"""
    reason: str = Field(..., min_length=1, max_length=500)


# ============================================================================
# RECONCILIATION
# ============================================================================

class ReconcileRequest(BaseModel):
    """Request to reconcile intercompany transactions"""
    transaction_ids: List[UUID] = Field(..., min_length=1)
    notes: Optional[str] = None


class ReconcileResponse(BaseModel):
    """Response for reconciliation"""
    success: bool
    message: str
    reconciled_count: int
    variance_total: int


class UnreconciledItem(BaseModel):
    """Unreconciled transaction item"""
    id: str
    transaction_number: str
    transaction_date: date
    counterparty_tenant_id: str
    counterparty_name: Optional[str]
    transaction_type: str
    amount: int
    days_outstanding: int


class UnreconciledListResponse(BaseModel):
    """Response for unreconciled transactions"""
    items: List[UnreconciledItem]
    total: int
    total_amount: int


class VarianceItem(BaseModel):
    """Variance report item"""
    transaction_id: str
    transaction_number: str
    from_amount: int
    to_amount: int
    variance: int
    variance_percent: Decimal


class VarianceReportResponse(BaseModel):
    """Response for variance report"""
    success: bool = True
    items: List[VarianceItem]
    total_variance: int


# ============================================================================
# BALANCES
# ============================================================================

class IntercompanyBalance(BaseModel):
    """Balance with a specific entity"""
    entity_tenant_id: str
    entity_name: Optional[str]
    balance: int  # positive = we owe them, negative = they owe us
    currency_code: Optional[str]
    last_transaction_date: Optional[date]
    last_reconciled_date: Optional[date]
    transaction_count: int = 0


class IntercompanyBalanceListResponse(BaseModel):
    """Response for listing IC balances"""
    success: bool = True
    items: List[IntercompanyBalance]
    total_receivable: int  # they owe us
    total_payable: int  # we owe them
    net_position: int


class IntercompanyBalanceDetailResponse(BaseModel):
    """Response for balance with specific entity"""
    success: bool = True
    data: IntercompanyBalance
    recent_transactions: List[IntercompanyTransactionListItem]


# ============================================================================
# SETTLEMENTS
# ============================================================================

class CreateSettlementRequest(BaseModel):
    """Request to create settlement"""
    settlement_date: date
    payee_tenant_id: str = Field(..., min_length=1)
    amount: int = Field(..., gt=0)
    currency_id: Optional[UUID] = None
    settlement_method: Literal["bank_transfer", "offset", "cash"] = "bank_transfer"
    reference_number: Optional[str] = None
    notes: Optional[str] = None


class SettlementListItem(BaseModel):
    """Settlement in list view"""
    id: str
    settlement_number: str
    settlement_date: date
    payer_tenant_id: str
    payer_name: Optional[str]
    payee_tenant_id: str
    payee_name: Optional[str]
    amount: int
    settlement_method: str
    status: str
    created_at: datetime


class SettlementListResponse(BaseModel):
    """Response for listing settlements"""
    items: List[SettlementListItem]
    total: int
    has_more: bool


class SettlementDetailResponse(BaseModel):
    """Response for settlement detail"""
    success: bool = True
    data: Dict[str, Any]


# ============================================================================
# REPORTS
# ============================================================================

class IntercompanyReportRequest(BaseModel):
    """Request for IC transaction report"""
    start_date: date
    end_date: date
    entity_tenant_id: Optional[str] = None
    transaction_type: Optional[str] = None
    include_reconciled: bool = True


class IntercompanyReportResponse(BaseModel):
    """Response for IC report"""
    success: bool = True
    period_start: date
    period_end: date
    transactions: List[IntercompanyTransactionListItem]
    summary: Dict[str, Any]


class IntercompanyAgingItem(BaseModel):
    """Aging bucket item"""
    entity_tenant_id: str
    entity_name: Optional[str]
    current: int  # 0-30 days
    days_31_60: int
    days_61_90: int
    over_90_days: int
    total: int


class IntercompanyAgingResponse(BaseModel):
    """Response for IC aging report"""
    success: bool = True
    as_of_date: date
    items: List[IntercompanyAgingItem]
    totals: Dict[str, int]


# ============================================================================
# GENERIC RESPONSES
# ============================================================================

class IntercompanyResponse(BaseModel):
    """Generic response for IC operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
