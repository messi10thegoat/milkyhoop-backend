"""
Pydantic schemas for Sales Invoices module.

This module defines request and response models for the /api/sales-invoices endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date


# =============================================================================
# CONSTANTS
# =============================================================================

INVOICE_STATUSES = ["draft", "posted", "partial", "paid", "overdue", "void"]
PAYMENT_METHODS = ["cash", "transfer", "check", "other"]


# =============================================================================
# INVOICE ITEM MODELS
# =============================================================================

class InvoiceItemCreate(BaseModel):
    """Invoice line item for creation."""
    item_id: Optional[str] = None
    item_code: Optional[str] = Field(None, max_length=50)
    description: str = Field(..., min_length=1, max_length=255)
    quantity: float = Field(..., gt=0)
    unit: Optional[str] = Field(None, max_length=20)
    unit_price: int = Field(..., ge=0)
    discount_percent: float = Field(0, ge=0, le=100)
    discount_amount: int = Field(0, ge=0)
    tax_code: Optional[str] = Field(None, max_length=20)
    tax_rate: float = Field(0, ge=0, le=100)


class InvoiceItemResponse(BaseModel):
    """Invoice line item response."""
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


# =============================================================================
# PAYMENT MODELS
# =============================================================================

class InvoicePaymentCreate(BaseModel):
    """Payment record for creation."""
    amount: int = Field(..., gt=0)
    payment_date: date
    payment_method: Literal["cash", "transfer", "check", "other"]
    account_id: str = Field(..., description="CoA account ID for Kas/Bank")
    bank_account_id: Optional[str] = None
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class InvoicePaymentResponse(BaseModel):
    """Payment record response."""
    id: str
    amount: int
    payment_date: str
    payment_method: str
    account_id: str
    bank_account_id: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    journal_id: Optional[str] = None
    created_at: str


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateInvoiceRequest(BaseModel):
    """Request body for creating a sales invoice (draft)."""
    customer_id: Optional[str] = None
    customer_name: str = Field(..., min_length=1, max_length=255)
    invoice_date: date
    due_date: date
    ref_no: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None

    # Items
    items: List[InvoiceItemCreate] = Field(..., min_length=1)

    # Invoice-level discount
    discount_percent: float = Field(0, ge=0, le=100)
    discount_amount: int = Field(0, ge=0)

    # Tax
    tax_rate: float = Field(0, ge=0, le=100)

    @field_validator('customer_name')
    @classmethod
    def validate_customer_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Customer name is required')
        return v.strip()

    @field_validator('items')
    @classmethod
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one item is required')
        return v


class UpdateInvoiceRequest(BaseModel):
    """Request body for updating a draft invoice."""
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[InvoiceItemCreate]] = None
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    discount_amount: Optional[int] = Field(None, ge=0)
    tax_rate: Optional[float] = Field(None, ge=0, le=100)


class PostInvoiceRequest(BaseModel):
    """Request body for posting an invoice to accounting."""
    sales_account_id: Optional[str] = Field(None, description="Override default sales account")


class VoidInvoiceRequest(BaseModel):
    """Request body for voiding an invoice."""
    reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - List Item
# =============================================================================

class InvoiceListItem(BaseModel):
    """Invoice item for list responses."""
    id: str
    invoice_number: str
    customer_id: Optional[str] = None
    customer_name: str
    invoice_date: str
    due_date: str
    total_amount: int
    amount_paid: int
    status: str
    created_at: str


class InvoiceListResponse(BaseModel):
    """Response for list invoices endpoint."""
    items: List[InvoiceListItem]
    total: int
    has_more: bool


# =============================================================================
# RESPONSE MODELS - Summary
# =============================================================================

class InvoiceSummary(BaseModel):
    """Invoice summary statistics."""
    total_count: int
    draft_count: int
    posted_count: int
    partial_count: int
    paid_count: int
    overdue_count: int
    total_outstanding: int
    total_overdue: int


class InvoiceSummaryResponse(BaseModel):
    """Response for invoice summary endpoint."""
    success: bool = True
    data: InvoiceSummary


# =============================================================================
# RESPONSE MODELS - Detail
# =============================================================================

class InvoiceDetail(BaseModel):
    """Full invoice detail."""
    id: str
    invoice_number: str
    customer_id: Optional[str] = None
    customer_name: str
    invoice_date: str
    due_date: str
    ref_no: Optional[str] = None
    notes: Optional[str] = None

    # Amounts
    subtotal: int
    discount_percent: float = 0
    discount_amount: int = 0
    tax_rate: float = 0
    tax_amount: int = 0
    total_amount: int
    amount_paid: int = 0

    # Status
    status: str

    # Items and payments
    items: List[InvoiceItemResponse] = []
    payments: List[InvoicePaymentResponse] = []

    # Accounting links
    ar_id: Optional[str] = None
    journal_id: Optional[str] = None

    # Audit
    posted_at: Optional[str] = None
    posted_by: Optional[str] = None
    voided_at: Optional[str] = None
    voided_reason: Optional[str] = None
    created_at: str
    updated_at: str


class InvoiceDetailResponse(BaseModel):
    """Response for get invoice detail endpoint."""
    success: bool = True
    data: InvoiceDetail


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class InvoiceResponse(BaseModel):
    """Generic invoice operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# CALCULATION RESPONSE
# =============================================================================

class InvoiceCalculation(BaseModel):
    """Invoice calculation preview."""
    subtotal: int
    discount_amount: int
    tax_amount: int
    total_amount: int
    items: List[Dict[str, Any]]


class InvoiceCalculationResponse(BaseModel):
    """Response for invoice calculation endpoint."""
    success: bool = True
    data: InvoiceCalculation
