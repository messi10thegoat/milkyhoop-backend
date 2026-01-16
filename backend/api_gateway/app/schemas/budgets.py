"""
Budgets (Anggaran) Schemas
"""
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class BudgetType(str, Enum):
    annual = "annual"
    quarterly = "quarterly"
    monthly = "monthly"


class BudgetStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    active = "active"
    closed = "closed"


# ============================================
# Budget Item Schemas
# ============================================

class BudgetItemBase(BaseModel):
    account_id: UUID
    cost_center_id: Optional[UUID] = None
    jan_amount: int = 0
    feb_amount: int = 0
    mar_amount: int = 0
    apr_amount: int = 0
    may_amount: int = 0
    jun_amount: int = 0
    jul_amount: int = 0
    aug_amount: int = 0
    sep_amount: int = 0
    oct_amount: int = 0
    nov_amount: int = 0
    dec_amount: int = 0
    notes: Optional[str] = None


class BudgetItemCreate(BudgetItemBase):
    pass


class BudgetItemUpdate(BaseModel):
    jan_amount: Optional[int] = None
    feb_amount: Optional[int] = None
    mar_amount: Optional[int] = None
    apr_amount: Optional[int] = None
    may_amount: Optional[int] = None
    jun_amount: Optional[int] = None
    jul_amount: Optional[int] = None
    aug_amount: Optional[int] = None
    sep_amount: Optional[int] = None
    oct_amount: Optional[int] = None
    nov_amount: Optional[int] = None
    dec_amount: Optional[int] = None
    notes: Optional[str] = None


class BudgetItemResponse(BudgetItemBase):
    id: UUID
    budget_id: UUID
    annual_amount: int  # computed
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    cost_center_code: Optional[str] = None
    cost_center_name: Optional[str] = None

    class Config:
        from_attributes = True


# ============================================
# Budget Schemas
# ============================================

class BudgetBase(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    fiscal_year: int
    budget_type: BudgetType = BudgetType.annual


class BudgetCreate(BudgetBase):
    items: Optional[List[BudgetItemCreate]] = None


class BudgetUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None


class BudgetResponse(BudgetBase):
    id: UUID
    tenant_id: str
    status: BudgetStatus
    approved_at: Optional[datetime] = None
    approved_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None

    class Config:
        from_attributes = True


class BudgetDetailResponse(BudgetResponse):
    items: List[BudgetItemResponse] = []
    total_budget: int = 0


# ============================================
# Budget vs Actual Schemas
# ============================================

class BudgetVsActualItem(BaseModel):
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    budget_amount: int
    actual_amount: int
    variance: int
    percentage_used: float


class BudgetVsActualResponse(BaseModel):
    budget: BudgetResponse
    month: Optional[int] = None  # None = full year
    items: List[BudgetVsActualItem]
    total_budget: int
    total_actual: int
    total_variance: int


class BudgetVsActualMonthlyItem(BaseModel):
    month: int
    month_name: str
    budget_amount: int
    actual_amount: int
    variance: int
    percentage_used: float


class BudgetVsActualMonthlyResponse(BaseModel):
    budget: BudgetResponse
    account_id: Optional[UUID] = None
    account_name: Optional[str] = None
    months: List[BudgetVsActualMonthlyItem]


class VarianceAlertItem(BaseModel):
    budget_id: UUID
    budget_name: str
    fiscal_year: int
    account_id: UUID
    account_code: str
    account_name: str
    budget_amount: int
    actual_amount: int
    variance: int
    percentage_used: float


class VarianceAlertsResponse(BaseModel):
    threshold_percent: float
    items: List[VarianceAlertItem]


# ============================================
# Budget Summary Schemas
# ============================================

class BudgetSummaryByType(BaseModel):
    account_type: str
    total_budget: int
    total_actual: int
    total_variance: int
    avg_percentage_used: float


class BudgetSummaryResponse(BaseModel):
    budget: BudgetResponse
    by_type: List[BudgetSummaryByType]


class BudgetByCostCenterItem(BaseModel):
    cost_center_id: Optional[UUID]
    cost_center_code: Optional[str]
    cost_center_name: Optional[str]
    total_budget: int
    total_actual: int
    variance: int


class BudgetByCostCenterResponse(BaseModel):
    budget: BudgetResponse
    items: List[BudgetByCostCenterItem]


# ============================================
# Budget Revision Schema
# ============================================

class BudgetRevisionResponse(BaseModel):
    id: UUID
    budget_id: UUID
    revision_number: int
    revision_date: date
    reason: Optional[str]
    created_by: Optional[UUID]
    created_at: datetime


# ============================================
# Batch Import Schema
# ============================================

class BudgetItemImport(BaseModel):
    account_code: str
    cost_center_code: Optional[str] = None
    jan_amount: int = 0
    feb_amount: int = 0
    mar_amount: int = 0
    apr_amount: int = 0
    may_amount: int = 0
    jun_amount: int = 0
    jul_amount: int = 0
    aug_amount: int = 0
    sep_amount: int = 0
    oct_amount: int = 0
    nov_amount: int = 0
    dec_amount: int = 0


class BudgetItemsImportRequest(BaseModel):
    items: List[BudgetItemImport]


class BudgetItemsImportResponse(BaseModel):
    imported: int
    skipped: int
    errors: List[str]


# List response
class BudgetListResponse(BaseModel):
    items: List[BudgetResponse]
    total: int
