"""
Pydantic schemas for Credit Notes module.

Credit Notes are used to handle:
- Sales returns (retur penjualan)
- Pricing adjustments
- AR reductions

Flow: draft -> posted -> partial/applied -> void (optional)
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date, datetime
from uuid import UUID


# =============================================================================
# REQUEST MODELS - Items
# =============================================================================

class CreditNoteItemCreate(BaseModel):
    """Item for creating a credit note."""
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
    original_invoice_item_id: Optional[str] = Field(None, description="Original invoice item UUID")

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if not v or not v.strip():
            raise ValueError('Description is required')
        return v.strip()


class CreditNoteItemUpdate(BaseModel):
    """Item for updating a credit note."""
    item_id: Optional[str] = None
    item_code: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[float] = Field(None, gt=0)
    unit: Optional[str] = None
    unit_price: Optional[int] = Field(None, ge=0)
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    discount_amount: Optional[int] = Field(None, ge=0)
    tax_code: Optional[str] = None
    tax_rate: Optional[float] = Field(None, ge=0, le=100)


# =============================================================================
# REQUEST MODELS - Credit Note
# =============================================================================

class CreateCreditNoteRequest(BaseModel):
    """Request body for creating a credit note (draft)."""
    customer_id: Optional[str] = Field(None, description="Customer UUID")
    customer_name: str = Field(..., min_length=1, max_length=255)
    credit_note_date: date
    original_invoice_id: Optional[str] = Field(None, description="Original invoice UUID")
    reason: Literal["return", "pricing_error", "discount", "damaged", "other"]
    reason_detail: Optional[str] = Field(None, max_length=500)
    ref_no: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    items: List[CreditNoteItemCreate] = Field(..., min_length=1)
    discount_percent: float = Field(0, ge=0, le=100, description="Overall discount percent")
    discount_amount: int = Field(0, ge=0, description="Overall discount amount")
    tax_rate: float = Field(0, ge=0, le=100, description="Overall tax rate")

    @field_validator('customer_name')
    @classmethod
    def validate_customer_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Customer name is required')
        return v.strip()


class UpdateCreditNoteRequest(BaseModel):
    """Request body for updating a credit note (draft only)."""
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    credit_note_date: Optional[date] = None
    original_invoice_id: Optional[str] = None
    reason: Optional[Literal["return", "pricing_error", "discount", "damaged", "other"]] = None
    reason_detail: Optional[str] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[CreditNoteItemCreate]] = None
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    discount_amount: Optional[int] = Field(None, ge=0)
    tax_rate: Optional[float] = Field(None, ge=0, le=100)


# =============================================================================
# REQUEST MODELS - Operations
# =============================================================================

class ApplyCreditNoteItem(BaseModel):
    """Single application to an invoice."""
    invoice_id: str = Field(..., description="Invoice UUID to apply credit to")
    amount: int = Field(..., gt=0, description="Amount to apply in IDR")


class ApplyCreditNoteRequest(BaseModel):
    """Request body for applying credit note to invoice(s)."""
    applications: List[ApplyCreditNoteItem] = Field(..., min_length=1)
    application_date: Optional[date] = Field(None, description="Application date, defaults to today")


class RefundCreditNoteRequest(BaseModel):
    """Request body for issuing a cash refund from credit note."""
    amount: int = Field(..., gt=0, description="Refund amount in IDR")
    refund_date: date
    payment_method: Literal["cash", "transfer", "check"]
    account_id: str = Field(..., description="Kas/Bank account UUID")
    bank_account_id: Optional[str] = Field(None, description="Bank account UUID if transfer")
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class VoidCreditNoteRequest(BaseModel):
    """Request body for voiding a credit note."""
    reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - Items
# =============================================================================

class CreditNoteItemResponse(BaseModel):
    """Credit note item in response."""
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
    line_number: int = 1


class CreditNoteApplicationResponse(BaseModel):
    """Credit note application in response."""
    id: str
    invoice_id: str
    invoice_number: Optional[str] = None
    amount_applied: int
    application_date: str
    created_at: str


class CreditNoteRefundResponse(BaseModel):
    """Credit note refund in response."""
    id: str
    amount: int
    refund_date: str
    payment_method: str
    account_id: str
    reference: Optional[str] = None
    created_at: str


# =============================================================================
# RESPONSE MODELS - Credit Note
# =============================================================================

class CreditNoteListItem(BaseModel):
    """Credit note item for list responses."""
    id: str
    credit_note_number: str
    customer_id: Optional[str] = None
    customer_name: str
    credit_note_date: str
    total_amount: int
    amount_applied: int = 0
    amount_refunded: int = 0
    remaining_amount: int = 0
    status: str
    reason: str
    created_at: str


class CreditNoteDetail(BaseModel):
    """Full credit note detail."""
    id: str
    credit_note_number: str
    customer_id: Optional[str] = None
    customer_name: str
    original_invoice_id: Optional[str] = None
    original_invoice_number: Optional[str] = None

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
    credit_note_date: str
    reason: str
    reason_detail: Optional[str] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None

    # Accounting links
    ar_id: Optional[str] = None
    journal_id: Optional[str] = None

    # Items, applications, refunds
    items: List[CreditNoteItemResponse] = []
    applications: List[CreditNoteApplicationResponse] = []
    refunds: List[CreditNoteRefundResponse] = []

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

class CreditNoteResponse(BaseModel):
    """Generic credit note operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class CreditNoteDetailResponse(BaseModel):
    """Response for get credit note detail."""
    success: bool = True
    data: CreditNoteDetail


class CreditNoteListResponse(BaseModel):
    """Response for list credit notes."""
    items: List[CreditNoteListItem]
    total: int
    has_more: bool


class CreditNoteSummaryResponse(BaseModel):
    """Response for credit notes summary."""
    success: bool = True
    data: Dict[str, Any]
