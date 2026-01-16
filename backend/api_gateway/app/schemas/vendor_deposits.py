"""
Vendor Deposits (Uang Muka Vendor) Schemas
"""
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class VendorDepositStatus(str, Enum):
    draft = "draft"
    posted = "posted"
    partial = "partial"
    applied = "applied"
    void = "void"


class PaymentMethod(str, Enum):
    cash = "cash"
    transfer = "transfer"
    check = "check"
    giro = "giro"


# ============================================
# Vendor Deposit Schemas
# ============================================

class VendorDepositBase(BaseModel):
    deposit_date: date
    vendor_id: UUID
    amount: int
    payment_method: PaymentMethod = PaymentMethod.transfer
    bank_account_id: Optional[UUID] = None
    reference: Optional[str] = Field(None, max_length=100)
    purchase_order_id: Optional[UUID] = None
    notes: Optional[str] = None


class VendorDepositCreate(VendorDepositBase):
    pass


class VendorDepositUpdate(BaseModel):
    deposit_date: Optional[date] = None
    amount: Optional[int] = None
    payment_method: Optional[PaymentMethod] = None
    bank_account_id: Optional[UUID] = None
    reference: Optional[str] = Field(None, max_length=100)
    purchase_order_id: Optional[UUID] = None
    notes: Optional[str] = None


class VendorDepositResponse(VendorDepositBase):
    id: UUID
    tenant_id: str
    deposit_number: str
    applied_amount: int
    remaining_amount: int
    status: VendorDepositStatus
    journal_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None
    vendor_name: Optional[str] = None
    vendor_code: Optional[str] = None
    bank_account_name: Optional[str] = None
    purchase_order_number: Optional[str] = None

    class Config:
        from_attributes = True


class VendorDepositDetailResponse(VendorDepositResponse):
    applications: List["VendorDepositApplicationResponse"] = []
    refunds: List["VendorDepositRefundResponse"] = []


# ============================================
# Deposit Application Schemas
# ============================================

class VendorDepositApplicationCreate(BaseModel):
    bill_id: UUID
    amount: int
    applied_date: Optional[date] = None  # defaults to today


class VendorDepositApplicationResponse(BaseModel):
    id: UUID
    vendor_deposit_id: UUID
    bill_id: UUID
    bill_number: Optional[str] = None
    bill_date: Optional[date] = None
    amount: int
    applied_date: date
    journal_id: Optional[UUID] = None
    created_at: datetime
    created_by: Optional[UUID] = None

    class Config:
        from_attributes = True


# ============================================
# Deposit Refund Schemas
# ============================================

class VendorDepositRefundCreate(BaseModel):
    refund_date: date
    amount: int
    bank_account_id: Optional[UUID] = None
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class VendorDepositRefundResponse(BaseModel):
    id: UUID
    vendor_deposit_id: UUID
    refund_date: date
    amount: int
    bank_account_id: Optional[UUID] = None
    bank_account_name: Optional[str] = None
    reference: Optional[str] = None
    journal_id: Optional[UUID] = None
    notes: Optional[str] = None
    created_at: datetime
    created_by: Optional[UUID] = None

    class Config:
        from_attributes = True


# ============================================
# Apply to Bill Request/Response
# ============================================

class ApplyDepositRequest(BaseModel):
    bill_id: UUID
    amount: int
    applied_date: Optional[date] = None


class ApplyDepositResponse(BaseModel):
    application_id: UUID
    deposit_id: UUID
    deposit_number: str
    bill_id: UUID
    bill_number: str
    applied_amount: int
    deposit_remaining: int
    bill_remaining: int
    journal_id: UUID


# ============================================
# Available Deposits for Vendor
# ============================================

class AvailableDepositItem(BaseModel):
    id: UUID
    deposit_number: str
    deposit_date: date
    amount: int
    applied_amount: int
    remaining_amount: int
    reference: Optional[str] = None


class AvailableDepositsResponse(BaseModel):
    vendor_id: UUID
    vendor_name: str
    items: List[AvailableDepositItem]
    total_available: int


# ============================================
# Vendor Deposits List for Vendor
# ============================================

class VendorDepositsForVendorResponse(BaseModel):
    vendor_id: UUID
    vendor_name: str
    items: List[VendorDepositResponse]
    total_deposits: int
    total_applied: int
    total_remaining: int


# ============================================
# Summary
# ============================================

class VendorDepositSummary(BaseModel):
    total_deposits: int
    total_applied: int
    total_remaining: int
    deposit_count: int
    pending_count: int


# ============================================
# Post/Void Requests
# ============================================

class PostDepositResponse(BaseModel):
    deposit_id: UUID
    deposit_number: str
    status: VendorDepositStatus
    journal_id: UUID
    journal_number: str


class VoidDepositResponse(BaseModel):
    deposit_id: UUID
    deposit_number: str
    status: VendorDepositStatus
    reversal_journal_id: Optional[UUID] = None


# List response
class VendorDepositListResponse(BaseModel):
    items: List[VendorDepositResponse]
    total: int


# Forward reference update
VendorDepositDetailResponse.model_rebuild()
