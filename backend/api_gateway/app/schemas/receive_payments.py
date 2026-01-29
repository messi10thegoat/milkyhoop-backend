"""
Pydantic schemas for Receive Payments module (Penerimaan Pembayaran).

Receive Payments handle customer payments for invoices:
- Can allocate to one or more invoices
- Overpayment creates customer deposit automatically
- Can pay from existing customer deposit

Flow: draft -> posted -> voided (optional)

Journal Entry on POST (Cash/Bank):
    Dr. Kas/Bank                        total_amount
    Dr. Potongan Penjualan (if any)     discount_amount
        Cr. Piutang Usaha                   allocated_amount
        Cr. Uang Muka Pelanggan (if any)    unapplied_amount

Journal Entry on POST (From Deposit):
    Dr. Uang Muka Pelanggan             total_amount
        Cr. Piutang Usaha                   allocated_amount
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import date


# =============================================================================
# REQUEST MODELS - Allocation
# =============================================================================


class AllocationInput(BaseModel):
    """Single invoice allocation in create/update request."""

    invoice_id: str = Field(..., description="Invoice UUID to allocate payment to")
    amount_applied: int = Field(..., gt=0, description="Amount to apply in IDR")


# =============================================================================
# REQUEST MODELS - Receive Payment
# =============================================================================


class CreateReceivePaymentRequest(BaseModel):
    """Request body for creating a receive payment."""

    customer_id: str = Field(..., description="Customer UUID")
    customer_name: str = Field(..., min_length=1, max_length=255)
    payment_date: date
    payment_method: Literal["cash", "bank_transfer"]
    bank_account_id: str = Field(..., description="Kas/Bank account UUID (CoA)")
    bank_account_name: str = Field(..., min_length=1, max_length=255)
    total_amount: int = Field(..., gt=0, description="Total payment amount in IDR")
    discount_amount: int = Field(0, ge=0, description="Early payment discount in IDR")
    discount_account_id: Optional[str] = Field(
        None, description="Discount account UUID (CoA)"
    )
    source_type: Literal["cash", "deposit"] = "cash"
    source_deposit_id: Optional[str] = Field(
        None, description="Deposit UUID if source_type='deposit'"
    )
    reference_number: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    allocations: List[AllocationInput] = Field(default_factory=list)
    save_as_draft: bool = Field(
        False, description="If true, save as draft without posting"
    )
    idempotency_key: Optional[str] = Field(None, max_length=255, description="Unique key to prevent duplicate payments")

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Customer name is required")
        return v.strip()

    @field_validator("source_deposit_id")
    @classmethod
    def validate_source_deposit(cls, v, info):
        source_type = info.data.get("source_type", "cash")
        if source_type == "deposit" and not v:
            raise ValueError(
                "source_deposit_id is required when source_type is deposit"
            )
        if source_type == "cash" and v:
            raise ValueError("source_deposit_id must be null when source_type is cash")
        return v


class UpdateReceivePaymentRequest(BaseModel):
    """Request body for updating a draft receive payment."""

    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    payment_date: Optional[date] = None
    payment_method: Optional[Literal["cash", "bank_transfer"]] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    total_amount: Optional[int] = Field(None, gt=0)
    discount_amount: Optional[int] = Field(None, ge=0)
    discount_account_id: Optional[str] = None
    source_type: Optional[Literal["cash", "deposit"]] = None
    source_deposit_id: Optional[str] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    allocations: Optional[List[AllocationInput]] = None


class VoidPaymentRequest(BaseModel):
    """Request body for voiding a posted payment."""

    void_reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - Allocation
# =============================================================================


class AllocationResponse(BaseModel):
    """Invoice allocation in response."""

    id: str
    invoice_id: str
    invoice_number: str
    invoice_amount: int
    remaining_before: int
    amount_applied: int
    remaining_after: int


# =============================================================================
# RESPONSE MODELS - Receive Payment
# =============================================================================


class ReceivePaymentListItem(BaseModel):
    """Receive payment item for list responses."""

    id: str
    payment_number: str
    customer_id: Optional[str] = None
    customer_name: str
    payment_date: str
    payment_method: str
    source_type: str
    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    status: str
    invoice_count: int = 0
    created_at: str


class ReceivePaymentDetail(BaseModel):
    """Full receive payment detail."""

    id: str
    payment_number: str
    customer_id: Optional[str] = None
    customer_name: str

    # Payment details
    payment_date: str
    payment_method: str
    bank_account_id: str
    bank_account_name: str

    # Source
    source_type: str
    source_deposit_id: Optional[str] = None
    source_deposit_number: Optional[str] = None

    # Amounts
    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    discount_amount: int
    discount_account_id: Optional[str] = None

    # Status
    status: str

    # Reference
    reference_number: Optional[str] = None
    notes: Optional[str] = None

    # Accounting links
    journal_id: Optional[str] = None
    journal_number: Optional[str] = None
    void_journal_id: Optional[str] = None

    # Overpayment deposit link
    created_deposit_id: Optional[str] = None
    created_deposit_number: Optional[str] = None

    # Allocations
    allocations: List[AllocationResponse] = []

    # Audit
    posted_at: Optional[str] = None
    posted_by: Optional[str] = None
    voided_at: Optional[str] = None
    voided_by: Optional[str] = None
    void_reason: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================


class ReceivePaymentResponse(BaseModel):
    """Generic receive payment operation response."""

    success: bool
    message: str
    data: Optional[dict] = None


class ReceivePaymentDetailResponse(BaseModel):
    """Response for get receive payment detail."""

    success: bool = True
    data: ReceivePaymentDetail


class ReceivePaymentListResponse(BaseModel):
    """Response for list receive payments."""

    items: List[ReceivePaymentListItem]
    total: int
    has_more: bool = False


class ReceivePaymentSummaryResponse(BaseModel):
    """Response for receive payments summary."""

    success: bool = True
    data: dict


# =============================================================================
# RESPONSE MODELS - Supporting endpoints
# =============================================================================


class OpenInvoiceItem(BaseModel):
    """Open invoice item for customer."""

    id: str
    invoice_number: str
    invoice_date: str
    due_date: str
    total_amount: int
    paid_amount: int
    remaining_amount: int
    is_overdue: bool = False
    overdue_days: int = 0


class OpenInvoicesResponse(BaseModel):
    """Response for customer open invoices."""

    invoices: List[OpenInvoiceItem]
    summary: dict


class AvailableDepositItem(BaseModel):
    """Available deposit item for customer."""

    id: str
    deposit_number: str
    deposit_date: str
    amount: int
    amount_applied: int
    amount_refunded: int
    remaining_amount: int


class AvailableDepositsResponse(BaseModel):
    """Response for customer available deposits."""

    deposits: List[AvailableDepositItem]
    total_available: int
