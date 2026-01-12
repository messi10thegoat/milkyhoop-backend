"""
Pydantic schemas for Bills (Faktur Pembelian) module.

This module defines request and response models for the /api/bills endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal


# =============================================================================
# REQUEST MODELS
# =============================================================================

class BillItemRequest(BaseModel):
    """Single line item in a bill."""
    product_id: Optional[UUID] = None
    description: Optional[str] = None
    quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = None
    unit_price: int = Field(..., gt=0)

    @field_validator('quantity')
    @classmethod
    def validate_quantity(cls, v):
        if v <= 0:
            raise ValueError('Quantity must be greater than 0')
        return v


class CreateBillRequest(BaseModel):
    """Request body for creating a new bill."""
    invoice_number: Optional[str] = None  # Auto-generate if empty
    vendor_id: Optional[UUID] = None
    vendor_name: Optional[str] = None  # Required if vendor_id not provided
    issue_date: Optional[date] = None  # Default: today
    due_date: date
    notes: Optional[str] = None
    items: List[BillItemRequest] = Field(..., min_length=1)

    @field_validator('items')
    @classmethod
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one item is required')
        return v


class UpdateBillRequest(BaseModel):
    """Request body for updating a bill (only allowed if unpaid)."""
    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    due_date: Optional[date] = None
    notes: Optional[str] = None
    items: Optional[List[BillItemRequest]] = None


class RecordPaymentRequest(BaseModel):
    """Request body for recording a payment."""
    amount: int = Field(..., gt=0)
    payment_date: Optional[date] = None  # Default: today
    payment_method: Literal["cash", "transfer", "check", "other"]
    account_id: UUID  # Kas/Bank account from CoA
    reference: Optional[str] = None
    notes: Optional[str] = None


class MarkPaidRequest(BaseModel):
    """Request body for marking a bill as fully paid."""
    payment_method: Literal["cash", "transfer", "check", "other"]
    account_id: UUID
    reference: Optional[str] = None
    notes: Optional[str] = None


class VoidBillRequest(BaseModel):
    """Request body for voiding a bill."""
    reason: str = Field(..., min_length=1)


# =============================================================================
# RESPONSE MODELS - Nested Objects
# =============================================================================

class VendorInfo(BaseModel):
    """Vendor information for bill responses."""
    id: Optional[UUID] = None
    name: str
    initials: Optional[str] = None

    @field_validator('initials', mode='before')
    @classmethod
    def generate_initials(cls, v, info):
        if v:
            return v
        # Generate initials from name if not provided
        name = info.data.get('name', '')
        if name:
            words = name.split()
            if len(words) >= 2:
                return (words[0][0] + words[1][0]).upper()
            elif len(words) == 1 and len(words[0]) >= 2:
                return words[0][:2].upper()
        return None


class BillItemResponse(BaseModel):
    """Single line item in bill response."""
    id: UUID
    product_id: Optional[UUID] = None
    product_name: Optional[str] = None
    description: Optional[str] = None
    quantity: float
    unit: Optional[str] = None
    unit_price: int
    subtotal: int


class BillPaymentResponse(BaseModel):
    """Payment record in bill response."""
    id: UUID
    amount: int
    payment_date: date
    payment_method: str
    reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime


class BillAttachmentResponse(BaseModel):
    """Attachment in bill response."""
    id: UUID
    filename: str
    url: str
    uploaded_at: datetime


# =============================================================================
# RESPONSE MODELS - Main
# =============================================================================

class BillListItem(BaseModel):
    """Bill item for list responses."""
    id: UUID
    invoice_number: str
    vendor: VendorInfo
    amount: int
    amount_paid: int
    amount_due: int
    status: str
    issue_date: date
    due_date: date
    created_at: datetime
    updated_at: datetime


class BillListResponse(BaseModel):
    """Response for list bills endpoint."""
    items: List[BillListItem]
    total: int
    has_more: bool


class BillDetailResponse(BaseModel):
    """Response for get bill detail endpoint."""
    success: bool = True
    data: Dict[str, Any]


class CreateBillResponse(BaseModel):
    """Response for create bill endpoint."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class UpdateBillResponse(BaseModel):
    """Response for update bill endpoint."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class DeleteBillResponse(BaseModel):
    """Response for delete bill endpoint."""
    success: bool
    message: str


class RecordPaymentResponse(BaseModel):
    """Response for record payment endpoint."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class MarkPaidResponse(BaseModel):
    """Response for mark paid endpoint."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class VoidBillResponse(BaseModel):
    """Response for void bill endpoint."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# SUMMARY RESPONSE
# =============================================================================

class StatusBreakdown(BaseModel):
    """Breakdown by status for summary."""
    count: int
    amount: int
    percentage: float


class BillSummaryResponse(BaseModel):
    """Response for bills summary endpoint."""
    success: bool = True
    data: Dict[str, Any]


class BillSummaryData(BaseModel):
    """Summary data structure."""
    period: str
    period_label: str
    total_amount: int
    total_count: int
    vendor_count: int
    breakdown: Dict[str, StatusBreakdown]


# =============================================================================
# ATTACHMENT RESPONSE
# =============================================================================

class UploadAttachmentResponse(BaseModel):
    """Response for upload attachment endpoint."""
    success: bool
    data: Optional[Dict[str, Any]] = None
