"""
AR/AP Aging Reports (Umur Piutang & Hutang) Schemas
"""
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class AgingType(str, Enum):
    ar = "ar"  # Accounts Receivable
    ap = "ap"  # Accounts Payable


# ============================================
# Aging Brackets Configuration
# ============================================

class AgingBracketsBase(BaseModel):
    bracket_1_days: int = 30
    bracket_2_days: int = 60
    bracket_3_days: int = 90
    bracket_4_days: int = 120
    bracket_1_label: str = "Current"
    bracket_2_label: str = "1-30 Days"
    bracket_3_label: str = "31-60 Days"
    bracket_4_label: str = "61-90 Days"
    bracket_5_label: str = "90+ Days"


class AgingBracketsUpdate(BaseModel):
    bracket_1_days: Optional[int] = None
    bracket_2_days: Optional[int] = None
    bracket_3_days: Optional[int] = None
    bracket_4_days: Optional[int] = None
    bracket_1_label: Optional[str] = None
    bracket_2_label: Optional[str] = None
    bracket_3_label: Optional[str] = None
    bracket_4_label: Optional[str] = None
    bracket_5_label: Optional[str] = None


class AgingBracketsResponse(AgingBracketsBase):
    id: UUID
    tenant_id: str
    bracket_type: AgingType
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================
# AR Aging Schemas
# ============================================

class ARAgingSummary(BaseModel):
    total_current: int
    total_1_30: int
    total_31_60: int
    total_61_90: int
    total_91_120: int
    total_over_120: int
    grand_total: int
    overdue_count: int


class ARAgingSummaryResponse(BaseModel):
    as_of_date: date
    summary: ARAgingSummary
    brackets: Optional[AgingBracketsResponse] = None


class ARAgingDetailItem(BaseModel):
    customer_id: UUID
    customer_name: str
    customer_code: Optional[str] = None
    current_amount: int
    days_1_30: int
    days_31_60: int
    days_61_90: int
    days_91_120: int
    days_over_120: int
    total_balance: int
    oldest_invoice_date: Optional[date] = None
    invoice_count: int


class ARAgingDetailResponse(BaseModel):
    as_of_date: date
    items: List[ARAgingDetailItem]
    summary: ARAgingSummary


class ARCustomerAgingItem(BaseModel):
    invoice_id: UUID
    invoice_number: str
    invoice_date: date
    due_date: date
    total_amount: int
    paid_amount: int
    balance: int
    days_overdue: int
    aging_bucket: str


class ARCustomerAgingResponse(BaseModel):
    customer_id: UUID
    customer_name: str
    as_of_date: date
    items: List[ARCustomerAgingItem]
    total_balance: int


# ============================================
# AP Aging Schemas
# ============================================

class APAgingSummary(BaseModel):
    total_current: int
    total_1_30: int
    total_31_60: int
    total_61_90: int
    total_91_120: int
    total_over_120: int
    grand_total: int
    overdue_count: int


class APAgingSummaryResponse(BaseModel):
    as_of_date: date
    summary: APAgingSummary
    brackets: Optional[AgingBracketsResponse] = None


class APAgingDetailItem(BaseModel):
    vendor_id: UUID
    vendor_name: str
    vendor_code: Optional[str] = None
    current_amount: int
    days_1_30: int
    days_31_60: int
    days_61_90: int
    days_91_120: int
    days_over_120: int
    total_balance: int
    oldest_bill_date: Optional[date] = None
    bill_count: int


class APAgingDetailResponse(BaseModel):
    as_of_date: date
    items: List[APAgingDetailItem]
    summary: APAgingSummary


class APVendorAgingItem(BaseModel):
    bill_id: UUID
    bill_number: str
    bill_date: date
    due_date: date
    total_amount: int
    paid_amount: int
    balance: int
    days_overdue: int
    aging_bucket: str


class APVendorAgingResponse(BaseModel):
    vendor_id: UUID
    vendor_name: str
    as_of_date: date
    items: List[APVendorAgingItem]
    total_balance: int


# ============================================
# Aging Snapshot Schemas
# ============================================

class AgingSnapshotResponse(BaseModel):
    id: UUID
    tenant_id: str
    snapshot_date: date
    snapshot_type: AgingType
    total_current: int
    total_bracket_1: int
    total_bracket_2: int
    total_bracket_3: int
    total_bracket_4: int
    total_overdue: int
    grand_total: int
    created_at: datetime

    class Config:
        from_attributes = True


class AgingSnapshotListResponse(BaseModel):
    items: List[AgingSnapshotResponse]
    total: int


class CreateSnapshotRequest(BaseModel):
    snapshot_type: AgingType
    as_of_date: Optional[date] = None  # defaults to today


class CreateSnapshotResponse(BaseModel):
    snapshot_id: UUID
    snapshot_type: AgingType
    as_of_date: date


# ============================================
# Aging Trend Schemas
# ============================================

class AgingTrendItem(BaseModel):
    snapshot_date: date
    total_current: int
    total_overdue: int
    grand_total: int


class AgingTrendResponse(BaseModel):
    snapshot_type: AgingType
    start_date: date
    end_date: date
    items: List[AgingTrendItem]


# ============================================
# Export Request
# ============================================

class AgingExportRequest(BaseModel):
    as_of_date: Optional[date] = None
    format: str = "xlsx"  # xlsx, csv


class AgingExportResponse(BaseModel):
    download_url: str
    filename: str
    expires_at: datetime
