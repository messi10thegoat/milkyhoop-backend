"""
Recurring Bills (Tagihan Berulang) Schemas
"""
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class RecurringFrequency(str, Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    quarterly = "quarterly"
    yearly = "yearly"


class RecurringBillStatus(str, Enum):
    active = "active"
    paused = "paused"
    completed = "completed"
    cancelled = "cancelled"


# ============================================
# Recurring Bill Item Schemas
# ============================================

class RecurringBillItemBase(BaseModel):
    item_id: Optional[UUID] = None
    description: str
    quantity: float
    unit_price: int
    account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    tax_id: Optional[UUID] = None
    tax_amount: int = 0
    line_total: int
    sort_order: int = 0


class RecurringBillItemCreate(RecurringBillItemBase):
    pass


class RecurringBillItemUpdate(BaseModel):
    item_id: Optional[UUID] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[int] = None
    account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    tax_id: Optional[UUID] = None
    tax_amount: Optional[int] = None
    line_total: Optional[int] = None
    sort_order: Optional[int] = None


class RecurringBillItemResponse(RecurringBillItemBase):
    id: UUID
    recurring_bill_id: UUID
    item_name: Optional[str] = None
    item_code: Optional[str] = None
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    cost_center_name: Optional[str] = None
    tax_name: Optional[str] = None

    class Config:
        from_attributes = True


# ============================================
# Recurring Bill Schemas
# ============================================

class RecurringBillBase(BaseModel):
    template_name: str = Field(..., max_length=100)
    vendor_id: UUID
    frequency: RecurringFrequency
    interval_count: int = 1
    start_date: date
    end_date: Optional[date] = None
    due_days: int = 30
    subtotal: int
    discount_amount: int = 0
    tax_amount: int = 0
    total_amount: int
    auto_post: bool = False
    notes: Optional[str] = None


class RecurringBillCreate(RecurringBillBase):
    items: List[RecurringBillItemCreate]


class RecurringBillUpdate(BaseModel):
    template_name: Optional[str] = Field(None, max_length=100)
    frequency: Optional[RecurringFrequency] = None
    interval_count: Optional[int] = None
    end_date: Optional[date] = None
    due_days: Optional[int] = None
    subtotal: Optional[int] = None
    discount_amount: Optional[int] = None
    tax_amount: Optional[int] = None
    total_amount: Optional[int] = None
    auto_post: Optional[bool] = None
    notes: Optional[str] = None
    items: Optional[List[RecurringBillItemCreate]] = None


class RecurringBillResponse(RecurringBillBase):
    id: UUID
    tenant_id: str
    next_bill_date: date
    last_bill_date: Optional[date] = None
    status: RecurringBillStatus
    bills_generated: int
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None
    vendor_name: Optional[str] = None
    vendor_code: Optional[str] = None

    class Config:
        from_attributes = True


class RecurringBillDetailResponse(RecurringBillResponse):
    items: List[RecurringBillItemResponse] = []


# ============================================
# Due Recurring Bills
# ============================================

class DueRecurringBillItem(BaseModel):
    id: UUID
    template_name: str
    vendor_id: UUID
    vendor_name: str
    next_bill_date: date
    frequency: RecurringFrequency
    total_amount: int
    auto_post: bool


class DueRecurringBillsResponse(BaseModel):
    as_of_date: date
    items: List[DueRecurringBillItem]
    total_amount: int


# ============================================
# Generated Bill History
# ============================================

class GeneratedBillItem(BaseModel):
    bill_id: UUID
    bill_number: str
    bill_date: date
    due_date: date
    total_amount: int
    status: str
    paid_amount: int


class GeneratedBillsResponse(BaseModel):
    recurring_bill: RecurringBillResponse
    bills: List[GeneratedBillItem]
    total_generated: int
    total_amount: int


# ============================================
# Generate Bill Request/Response
# ============================================

class GenerateBillRequest(BaseModel):
    bill_date: Optional[date] = None  # defaults to next_bill_date
    post_immediately: Optional[bool] = None  # defaults to auto_post setting


class GenerateBillResponse(BaseModel):
    bill_id: UUID
    bill_number: str
    bill_date: date
    due_date: date
    total_amount: int
    status: str
    next_bill_date: date


# ============================================
# Process Due Bills (Batch)
# ============================================

class ProcessDueBillsRequest(BaseModel):
    as_of_date: Optional[date] = None  # defaults to today


class ProcessDueBillsResult(BaseModel):
    recurring_bill_id: UUID
    template_name: str
    bill_id: Optional[UUID] = None
    bill_number: Optional[str] = None
    success: bool
    error: Optional[str] = None


class ProcessDueBillsResponse(BaseModel):
    processed: int
    successful: int
    failed: int
    results: List[ProcessDueBillsResult]


# ============================================
# Statistics
# ============================================

class RecurringBillStats(BaseModel):
    total_active: int
    total_paused: int
    total_completed: int
    bills_generated_this_month: int
    total_amount_this_month: int
    due_today: int
    due_this_week: int


# List response
class RecurringBillListResponse(BaseModel):
    items: List[RecurringBillResponse]
    total: int
