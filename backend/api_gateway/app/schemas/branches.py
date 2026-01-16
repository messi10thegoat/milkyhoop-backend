"""
Schemas for Multi-Branch (Multi-Cabang)
Manage multiple branches with separate accounting within same tenant
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, EmailStr


# ============================================================================
# BRANCHES
# ============================================================================

class CreateBranchRequest(BaseModel):
    """Request to create a branch"""
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    country: str = Field("Indonesia", max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=100)
    parent_branch_id: Optional[UUID] = None
    branch_level: int = Field(1, ge=1, le=5)
    is_headquarters: bool = False
    has_own_sequence: bool = False
    default_warehouse_id: Optional[UUID] = None
    default_bank_account_id: Optional[UUID] = None
    profit_center_id: Optional[UUID] = None
    opened_date: Optional[date] = None


class UpdateBranchRequest(BaseModel):
    """Request to update a branch"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=100)
    parent_branch_id: Optional[UUID] = None
    branch_level: Optional[int] = Field(None, ge=1, le=5)
    is_headquarters: Optional[bool] = None
    has_own_sequence: Optional[bool] = None
    default_warehouse_id: Optional[UUID] = None
    default_bank_account_id: Optional[UUID] = None
    profit_center_id: Optional[UUID] = None
    is_active: Optional[bool] = None
    closed_date: Optional[date] = None


class BranchListItem(BaseModel):
    """Branch in list view"""
    id: str
    code: str
    name: str
    city: Optional[str]
    branch_level: int
    is_headquarters: bool
    is_active: bool
    parent_branch_id: Optional[str]
    parent_branch_name: Optional[str]
    transaction_count: int = 0
    created_at: datetime


class BranchListResponse(BaseModel):
    """Response for listing branches"""
    items: List[BranchListItem]
    total: int
    has_more: bool


class BranchDetail(BaseModel):
    """Detailed branch information"""
    id: str
    code: str
    name: str
    address: Optional[str]
    city: Optional[str]
    province: Optional[str]
    postal_code: Optional[str]
    country: str
    phone: Optional[str]
    email: Optional[str]
    parent_branch_id: Optional[str]
    parent_branch_name: Optional[str]
    branch_level: int
    is_headquarters: bool
    has_own_sequence: bool
    default_warehouse_id: Optional[str]
    default_warehouse_name: Optional[str]
    default_bank_account_id: Optional[str]
    default_bank_account_name: Optional[str]
    profit_center_id: Optional[str]
    profit_center_name: Optional[str]
    is_active: bool
    opened_date: Optional[date]
    closed_date: Optional[date]
    created_at: datetime
    updated_at: datetime


class BranchDetailResponse(BaseModel):
    """Response for branch detail"""
    success: bool = True
    data: BranchDetail


class BranchTreeNode(BaseModel):
    """Branch in tree view"""
    id: str
    code: str
    name: str
    branch_level: int
    is_headquarters: bool
    is_active: bool
    children: List["BranchTreeNode"] = []


class BranchTreeResponse(BaseModel):
    """Response for branch tree"""
    success: bool = True
    data: List[BranchTreeNode]


# ============================================================================
# BRANCH PERMISSIONS
# ============================================================================

class CreateBranchPermissionRequest(BaseModel):
    """Request to grant branch permission"""
    user_id: UUID
    can_view: bool = True
    can_create: bool = False
    can_edit: bool = False
    can_delete: bool = False
    can_approve: bool = False
    is_default: bool = False


class UpdateBranchPermissionRequest(BaseModel):
    """Request to update branch permission"""
    can_view: Optional[bool] = None
    can_create: Optional[bool] = None
    can_edit: Optional[bool] = None
    can_delete: Optional[bool] = None
    can_approve: Optional[bool] = None
    is_default: Optional[bool] = None


class BranchPermissionDetail(BaseModel):
    """Branch permission detail"""
    id: str
    user_id: str
    user_name: Optional[str]
    branch_id: str
    branch_name: str
    can_view: bool
    can_create: bool
    can_edit: bool
    can_delete: bool
    can_approve: bool
    is_default: bool
    created_at: datetime


class BranchPermissionListResponse(BaseModel):
    """Response for listing branch permissions"""
    success: bool = True
    items: List[BranchPermissionDetail]
    total: int


class UserBranchItem(BaseModel):
    """Branch accessible by user"""
    branch_id: str
    branch_code: str
    branch_name: str
    can_view: bool
    can_create: bool
    can_edit: bool
    can_delete: bool
    can_approve: bool
    is_default: bool


