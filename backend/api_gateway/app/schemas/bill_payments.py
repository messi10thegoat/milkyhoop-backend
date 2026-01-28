"""
Pydantic schemas for Bill Payments module (Pembayaran Keluar / Payment Out).

Bill Payments handle vendor payments for purchase invoices (bills):
- Can allocate to one or more bills
- Overpayment creates vendor deposit automatically
- Can pay using existing vendor deposit

Flow: draft -> posted -> voided (optional)

Journal Entry on POST (Cash/Bank):
    Dr. Hutang Usaha (A/P)              allocated_amount
    Dr. Uang Muka Vendor (if deposit)   unapplied_amount
    Dr. Biaya Bank (if any)             bank_fee_amount
        Cr. Kas/Bank                        total_amount + bank_fee_amount
        Cr. Potongan Pembelian (if any)     discount_amount

This mirrors ReceivePayments for the payable side.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import date


# =============================================================================
# TYPES
# =============================================================================

PaymentMethod = Literal[
    "cash",
    "bank_transfer",
    "check",
    "giro",
    "credit_card",
    "debit_card",
    "e_wallet",
    "other",
]

PaymentStatus = Literal["draft", "posted", "voided"]


# =============================================================================
# REQUEST MODELS - Allocation
# =============================================================================


class BillAllocationInput(BaseModel):
    """Single bill allocation in create/update request."""

    bill_id: str = Field(..., description="Bill UUID to allocate payment to")
    amount_applied: int = Field(..., gt=0, description="Amount to apply in IDR")


# =============================================================================
# REQUEST MODELS - Bill Payment
# =============================================================================


class CreateBillPaymentRequest(BaseModel):
    """Request body for creating a bill payment."""

    vendor_id: str = Field(..., description="Vendor UUID")
    vendor_name: str = Field(..., min_length=1, max_length=255)
    payment_date: date
    payment_method: PaymentMethod = "bank_transfer"
    bank_account_id: str = Field(..., description="Kas/Bank account UUID")
    bank_account_name: str = Field(..., min_length=1, max_length=255)
    total_amount: int = Field(..., gt=0, description="Total payment amount in IDR")

    # Optional discount and fees
    discount_amount: int = Field(0, ge=0, description="Early payment discount in IDR")
    discount_account_id: Optional[str] = Field(
        None, description="Discount account UUID (CoA)"
    )
    bank_fee_amount: int = Field(0, ge=0, description="Bank fee in IDR")
    bank_fee_account_id: Optional[str] = Field(
        None, description="Bank fee account UUID (CoA)"
    )

    # Check/Giro details
    check_number: Optional[str] = Field(None, max_length=50)
    check_due_date: Optional[date] = None
    check_bank_name: Optional[str] = Field(None, max_length=100)

    # Multi-currency support
    currency_code: str = Field("IDR", max_length=3)
    exchange_rate: float = Field(1.0, gt=0)

    # Source (from deposit or new payment)
    source_type: Literal["cash", "deposit"] = "cash"
    source_deposit_id: Optional[str] = Field(
        None, description="Vendor deposit UUID if using deposit"
    )

    reference_number: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    allocations: List[BillAllocationInput] = Field(default_factory=list)
    save_as_draft: bool = Field(
        False, description="If true, save as draft without posting"
    )

    @field_validator("vendor_name")
    @classmethod
    def validate_vendor_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Vendor name is required")
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


class UpdateBillPaymentRequest(BaseModel):
    """Request body for updating a draft bill payment."""

    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = Field(None, max_length=255)
    payment_date: Optional[date] = None
    payment_method: Optional[PaymentMethod] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    total_amount: Optional[int] = Field(None, gt=0)
    discount_amount: Optional[int] = Field(None, ge=0)
    discount_account_id: Optional[str] = None
    bank_fee_amount: Optional[int] = Field(None, ge=0)
    bank_fee_account_id: Optional[str] = None
    check_number: Optional[str] = None
    check_due_date: Optional[date] = None
    check_bank_name: Optional[str] = None
    currency_code: Optional[str] = None
    exchange_rate: Optional[float] = None
    source_type: Optional[Literal["cash", "deposit"]] = None
    source_deposit_id: Optional[str] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    allocations: Optional[List[BillAllocationInput]] = None


class VoidBillPaymentRequest(BaseModel):
    """Request body for voiding a posted payment."""

    void_reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - Allocation
# =============================================================================


class BillAllocationResponse(BaseModel):
    """Bill allocation in response."""

    id: str
    bill_id: str
    bill_number: str
    bill_amount: int
    remaining_before: int
    amount_applied: int
    remaining_after: int


# =============================================================================
# RESPONSE MODELS - Bill Payment
# =============================================================================


class BillPaymentListItem(BaseModel):
    """Bill payment item for list responses."""

    id: str
    payment_number: str
    vendor_id: Optional[str] = None
    vendor_name: str
    payment_date: str
    payment_method: str
    total_amount: int
    allocated_amount: int
    unapplied_amount: int
    status: str
    bill_count: int = 0
    created_at: str


class BillPaymentDetail(BaseModel):
    """Full bill payment detail."""

    id: str
    payment_number: str
    vendor_id: Optional[str] = None
    vendor_name: str

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
    bank_fee_amount: int
    bank_fee_account_id: Optional[str] = None

    # Multi-currency
    currency_code: str = "IDR"
    exchange_rate: float = 1.0
    amount_in_base_currency: int = 0

    # Check/Giro details
    check_number: Optional[str] = None
    check_due_date: Optional[str] = None
    check_bank_name: Optional[str] = None

    # Status
    status: str

    # Reference
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    tags: List[str] = []

    # Accounting links
    journal_id: Optional[str] = None
    journal_number: Optional[str] = None
    void_journal_id: Optional[str] = None

    # Overpayment deposit link
    created_deposit_id: Optional[str] = None
    created_deposit_number: Optional[str] = None

    # Allocations
    allocations: List[BillAllocationResponse] = []

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


class BillPaymentResponse(BaseModel):
    """Generic bill payment operation response."""

    success: bool
    message: str
    data: Optional[dict] = None


class BillPaymentDetailResponse(BaseModel):
    """Response for get bill payment detail."""

    success: bool = True
    data: BillPaymentDetail


class BillPaymentListResponse(BaseModel):
    """Response for list bill payments."""

    items: List[BillPaymentListItem]
    total: int
    has_more: bool = False


class BillPaymentSummaryResponse(BaseModel):
    """Response for bill payments summary."""

    success: bool = True
    data: dict


# =============================================================================
# RESPONSE MODELS - Supporting endpoints
# =============================================================================


class OpenBillItem(BaseModel):
    """Open bill item for vendor."""

    id: str
    bill_number: str
    bill_date: str
    due_date: str
    total_amount: int
    paid_amount: int
    remaining_amount: int
    is_overdue: bool = False
    overdue_days: int = 0


class OpenBillsResponse(BaseModel):
    """Response for vendor open bills."""

    bills: List[OpenBillItem]
    summary: dict


class AvailableVendorDepositItem(BaseModel):
    """Available deposit item for vendor."""

    id: str
    deposit_number: str
    deposit_date: str
    amount: int
    amount_applied: int
    amount_refunded: int
    remaining_amount: int


class AvailableVendorDepositsResponse(BaseModel):
    """Response for vendor available deposits."""

    deposits: List[AvailableVendorDepositItem]
    total_available: int
