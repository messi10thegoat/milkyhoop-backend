"""
Schemas for Report Consolidation (Konsolidasi Laporan)
Combines financial reports from multiple entities/branches for group reporting
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ============================================================================
# CONSOLIDATION GROUPS
# ============================================================================

class CreateConsolidationGroupRequest(BaseModel):
    """Request to create a consolidation group"""
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    consolidation_currency_id: Optional[UUID] = None
    elimination_method: Literal["full", "proportional", "equity"] = "full"
    fiscal_year_end_month: int = Field(12, ge=1, le=12)
    fiscal_year_end_day: int = Field(31, ge=1, le=31)


class UpdateConsolidationGroupRequest(BaseModel):
    """Request to update a consolidation group"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    consolidation_currency_id: Optional[UUID] = None
    elimination_method: Optional[Literal["full", "proportional", "equity"]] = None
    fiscal_year_end_month: Optional[int] = Field(None, ge=1, le=12)
    fiscal_year_end_day: Optional[int] = Field(None, ge=1, le=31)
    is_active: Optional[bool] = None


class ConsolidationGroupListItem(BaseModel):
    """Consolidation group in list view"""
    id: str
    code: str
    name: str
    elimination_method: str
    entity_count: int = 0
    is_active: bool
    created_at: datetime


class ConsolidationGroupListResponse(BaseModel):
    """Response for listing consolidation groups"""
    items: List[ConsolidationGroupListItem]
    total: int
    has_more: bool


class ConsolidationGroupDetail(BaseModel):
    """Detailed consolidation group with entities"""
    id: str
    code: str
    name: str
    description: Optional[str]
    consolidation_currency_id: Optional[str]
    consolidation_currency_code: Optional[str]
    elimination_method: str
    fiscal_year_end_month: int
    fiscal_year_end_day: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    entities: List["ConsolidationEntityDetail"] = []


class ConsolidationGroupDetailResponse(BaseModel):
    """Response for group detail"""
    success: bool = True
    data: ConsolidationGroupDetail


# ============================================================================
# CONSOLIDATION ENTITIES
# ============================================================================

class CreateConsolidationEntityRequest(BaseModel):
    """Request to add entity to consolidation group"""
    entity_tenant_id: str = Field(..., min_length=1)
    entity_name: str = Field(..., min_length=1, max_length=100)
    entity_code: str = Field(..., min_length=1, max_length=50)
    ownership_percent: Decimal = Field(Decimal("100.00"), ge=0, le=100)
    is_parent: bool = False
    parent_entity_id: Optional[UUID] = None
    functional_currency_id: Optional[UUID] = None
    consolidation_type: Literal["full", "proportional", "equity", "none"] = "full"
    effective_date: Optional[date] = None


class UpdateConsolidationEntityRequest(BaseModel):
    """Request to update entity"""
    entity_name: Optional[str] = Field(None, min_length=1, max_length=100)
    ownership_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    is_parent: Optional[bool] = None
    parent_entity_id: Optional[UUID] = None
    functional_currency_id: Optional[UUID] = None
    consolidation_type: Optional[Literal["full", "proportional", "equity", "none"]] = None
    is_active: Optional[bool] = None
    effective_date: Optional[date] = None


class ConsolidationEntityDetail(BaseModel):
    """Consolidation entity detail"""
    id: str
    group_id: str
    entity_tenant_id: str
    entity_name: str
    entity_code: str
    ownership_percent: Decimal
    is_parent: bool
    parent_entity_id: Optional[str]
    parent_entity_name: Optional[str]
    functional_currency_id: Optional[str]
    functional_currency_code: Optional[str]
    consolidation_type: str
    is_active: bool
    effective_date: Optional[date]


class ConsolidationEntityResponse(BaseModel):
    """Response for entity operations"""
    success: bool = True
    message: str
    data: Optional[Dict[str, Any]] = None


# ============================================================================
# ACCOUNT MAPPINGS
# ============================================================================

class AccountMappingInput(BaseModel):
    """Single account mapping input"""
    source_entity_id: UUID
    source_account_code: str = Field(..., min_length=1, max_length=50)
    target_account_code: str = Field(..., min_length=1, max_length=50)
    sign_flip: bool = False
    elimination_account: bool = False


class CreateAccountMappingsRequest(BaseModel):
    """Request to create/update multiple account mappings"""
    mappings: List[AccountMappingInput]


class AccountMappingDetail(BaseModel):
    """Account mapping detail"""
    id: str
    source_entity_id: str
    source_entity_name: str
    source_account_code: str
    target_account_code: str
    sign_flip: bool
    elimination_account: bool


class AccountMappingListResponse(BaseModel):
    """Response for listing account mappings"""
    success: bool = True
    items: List[AccountMappingDetail]
    total: int


class AutoMapRequest(BaseModel):
    """Request to auto-generate account mappings"""
    source_entity_id: UUID
    mapping_strategy: Literal["exact", "prefix", "suffix"] = "exact"


# ============================================================================
# INTERCOMPANY RELATIONSHIPS
# ============================================================================

