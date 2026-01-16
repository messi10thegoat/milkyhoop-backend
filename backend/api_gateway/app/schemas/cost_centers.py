"""
Cost Centers (Pusat Biaya) Schemas
"""
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field


# ============================================
# Cost Center Schemas
# ============================================

class CostCenterBase(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    manager_name: Optional[str] = Field(None, max_length=100)
    manager_email: Optional[str] = Field(None, max_length=100)


class CostCenterCreate(CostCenterBase):
    pass


class CostCenterUpdate(BaseModel):
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    manager_name: Optional[str] = Field(None, max_length=100)
    manager_email: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None


class CostCenterResponse(CostCenterBase):
    id: UUID
    tenant_id: str
    level: int
    path: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CostCenterTreeNode(CostCenterResponse):
    children_count: int = 0
    children: List["CostCenterTreeNode"] = []


# ============================================
# Cost Center Summary/Report Schemas
# ============================================

class CostCenterSummaryItem(BaseModel):
    account_type: str
    account_code: str
    account_name: str
    total_debit: int
    total_credit: int
    net_amount: int


class CostCenterSummaryResponse(BaseModel):
    cost_center: CostCenterResponse
    start_date: date
    end_date: date
    items: List[CostCenterSummaryItem]
    total_debit: int
    total_credit: int
    total_net: int


class CostCenterComparisonItem(BaseModel):
    cost_center_id: UUID
    cost_center_code: str
    cost_center_name: str
    total_revenue: int
    total_expense: int
    net_amount: int


class CostCenterComparisonResponse(BaseModel):
    start_date: date
    end_date: date
    items: List[CostCenterComparisonItem]


class CostCenterTransactionItem(BaseModel):
    journal_id: UUID
    entry_date: date
    reference: Optional[str]
    description: Optional[str]
    account_code: str
    account_name: str
    debit: int
    credit: int


class CostCenterTransactionsResponse(BaseModel):
    cost_center: CostCenterResponse
    start_date: date
    end_date: date
    transactions: List[CostCenterTransactionItem]
    total_debit: int
    total_credit: int


# List response
class CostCenterListResponse(BaseModel):
    items: List[CostCenterResponse]
    total: int
