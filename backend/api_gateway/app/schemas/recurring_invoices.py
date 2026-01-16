"""
Recurring Invoices Schemas
==========================
Pydantic models for recurring invoice templates.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# TYPES
# ============================================================================

RecurringFrequency = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]
RecurringStatus = Literal["active", "paused", "completed", "cancelled"]


# ============================================================================
# REQUEST MODELS
# ============================================================================

class RecurringInvoiceItemCreate(BaseModel):
    """Line item for recurring invoice template"""
    item_id: Optional[UUID] = None
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    description: str = Field(..., min_length=1)
    quantity: Decimal = Field(default=1, gt=0)
    unit: Optional[str] = None
    unit_price: int = Field(..., ge=0)
    discount_percent: Decimal = Field(default=0, ge=0, le=100)
    tax_id: Optional[UUID] = None
    tax_rate: Decimal = Field(default=0, ge=0)


class CreateRecurringInvoiceRequest(BaseModel):
    """Create a recurring invoice template"""
    template_name: str = Field(..., min_length=1, max_length=100)
    template_code: Optional[str] = Field(None, max_length=50)

    # Customer
    customer_id: UUID
    customer_name: Optional[str] = None

    # Warehouse
    warehouse_id: Optional[UUID] = None

    # Schedule
    frequency: RecurringFrequency
    interval_count: int = Field(default=1, ge=1, description="Every X frequency")
    day_of_month: Optional[int] = Field(None, ge=1, le=28, description="For monthly: day to generate")
    day_of_week: Optional[int] = Field(None, ge=0, le=6, description="For weekly: 0=Sun to 6=Sat")

    # Dates
    start_date: date
    end_date: Optional[date] = None  # None = indefinite

    # Invoice defaults
    due_days: int = Field(default=30, ge=0)
    payment_terms: Optional[str] = None

    # Discount (header level)
    discount_percent: Decimal = Field(default=0, ge=0, le=100)
    discount_amount: int = 0

    # Settings
    auto_send: bool = False
    auto_post: bool = False

    # Notes
    invoice_notes: Optional[str] = None
    internal_notes: Optional[str] = None

    # Line items
    items: List[RecurringInvoiceItemCreate] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.frequency == "weekly" and self.day_of_week is None:
            self.day_of_week = 1  # Default to Monday
        if self.frequency in ["monthly", "quarterly", "yearly"] and self.day_of_month is None:
            self.day_of_month = 1  # Default to 1st of month
        return self


class UpdateRecurringInvoiceRequest(BaseModel):
    """Update recurring invoice template"""
    template_name: Optional[str] = Field(None, min_length=1, max_length=100)
    template_code: Optional[str] = None
    customer_id: Optional[UUID] = None
    customer_name: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    frequency: Optional[RecurringFrequency] = None
    interval_count: Optional[int] = Field(None, ge=1)
    day_of_month: Optional[int] = Field(None, ge=1, le=28)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    end_date: Optional[date] = None
    due_days: Optional[int] = Field(None, ge=0)
    payment_terms: Optional[str] = None
    discount_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    discount_amount: Optional[int] = None
    auto_send: Optional[bool] = None
    auto_post: Optional[bool] = None
    invoice_notes: Optional[str] = None
    internal_notes: Optional[str] = None
    items: Optional[List[RecurringInvoiceItemCreate]] = None


class PauseRecurringInvoiceRequest(BaseModel):
    """Pause a recurring invoice"""
    reason: Optional[str] = Field(None, max_length=500)


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class RecurringInvoiceItemData(BaseModel):
    """Line item data"""
    id: UUID
    item_id: Optional[UUID] = None
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    description: str
    quantity: Decimal
    unit: Optional[str] = None
    unit_price: int
    discount_percent: Decimal
    discount_amount: int
    tax_id: Optional[UUID] = None
    tax_rate: Decimal
    tax_amount: int
    subtotal: int
    line_total: int
    line_number: int


class RecurringInvoiceData(BaseModel):
    """Recurring invoice template details"""
    id: UUID
    template_name: str
    template_code: Optional[str] = None
    customer_id: UUID
    customer_name: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    warehouse_name: Optional[str] = None
    frequency: RecurringFrequency
    interval_count: int
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    start_date: date
    end_date: Optional[date] = None
    next_invoice_date: date
    last_invoice_date: Optional[date] = None
    due_days: int
    payment_terms: Optional[str] = None
    subtotal: int
    discount_percent: Decimal
    discount_amount: int
    tax_amount: int
    total_amount: int
    auto_send: bool
    auto_post: bool
    status: RecurringStatus
    invoices_generated: int
    total_invoiced: int
    invoice_notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    pause_reason: Optional[str] = None


class RecurringInvoiceDetailData(RecurringInvoiceData):
    """Recurring invoice with line items"""
    items: List[RecurringInvoiceItemData]


class RecurringInvoiceListResponse(BaseModel):
    """Response for recurring invoice list"""
    success: bool = True
    data: List[RecurringInvoiceData]
    total: int
    has_more: bool = False


class RecurringInvoiceDetailResponse(BaseModel):
    """Response for single recurring invoice"""
    success: bool = True
    data: RecurringInvoiceDetailData


class CreateRecurringInvoiceResponse(BaseModel):
    """Response for creation"""
    success: bool = True
    data: RecurringInvoiceDetailData
    message: str = "Recurring invoice template created successfully"


class UpdateRecurringInvoiceResponse(BaseModel):
    """Response for update"""
    success: bool = True
    data: RecurringInvoiceDetailData
    message: str = "Recurring invoice template updated successfully"


class PauseRecurringInvoiceResponse(BaseModel):
    """Response for pause"""
    success: bool = True
    data: RecurringInvoiceData
    message: str = "Recurring invoice paused"


class ResumeRecurringInvoiceResponse(BaseModel):
    """Response for resume"""
    success: bool = True
    data: RecurringInvoiceData
    message: str = "Recurring invoice resumed"


class GenerateInvoiceResponse(BaseModel):
    """Response for manual invoice generation"""
    success: bool = True
    invoice_id: UUID
    invoice_number: str
    next_invoice_date: date
    message: str = "Invoice generated successfully"


class DueRecurringInvoice(BaseModel):
    """Recurring invoice due for generation"""
    id: UUID
    template_name: str
    customer_id: UUID
    customer_name: Optional[str] = None
    next_invoice_date: date
    total_amount: int
    invoices_generated: int


class DueRecurringInvoicesResponse(BaseModel):
    """Response for due recurring invoices"""
    success: bool = True
    data: List[DueRecurringInvoice]
    total: int


class ProcessDueResult(BaseModel):
    """Result of processing a due invoice"""
    recurring_invoice_id: UUID
    template_name: str
    success: bool
    invoice_id: Optional[UUID] = None
    invoice_number: Optional[str] = None
    error: Optional[str] = None


class ProcessDueResponse(BaseModel):
    """Response for processing all due invoices"""
    success: bool = True
    processed: int
    succeeded: int
    failed: int
    results: List[ProcessDueResult]


class GeneratedInvoice(BaseModel):
    """Invoice generated from recurring template"""
    invoice_id: UUID
    invoice_number: str
    invoice_date: date
    due_date: date
    total_amount: int
    status: str


class RecurringInvoiceHistoryResponse(BaseModel):
    """Response for generated invoices history"""
    success: bool = True
    recurring_invoice_id: UUID
    template_name: str
    data: List[GeneratedInvoice]
    total: int