class CreateIntercompanyRelationshipRequest(BaseModel):
    """Request to create intercompany relationship"""
    entity_a_id: UUID
    entity_b_id: UUID
    relationship_type: Optional[str] = Field(None, max_length=50)
    ar_account_code: Optional[str] = Field(None, max_length=50)
    ap_account_code: Optional[str] = Field(None, max_length=50)

    @field_validator('entity_b_id')
    @classmethod
    def validate_different_entities(cls, v, info):
        if info.data.get('entity_a_id') == v:
            raise ValueError('entity_a_id and entity_b_id must be different')
        return v


class IntercompanyRelationshipDetail(BaseModel):
    """Intercompany relationship detail"""
    id: str
    entity_a_id: str
    entity_a_name: str
    entity_b_id: str
    entity_b_name: str
    relationship_type: Optional[str]
    ar_account_code: Optional[str]
    ap_account_code: Optional[str]
    is_active: bool


class IntercompanyRelationshipListResponse(BaseModel):
    """Response for listing intercompany relationships"""
    success: bool = True
    items: List[IntercompanyRelationshipDetail]
    total: int


# ============================================================================
# CONSOLIDATION RUNS
# ============================================================================

class CreateConsolidationRunRequest(BaseModel):
    """Request to create a consolidation run"""
    group_id: UUID
    period_type: Literal["monthly", "quarterly", "yearly"]
    period_year: int = Field(..., ge=2000, le=2100)
    period_month: Optional[int] = Field(None, ge=1, le=12)
    period_quarter: Optional[int] = Field(None, ge=1, le=4)
    as_of_date: date

    @field_validator('period_month')
    @classmethod
    def validate_month_for_monthly(cls, v, info):
        if info.data.get('period_type') == 'monthly' and v is None:
            raise ValueError('period_month is required for monthly consolidation')
        return v

    @field_validator('period_quarter')
    @classmethod
    def validate_quarter_for_quarterly(cls, v, info):
        if info.data.get('period_type') == 'quarterly' and v is None:
            raise ValueError('period_quarter is required for quarterly consolidation')
        return v


class ConsolidationRunListItem(BaseModel):
    """Consolidation run in list view"""
    id: str
    group_id: str
    group_name: str
    period_type: str
    period_year: int
    period_month: Optional[int]
    period_quarter: Optional[int]
    as_of_date: date
    status: str
    created_at: datetime
    completed_at: Optional[datetime]


class ConsolidationRunListResponse(BaseModel):
    """Response for listing consolidation runs"""
    items: List[ConsolidationRunListItem]
    total: int
    has_more: bool


class EliminationEntry(BaseModel):
    """Elimination journal entry (not posted to GL)"""
    description: str
    account_code: str
    account_name: str
    debit: int
    credit: int
    entity_a: str
    entity_b: str


class TrialBalanceRow(BaseModel):
    """Row in consolidated trial balance"""
    account_code: str
    account_name: str
    entity_balances: Dict[str, int]  # entity_code -> balance
    eliminations: int
    consolidated_balance: int


class ConsolidationRunDetail(BaseModel):
    """Detailed consolidation run with results"""
    id: str
    group_id: str
    group_name: str
    period_type: str
    period_year: int
    period_month: Optional[int]
    period_quarter: Optional[int]
    as_of_date: date
    status: str
    error_message: Optional[str]
    consolidated_trial_balance: Optional[List[Dict[str, Any]]]
    consolidated_balance_sheet: Optional[Dict[str, Any]]
    consolidated_income_statement: Optional[Dict[str, Any]]
    elimination_entries: Optional[List[Dict[str, Any]]]
    exchange_rates_snapshot: Optional[Dict[str, Any]]
    created_at: datetime
    completed_at: Optional[datetime]


class ConsolidationRunDetailResponse(BaseModel):
    """Response for run detail"""
    success: bool = True
    data: ConsolidationRunDetail


class ProcessConsolidationRequest(BaseModel):
    """Request to process consolidation (optional parameters)"""
    recalculate_exchange_rates: bool = True
    include_draft_journals: bool = False


# ============================================================================
# REPORTS
# ============================================================================

class ConsolidatedTrialBalanceResponse(BaseModel):
    """Response for consolidated trial balance"""
    success: bool = True
    group_name: str
    as_of_date: date
    period_description: str
    currency_code: str
    rows: List[TrialBalanceRow]
    total_debit: int
    total_credit: int


class ConsolidatedBalanceSheetResponse(BaseModel):
    """Response for consolidated balance sheet"""
    success: bool = True
    group_name: str
    as_of_date: date
    currency_code: str
    assets: Dict[str, Any]
    liabilities: Dict[str, Any]
    equity: Dict[str, Any]
    total_assets: int
    total_liabilities_equity: int


class ConsolidatedIncomeStatementResponse(BaseModel):
    """Response for consolidated income statement"""
    success: bool = True
    group_name: str
    period_start: date
    period_end: date
    currency_code: str
    revenue: Dict[str, Any]
    expenses: Dict[str, Any]
    total_revenue: int
    total_expenses: int
    net_income: int


class EliminationEntriesResponse(BaseModel):
    """Response for elimination entries"""
    success: bool = True
    entries: List[EliminationEntry]
    total_eliminations: int


# ============================================================================
# GENERIC RESPONSES
# ============================================================================

class ConsolidationResponse(BaseModel):
    """Generic response for consolidation operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# Update forward references
ConsolidationGroupDetail.model_rebuild()
