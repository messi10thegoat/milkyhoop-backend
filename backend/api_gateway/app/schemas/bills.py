"""
Pydantic schemas for Bills (Faktur Pembelian) module.

This module defines request and response models for the /api/bills endpoints.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
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

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v):
        if v <= 0:
            raise ValueError("Quantity must be greater than 0")
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

    @field_validator("items")
    @classmethod
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one item is required")
        return v


class UpdateBillRequest(BaseModel):
    """Request body for updating a bill (only allowed if unpaid)."""

    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    due_date: Optional[date] = None
    notes: Optional[str] = None
    items: Optional[List[BillItemRequest]] = None


class RecordPaymentRequest(BaseModel):
    """Request body for recording a payment.

    Account handling:
    - bank_account_id (preferred): Links to bank_accounts table, creates bank transaction
    - account_id (legacy): Direct CoA UUID, no bank transaction created

    At least one must be provided. bank_account_id takes precedence if both are provided.
    """

    amount: int = Field(..., gt=0)
    payment_date: Optional[date] = None  # Default: today
    payment_method: Literal["cash", "transfer", "check", "other"]
    bank_account_id: Optional[UUID] = Field(
        None, description="Bank account UUID (preferred)"
    )
    account_id: Optional[UUID] = Field(
        None, description="CoA UUID (legacy, backward compat)"
    )
    reference: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_account(self):
        if not self.bank_account_id and not self.account_id:
            raise ValueError("Either bank_account_id or account_id is required")
        return self


class MarkPaidRequest(BaseModel):
    """Request body for marking a bill as fully paid.

    Account handling same as RecordPaymentRequest.
    """

    payment_method: Literal["cash", "transfer", "check", "other"]
    bank_account_id: Optional[UUID] = Field(
        None, description="Bank account UUID (preferred)"
    )
    account_id: Optional[UUID] = Field(
        None, description="CoA UUID (legacy, backward compat)"
    )
    reference: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_account(self):
        if not self.bank_account_id and not self.account_id:
            raise ValueError("Either bank_account_id or account_id is required")
        return self


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

    @field_validator("initials", mode="before")
    @classmethod
    def generate_initials(cls, v, info):
        if v:
            return v
        # Generate initials from name if not provided
        name = info.data.get("name", "")
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


# =============================================================================
# V2 REQUEST MODELS - Extended for Pharmacy
# =============================================================================


class BillItemRequestV2(BaseModel):
    """Extended line item for pharmacy bills (V2)."""

    product_id: Optional[UUID] = None
    product_code: Optional[str] = Field(
        None, max_length=100, description="Product code from supplier"
    )
    product_name: str = Field(..., min_length=1, description="Product name for display")
    qty: int = Field(..., gt=0, description="Quantity (must be > 0)")
    unit: Optional[str] = Field(None, max_length=20, description="Unit of measure")
    price: int = Field(..., gt=0, description="Unit price in Rupiah")
    discount_percent: Decimal = Field(
        Decimal("0"), ge=0, le=100, description="Item discount %"
    )
    batch_no: Optional[str] = Field(
        None, max_length=100, description="Batch/lot number"
    )
    exp_date: Optional[str] = Field(
        None, pattern=r"^\d{4}-\d{2}$", description="Expiry date (YYYY-MM)"
    )
    bonus_qty: int = Field(
        0, ge=0, description="Free/bonus quantity (not in calculation)"
    )

    @field_validator("exp_date")
    @classmethod
    def validate_exp_date(cls, v):
        if v:
            try:
                year, month = map(int, v.split("-"))
                if not (2020 <= year <= 2050):
                    raise ValueError("Year must be between 2020 and 2050")
                if not (1 <= month <= 12):
                    raise ValueError("Month must be between 01 and 12")
            except ValueError as e:
                raise ValueError(f"exp_date must be YYYY-MM format: {e}")
        return v


class CreateBillRequestV2(BaseModel):
    """
    Extended request for creating pharmacy bills (V2).

    **Discount Rules:**
    - invoice_discount: use percent OR amount (percent takes precedence)
    - cash_discount: use percent OR amount (percent takes precedence)
    """

    vendor_id: Optional[UUID] = Field(None, description="Existing vendor ID")
    vendor_name: Optional[str] = Field(
        None, description="Vendor name (required if vendor_id not provided)"
    )
    invoice_number: Optional[str] = Field(
        None, description="Auto-generate if empty (format: PB-YYMM-0001)"
    )
    ref_no: Optional[str] = Field(
        None, max_length=100, description="Reference number from vendor"
    )
    issue_date: Optional[date] = Field(None, description="Bill date (default: today)")
    due_date: date = Field(..., description="Payment due date")
    tax_rate: Literal[0, 11, 12] = Field(11, description="Tax rate: 0%, 11%, or 12%")
    tax_inclusive: bool = Field(False, description="True if prices include tax")
    invoice_discount_percent: Decimal = Field(Decimal("0"), ge=0, le=100)
    invoice_discount_amount: int = Field(0, ge=0)
    cash_discount_percent: Decimal = Field(Decimal("0"), ge=0, le=100)
    cash_discount_amount: int = Field(0, ge=0)
    dpp_manual: Optional[int] = Field(
        None, ge=0, description="Manual DPP override (null = auto)"
    )
    notes: Optional[str] = None
    items: List[BillItemRequestV2] = Field(
        ..., min_length=1, description="Line items (min 1)"
    )
    status: Literal["draft", "posted"] = Field("draft", description="Initial status")

    @field_validator("items")
    @classmethod
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one item is required")
        return v


class UpdateBillRequestV2(BaseModel):
    """
    Extended request for updating a draft bill (V2).

    **Important:** Only draft bills can be updated.
    """

    vendor_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    ref_no: Optional[str] = None
    due_date: Optional[date] = None
    tax_rate: Optional[Literal[0, 11, 12]] = None
    tax_inclusive: Optional[bool] = None
    invoice_discount_percent: Optional[Decimal] = None
    invoice_discount_amount: Optional[int] = None
    cash_discount_percent: Optional[Decimal] = None
    cash_discount_amount: Optional[int] = None
    dpp_manual: Optional[int] = None
    notes: Optional[str] = None
    items: Optional[List[BillItemRequestV2]] = None


# =============================================================================
# V2 RESPONSE MODELS
# =============================================================================


class BillCalculationResult(BaseModel):
    """Calculated totals for a bill."""

    subtotal: int = Field(..., description="Sum of (qty * price) for all items")
    item_discount_total: int = Field(..., description="Sum of item-level discounts")
    invoice_discount_total: int = Field(
        ..., description="Invoice-level discount amount"
    )
    cash_discount_total: int = Field(..., description="Cash/early payment discount")
    dpp: int = Field(..., description="Dasar Pengenaan Pajak (tax base)")
    tax_amount: int = Field(..., description="Calculated tax (dpp * tax_rate / 100)")
    grand_total: int = Field(..., description="Final total (dpp + tax_amount)")


class BillItemResponseV2(BaseModel):
    """Extended line item response (V2)."""

    id: UUID
    product_id: Optional[UUID] = None
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    qty: int
    unit: Optional[str] = None
    price: int
    discount_percent: float
    discount_amount: int
    total: int
    batch_no: Optional[str] = None
    exp_date: Optional[str] = None
    bonus_qty: int


class CreateBillResponseV2(BaseModel):
    """Response for create bill V2 endpoint."""

    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class OutstandingSummaryResponse(BaseModel):
    """Response for outstanding bills summary."""

    success: bool = True
    data: Optional[Dict[str, Any]] = None


class CalculateBillResponse(BaseModel):
    """Response for bill calculation preview."""

    success: bool = True
    calculation: BillCalculationResult


# =============================================================================
# ACTIVITY RESPONSE MODELS
# =============================================================================


class BillActivity(BaseModel):
    """Single activity entry for a bill."""

    id: str
    type: str  # created, updated, payment, voided, status_changed
    description: str
    actor_name: Optional[str] = None
    timestamp: str
    field_name: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    amount: Optional[int] = None
    details: Optional[str] = None


class BillActivityResponse(BaseModel):
    """Response for bill activity endpoint."""

    activities: List[BillActivity]
