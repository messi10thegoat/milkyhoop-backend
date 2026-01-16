"""
Sales Receipts Schemas
======================
Pydantic models for POS/Cash sales transactions.
Creates 2 journal entries: Sales + COGS.
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# TYPES
# ============================================================================

ReceiptStatus = Literal["completed", "void"]
PaymentMethod = Literal["cash", "card", "transfer", "qris", "gopay", "ovo", "dana", "other"]


# ============================================================================
# REQUEST MODELS
# ============================================================================

class SalesReceiptItemCreate(BaseModel):
    """Line item for sales receipt"""
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    description: Optional[str] = None
    quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = None
    unit_price: int = Field(..., ge=0)
    discount_percent: Decimal = Field(default=0, ge=0, le=100)
    tax_id: Optional[UUID] = None
    tax_rate: Decimal = Field(default=0, ge=0)
    batch_id: Optional[UUID] = None
    batch_number: Optional[str] = None
    serial_ids: Optional[List[UUID]] = None


class CreateSalesReceiptRequest(BaseModel):
    """Create a new sales receipt (atomic - creates and completes)"""
    receipt_date: date
    receipt_time: Optional[time] = None

    # Customer (optional for walk-in)
    customer_id: Optional[UUID] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    customer_phone: Optional[str] = Field(None, max_length=50)
    customer_email: Optional[str] = Field(None, max_length=100)

    # Location
    warehouse_id: Optional[UUID] = None

    # Discount (header level)
    discount_percent: Decimal = Field(default=0, ge=0, le=100)
    discount_amount: int = 0

    # Payment
    payment_method: PaymentMethod = "cash"
    payment_reference: Optional[str] = Field(None, max_length=100)
    amount_received: int = Field(..., ge=0)
    bank_account_id: Optional[UUID] = None

    # POS info
    pos_terminal: Optional[str] = Field(None, max_length=50)
    shift_number: Optional[str] = Field(None, max_length=50)

    # Notes
    notes: Optional[str] = None
    internal_notes: Optional[str] = None

    # Line items
    items: List[SalesReceiptItemCreate] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_payment(self):
        # For non-cash, bank_account_id might be required
        if self.payment_method != "cash" and self.bank_account_id is None:
            # Warning only - might be configured at system level
            pass
        return self


class VoidSalesReceiptRequest(BaseModel):
    """Void a sales receipt"""
    reason: str = Field(..., min_length=1, max_length=500)


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class SalesReceiptItemData(BaseModel):
    """Line item data"""
    id: UUID
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    description: Optional[str] = None
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
    unit_cost: int
    total_cost: int
    batch_id: Optional[UUID] = None
    batch_number: Optional[str] = None
    serial_ids: Optional[List[UUID]] = None
    line_number: int


class SalesReceiptData(BaseModel):
    """Sales receipt details"""
    id: UUID
    receipt_number: str
    receipt_date: date
    receipt_time: Optional[time] = None
    customer_id: Optional[UUID] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    warehouse_name: Optional[str] = None
    subtotal: int
    discount_percent: Decimal
    discount_amount: int
    tax_amount: int
    total_amount: int
    payment_method: PaymentMethod
    payment_reference: Optional[str] = None
    amount_received: int
    change_amount: int
    bank_account_id: Optional[UUID] = None
    journal_id: Optional[UUID] = None
    cogs_journal_id: Optional[UUID] = None
    status: ReceiptStatus
    cashier_name: Optional[str] = None
    pos_terminal: Optional[str] = None
    shift_number: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None


class SalesReceiptDetailData(SalesReceiptData):
    """Sales receipt with line items"""
    items: List[SalesReceiptItemData]


class SalesReceiptListResponse(BaseModel):
    """Response for receipt list"""
    success: bool = True
    data: List[SalesReceiptData]
    total: int
    has_more: bool = False


class SalesReceiptDetailResponse(BaseModel):
    """Response for single receipt"""
    success: bool = True
    data: SalesReceiptDetailData


class CreateSalesReceiptResponse(BaseModel):
    """Response for receipt creation"""
    success: bool = True
    data: SalesReceiptDetailData
    message: str = "Sales receipt created successfully"
    journal_id: Optional[UUID] = None
    cogs_journal_id: Optional[UUID] = None


class VoidSalesReceiptResponse(BaseModel):
    """Response for voiding receipt"""
    success: bool = True
    data: SalesReceiptData
    message: str = "Sales receipt voided successfully"
    reversal_journal_id: Optional[UUID] = None
    reversal_cogs_journal_id: Optional[UUID] = None


# ============================================================================
# SUMMARY MODELS
# ============================================================================

class DailySalesSummary(BaseModel):
    """Daily sales summary"""
    date: date
    total_receipts: int
    total_sales: int
    total_tax: int
    total_discount: int
    cash_amount: int
    card_amount: int
    transfer_amount: int
    qris_amount: int
    other_amount: int


class DailySummaryResponse(BaseModel):
    """Response for daily summary"""
    success: bool = True
    data: DailySalesSummary


class SalesByWarehouse(BaseModel):
    """Sales per warehouse"""
    warehouse_id: UUID
    warehouse_name: str
    total_receipts: int
    total_sales: int


class SalesByWarehouseResponse(BaseModel):
    """Response for sales by warehouse"""
    success: bool = True
    date_from: date
    date_to: date
    data: List[SalesByWarehouse]
    total_sales: int


class SalesByPaymentMethod(BaseModel):
    """Sales per payment method"""
    payment_method: PaymentMethod
    total_receipts: int
    total_amount: int
    percentage: Decimal


class SalesByPaymentMethodResponse(BaseModel):
    """Response for sales by payment method"""
    success: bool = True
    date_from: date
    date_to: date
    data: List[SalesByPaymentMethod]
    total_sales: int