class UserBranchesResponse(BaseModel):
    """Response for user's accessible branches"""
    success: bool = True
    items: List[UserBranchItem]


# ============================================================================
# BRANCH TRANSFERS
# ============================================================================

class BranchTransferLineInput(BaseModel):
    """Line item for branch transfer"""
    product_id: UUID
    quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = None
    unit_cost: int = Field(..., ge=0)
    batch_id: Optional[UUID] = None
    serial_ids: Optional[List[UUID]] = None
    notes: Optional[str] = None


class CreateBranchTransferRequest(BaseModel):
    """Request to create branch transfer"""
    transfer_date: date
    from_branch_id: UUID
    to_branch_id: UUID
    pricing_method: Literal["cost", "markup", "market"] = "cost"
    markup_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    notes: Optional[str] = None
    lines: List[BranchTransferLineInput] = Field(..., min_length=1)

    @field_validator('to_branch_id')
    @classmethod
    def validate_different_branches(cls, v, info):
        if info.data.get('from_branch_id') == v:
            raise ValueError('from_branch_id and to_branch_id must be different')
        return v


class BranchTransferListItem(BaseModel):
    """Branch transfer in list view"""
    id: str
    transfer_number: str
    transfer_date: date
    from_branch_id: str
    from_branch_name: str
    to_branch_id: str
    to_branch_name: str
    transfer_price: int
    status: str
    item_count: int
    created_at: datetime


class BranchTransferListResponse(BaseModel):
    """Response for listing branch transfers"""
    items: List[BranchTransferListItem]
    total: int
    has_more: bool


class BranchTransferLineDetail(BaseModel):
    """Branch transfer line detail"""
    id: str
    product_id: str
    product_name: str
    product_sku: Optional[str]
    quantity: Decimal
    unit: Optional[str]
    unit_cost: int
    line_total: int
    batch_id: Optional[str]
    batch_number: Optional[str]


class BranchTransferDetail(BaseModel):
    """Detailed branch transfer"""
    id: str
    transfer_number: str
    transfer_date: date
    from_branch_id: str
    from_branch_name: str
    to_branch_id: str
    to_branch_name: str
    stock_transfer_id: Optional[str]
    transfer_price: int
    pricing_method: str
    markup_percent: Optional[Decimal]
    status: str
    settlement_date: Optional[date]
    settlement_journal_id: Optional[str]
    from_journal_id: Optional[str]
    to_journal_id: Optional[str]
    notes: Optional[str]
    lines: List[BranchTransferLineDetail]
    created_at: datetime


class BranchTransferDetailResponse(BaseModel):
    """Response for branch transfer detail"""
    success: bool = True
    data: BranchTransferDetail


# ============================================================================
# BRANCH REPORTS
# ============================================================================

class BranchSummary(BaseModel):
    """Branch summary data"""
    branch_id: str
    branch_name: str
    total_revenue: int
    total_expenses: int
    net_income: int
    transaction_count: int
    period_start: date
    period_end: date


class BranchSummaryResponse(BaseModel):
    """Response for branch summary"""
    success: bool = True
    data: BranchSummary


class BranchTrialBalanceRow(BaseModel):
    """Row in branch trial balance"""
    account_code: str
    account_name: str
    debit: int
    credit: int
    balance: int


class BranchTrialBalanceResponse(BaseModel):
    """Response for branch trial balance"""
    success: bool = True
    branch_name: str
    as_of_date: date
    rows: List[BranchTrialBalanceRow]
    total_debit: int
    total_credit: int


class BranchComparisonItem(BaseModel):
    """Branch comparison item"""
    branch_id: str
    branch_name: str
    revenue: int
    expenses: int
    net_income: int
    margin_percent: Decimal


class BranchComparisonResponse(BaseModel):
    """Response for branch comparison"""
    success: bool = True
    period_start: date
    period_end: date
    items: List[BranchComparisonItem]
    totals: Dict[str, int]


class BranchRankingItem(BaseModel):
    """Branch ranking item"""
    rank: int
    branch_id: str
    branch_name: str
    value: int
    percent_of_total: Decimal


class BranchRankingResponse(BaseModel):
    """Response for branch ranking"""
    success: bool = True
    ranking_by: str
    period_start: date
    period_end: date
    items: List[BranchRankingItem]
    total: int


# ============================================================================
# GENERIC RESPONSES
# ============================================================================

class BranchResponse(BaseModel):
    """Generic response for branch operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# Update forward references
BranchTreeNode.model_rebuild()
