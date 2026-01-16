"""
Pydantic schemas for Vendor Credits module.

Vendor Credits are used to handle:
- Purchase returns (retur pembelian)
- Pricing adjustments from vendors
- AP reductions

Flow: draft -> posted -> partial/applied -> void (optional)
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date, datetime
from uuid import UUID


# =============================================================================
# REQUEST MODELS - Items
# =============================================================================

class VendorCreditItemCreate(BaseModel):
    """Item for creating a vendor credit."""
    item_id: Optional[str] = Field(None, description="Product UUID")
    item_code: Optional[str] = Field(None, max_length=50)
    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0)
    unit: Optional[str] = Field(None, max_length=20)
    unit_price: int = Field(..., ge=0, description="Price per unit in IDR")
    discount_percent: float = Field(0, ge=0, le=100)
    discount_amount: int = Field(0, ge=0)
    tax_code: Optional[str] = Field(None, max_length=20)
    tax_rate: float = Field(0, ge=0, le=100)
    original_bill_item_id: Optional[str] = Field(None, description="Original bill item UUID")
    batch_no: Optional[str] = Field(None, max_length=100)
    exp_date: Optional[date] = None

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if not v or not v.strip():
            raise ValueError('Description is required')
        return v.strip()


# =============================================================================
# REQUEST MODELS - Vendor Credit
# =============================================================================

class CreateVendorCreditRequest(BaseModel):
    """Request body for creating a vendor credit (draft)."""
    vendor_id: Optional[str] = Field(None, description="Vendor UUID")
    vendor_name: str = Field(..., min_length=1, max_length=255)
    vendor_credit_date: date
    original_bill_id: Optional[str] = Field(None, description="Original bill UUID")
    reason: Literal["return", "pricing_error", "discount", "damaged", "other"]
    reason_detail: Optional[str] = Field(None, max_length=500)
    ref_no: Optional[str] = Field(None, max_length=100, description="Vendor's credit note number")
    notes: Optional[str] = None
    items: List[VendorCreditItemCreate] = Field(..., min_length=1)
    discount_percent: float = Field(0, ge=0, le=100, description="Overall discount percent")
    discount_amount: int = Field(0, ge=0, description="Overall discount amount")
    tax_rate: float = Field(0, ge=0, le=100, description="Overall tax rate")

    @field_validator('vendor_name')
    @classmethod
    def validate_vendor_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Vendor name is required')
        return v.strip()


class UpdateVendorCreditRequest(BaseModel):
    """Request body for updating a vendor credit (draft only)."""
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = Field(None, max_length=255)
    vendor_credit_date: Optional[date] = None
    original_bill_id: Optional[str] = None
    reason: Optional[Literal["return", "pricing_error", "discount", "damaged", "other"]] = None
    reason_detail: Optional[str] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[VendorCreditItemCreate]] = None
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    discount_amount: Optional[int] = Field(None, ge=0)
    tax_rate: Optional[float] = Field(None, ge=0, le=100)


# =============================================================================
# REQUEST MODELS - Operations
# =============================================================================

class ApplyVendorCreditItem(BaseModel):
    """Single application to a bill."""
    bill_id: str = Field(..., description="Bill UUID to apply credit to")
    amount: int = Field(..., gt=0, description="Amount to apply in IDR")


class ApplyVendorCreditRequest(BaseModel):
    """Request body for applying vendor credit to bill(s)."""
    applications: List[ApplyVendorCreditItem] = Field(..., min_length=1)
    application_date: Optional[date] = Field(None, description="Application date, defaults to today")


class ReceiveRefundRequest(BaseModel):
    """Request body for recording cash received from vendor."""
    amount: int = Field(..., gt=0, description="Refund amount received in IDR")
    refund_date: date
    payment_method: Literal["cash", "transfer", "check"]
    account_id: str = Field(..., description="Kas/Bank account UUID")
    bank_account_id: Optional[str] = Field(None, description="Bank account UUID if transfer")
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class VoidVendorCreditRequest(BaseModel):
    """Request body for voiding a vendor credit."""
    reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - Items
# =============================================================================

class VendorCreditItemResponse(BaseModel):
    """Vendor credit item in response."""
    id: str
    item_id: Optional[str] = None
    item_code: Optional[str] = None
    description: str
    quantity: float
    unit: Optional[str] = None
    unit_price: int
    discount_percent: float = 0
    discount_amount: int = 0
    tax_code: Optional[str] = None
    tax_rate: float = 0
    tax_amount: int = 0
    subtotal: int
    total: int
    batch_no: Optional[str] = None
    exp_date: Optional[str] = None
    line_number: int = 1


class VendorCreditApplicationResponse(BaseModel):
    """Vendor credit application in response."""
    id: str
    bill_id: str
    bill_number: Optional[str] = None
    amount_applied: int
    application_date: str
    created_at: str


class VendorCreditRefundResponse(BaseModel):
    """Vendor credit refund in response."""
    id: str
    amount: int
    refund_date: str
    payment_method: str
    account_id: str
    reference: Optional[str] = None
    created_at: str


# =============================================================================
# RESPONSE MODELS - Vendor Credit
# =============================================================================

class VendorCreditListItem(BaseModel):
    """Vendor credit item for list responses."""
    id: str
    vendor_credit_number: str
    vendor_id: Optional[str] = None
    vendor_name: str
    vendor_credit_date: str
    total_amount: int
    amount_applied: int = 0
    amount_refunded: int = 0
    remaining_amount: int = 0
    status: str
    reason: str
    ref_no: Optional[str] = None
    created_at: str


class VendorCreditDetail(BaseModel):
    """Full vendor credit detail."""
    id: str
    vendor_credit_number: str
    vendor_id: Optional[str] = None
    vendor_name: str
    original_bill_id: Optional[str] = None
    original_bill_number: Optional[str] = None

    # Amounts
    subtotal: int
    discount_percent: float = 0
    discount_amount: int = 0
    tax_rate: float = 0
    tax_amount: int = 0
    total_amount: int
    amount_applied: int = 0
    amount_refunded: int = 0
    remaining_amount: int = 0

    # Status & dates
    status: str
    vendor_credit_date: str
    reason: str
    reason_detail: Optional[str] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None

    # Accounting links
    ap_id: Optional[str] = None
    journal_id: Optional[str] = None

    # Items, applications, refunds
    items: List[VendorCreditItemResponse] = []
    applications: List[VendorCreditApplicationResponse] = []
    refunds: List[VendorCreditRefundResponse] = []

    # Audit
    posted_at: Optional[str] = None
    posted_by: Optional[str] = None
    voided_at: Optional[str] = None
    voided_reason: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class VendorCreditResponse(BaseModel):
    """Generic vendor credit operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class VendorCreditDetailResponse(BaseModel):
    """Response for get vendor credit detail."""
    success: bool = True
    data: VendorCreditDetail


class VendorCreditListResponse(BaseModel):
    """Response for list vendor credits."""
    items: List[VendorCreditListItem]
    total: int
    has_more: bool


class VendorCreditSummaryResponse(BaseModel):
    """Response for vendor credits summary."""
    success: bool = True
    data: Dict[str, Any]
