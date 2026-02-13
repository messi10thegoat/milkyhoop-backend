"""
Quotes (Penawaran) Schemas
Pre-sale quotes before conversion to Invoice or Sales Order.
NO journal entries - accounting impact happens on conversion.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from datetime import date
from decimal import Decimal
from uuid import UUID


# ============================================================================
# QUOTE ITEM SCHEMAS
# ============================================================================

class QuoteItemCreate(BaseModel):
    """Schema for creating a quote line item."""
    item_id: Optional[str] = Field(None, description="Reference to items table (optional)")
    description: str = Field(..., min_length=1, max_length=500, description="Item description")
    quantity: float = Field(1, gt=0, description="Quantity")
    unit: Optional[str] = Field(None, max_length=50, description="Unit of measure")
    unit_price: int = Field(..., ge=0, description="Unit price in smallest currency unit")
    discount_percent: float = Field(0, ge=0, le=100, description="Line discount percentage")
    tax_id: Optional[str] = Field(None, description="Tax code reference")
    tax_rate: float = Field(0, ge=0, le=100, description="Tax rate percentage")
    group_name: Optional[str] = Field(None, max_length=100, description="Item grouping")
    sort_order: int = Field(0, ge=0, description="Display order")

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if not v or not v.strip():
            raise ValueError('Description is required')
        return v.strip()


class QuoteItemUpdate(BaseModel):
    """Schema for updating a quote line item."""
    id: Optional[str] = Field(None, description="Item ID for existing items")
    item_id: Optional[str] = None
    description: Optional[str] = Field(None, max_length=500)
    quantity: Optional[float] = Field(None, gt=0)
    unit: Optional[str] = Field(None, max_length=50)
    unit_price: Optional[int] = Field(None, ge=0)
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    tax_id: Optional[str] = None
    tax_rate: Optional[float] = Field(None, ge=0, le=100)
    group_name: Optional[str] = Field(None, max_length=100)
    sort_order: Optional[int] = Field(None, ge=0)


class QuoteItemResponse(BaseModel):
    """Response schema for quote line item."""
    id: str
    item_id: Optional[str] = None
    description: str
    quantity: float
    unit: Optional[str] = None
    unit_price: int
    discount_percent: float = 0
    tax_id: Optional[str] = None
    tax_rate: float = 0
    tax_amount: int = 0
    line_total: int
    group_name: Optional[str] = None
    sort_order: int = 0


# ============================================================================
# QUOTE REQUEST SCHEMAS
# ============================================================================

class CreateQuoteRequest(BaseModel):
    """Schema for creating a new quote."""
    quote_date: date = Field(..., description="Quote date")
    expiry_date: Optional[date] = Field(None, description="Quote expiry date")
    customer_id: str = Field(..., description="Customer UUID")
    customer_name: str = Field(..., min_length=1, max_length=255, description="Customer name")
    customer_email: Optional[str] = Field(None, max_length=255, description="Customer email")
    reference: Optional[str] = Field(None, max_length=100, description="External reference")
    subject: Optional[str] = Field(None, max_length=255, description="Quote subject/title")
    discount_type: Literal['fixed', 'percentage'] = Field('fixed', description="Discount type")
    discount_value: float = Field(0, ge=0, description="Discount value")
    notes: Optional[str] = Field(None, description="Notes to customer")
    terms: Optional[str] = Field(None, description="Terms and conditions")
    footer: Optional[str] = Field(None, description="Footer text")
    items: List[QuoteItemCreate] = Field(..., min_length=1, description="Quote line items")

    @field_validator('customer_name')
    @classmethod
    def validate_customer_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Customer name is required')
        return v.strip()

    @field_validator('expiry_date')
    @classmethod
    def validate_expiry_date(cls, v, info):
        if v is not None and 'quote_date' in info.data:
            if v < info.data['quote_date']:
                raise ValueError('Expiry date cannot be before quote date')
        return v


class UpdateQuoteRequest(BaseModel):
    """Schema for updating an existing quote (draft only)."""
    quote_date: Optional[date] = None
    expiry_date: Optional[date] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    customer_email: Optional[str] = Field(None, max_length=255)
    reference: Optional[str] = Field(None, max_length=100)
    subject: Optional[str] = Field(None, max_length=255)
    discount_type: Optional[Literal['fixed', 'percentage']] = None
    discount_value: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None
    terms: Optional[str] = None
    footer: Optional[str] = None
    items: Optional[List[QuoteItemUpdate]] = None


# ============================================================================
# QUOTE WORKFLOW SCHEMAS
# ============================================================================

class SendQuoteRequest(BaseModel):
    """Schema for sending a quote to customer."""
    send_email: bool = Field(False, description="Send email notification to customer")
    email_subject: Optional[str] = Field(None, max_length=255, description="Custom email subject")
    email_message: Optional[str] = Field(None, description="Custom email message")


class DeclineQuoteRequest(BaseModel):
    """Schema for declining a quote."""
    reason: str = Field(..., min_length=1, max_length=500, description="Decline reason")


class ConvertToInvoiceRequest(BaseModel):
    """Schema for converting quote to invoice."""
    invoice_date: Optional[date] = Field(None, description="Invoice date (defaults to today)")
    due_date: Optional[date] = Field(None, description="Invoice due date")
    item_ids: Optional[List[str]] = Field(None, description="Specific items to include (all if empty)")


class ConvertToOrderRequest(BaseModel):
    """Schema for converting quote to sales order."""
    order_date: Optional[date] = Field(None, description="Order date (defaults to today)")
    expected_ship_date: Optional[date] = Field(None, description="Expected shipping date")
    item_ids: Optional[List[str]] = Field(None, description="Specific items to include (all if empty)")


class DuplicateQuoteRequest(BaseModel):
    """Schema for duplicating a quote."""
    quote_date: Optional[date] = Field(None, description="New quote date (defaults to today)")
    expiry_date: Optional[date] = Field(None, description="New expiry date")


# ============================================================================
# QUOTE RESPONSE SCHEMAS
# ============================================================================

class QuoteListItem(BaseModel):
    """Summary schema for quote list."""
    id: str
    quote_number: str
    quote_date: str
    expiry_date: Optional[str] = None
    customer_id: str
    customer_name: str
    subject: Optional[str] = None
    subtotal: int
    discount_amount: int
    tax_amount: int
    total_amount: int
    status: str
    converted_to_type: Optional[str] = None
    converted_to_id: Optional[str] = None
    created_at: str
    is_expired: bool = False


class QuoteDetail(BaseModel):
    """Full detail schema for single quote."""
    id: str
    quote_number: str
    quote_date: str
    expiry_date: Optional[str] = None
    customer_id: str
    customer_name: str
    customer_email: Optional[str] = None
    reference: Optional[str] = None
    subject: Optional[str] = None
    subtotal: int
    discount_type: str
    discount_value: float
    discount_amount: int
    tax_amount: int
    total_amount: int
    status: str
    converted_to_type: Optional[str] = None
    converted_to_id: Optional[str] = None
    converted_at: Optional[str] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    footer: Optional[str] = None
    items: List[QuoteItemResponse] = []
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    sent_at: Optional[str] = None
    viewed_at: Optional[str] = None
    accepted_at: Optional[str] = None
    declined_at: Optional[str] = None
    declined_reason: Optional[str] = None
    is_expired: bool = False


class QuoteListResponse(BaseModel):
    """Response for quote list endpoint."""
    items: List[QuoteListItem]
    total: int
    has_more: bool = False


class QuoteDetailResponse(BaseModel):
    """Response for quote detail endpoint."""
    success: bool
    data: QuoteDetail


class QuoteResponse(BaseModel):
    """Generic quote operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class QuoteSummary(BaseModel):
    """Quote statistics summary."""
    total_quotes: int
    draft_count: int
    sent_count: int
    accepted_count: int
    declined_count: int
    expired_count: int
    converted_count: int
    total_value: int  # Sum of all quote totals
    accepted_value: int  # Sum of accepted quotes
    pending_value: int  # Sum of sent quotes


class QuoteSummaryResponse(BaseModel):
    """Response for quote summary endpoint."""
    success: bool
    data: QuoteSummary


class ExpiringQuote(BaseModel):
    """Quote expiring soon."""
    id: str
    quote_number: str
    customer_name: str
    expiry_date: str
    total_amount: int
    days_until_expiry: int


class ExpiringQuotesResponse(BaseModel):
    """Response for expiring quotes endpoint."""
    success: bool
    data: List[ExpiringQuote]
    total: int
