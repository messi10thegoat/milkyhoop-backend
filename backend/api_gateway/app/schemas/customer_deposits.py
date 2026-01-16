"""
Pydantic schemas for Customer Deposits module (Uang Muka Pelanggan).

Customer Deposits are advance payments from customers that can be:
- Applied to invoices
- Refunded back to customers

Flow: draft -> posted -> partial/applied -> void (optional)

Journal Entry on POST (Receive):
    Dr. Kas/Bank (1-10100/1-10200)           amount
        Cr. Uang Muka Pelanggan (2-10400)        amount

Journal Entry on APPLY (to Invoice):
    Dr. Uang Muka Pelanggan (2-10400)        applied_amount
        Cr. Piutang Usaha (1-10300)              applied_amount

Journal Entry on REFUND:
    Dr. Uang Muka Pelanggan (2-10400)        refund_amount
        Cr. Kas/Bank (1-10100/1-10200)           refund_amount
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date


# =============================================================================
# REQUEST MODELS - Customer Deposit
# =============================================================================

class CreateCustomerDepositRequest(BaseModel):
    """Request body for creating a customer deposit (draft)."""
    customer_id: Optional[str] = Field(None, description="Customer UUID")
    customer_name: str = Field(..., min_length=1, max_length=255)
    amount: int = Field(..., gt=0, description="Deposit amount in IDR")
    deposit_date: date
    payment_method: Literal["cash", "transfer", "check", "other"]
    account_id: str = Field(..., description="Kas/Bank account UUID (CoA)")
    bank_account_id: Optional[str] = Field(None, description="Bank account UUID if transfer")
    reference: Optional[str] = Field(None, max_length=100, description="Payment reference number")
    notes: Optional[str] = None
    auto_post: bool = Field(False, description="Automatically post after creation")

    @field_validator('customer_name')
    @classmethod
    def validate_customer_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Customer name is required')
        return v.strip()


class UpdateCustomerDepositRequest(BaseModel):
    """Request body for updating a customer deposit (draft only)."""
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    amount: Optional[int] = Field(None, gt=0)
    deposit_date: Optional[date] = None
    payment_method: Optional[Literal["cash", "transfer", "check", "other"]] = None
    account_id: Optional[str] = None
    bank_account_id: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None


# =============================================================================
# REQUEST MODELS - Operations
# =============================================================================

class ApplyDepositItem(BaseModel):
    """Single application to an invoice."""
    invoice_id: str = Field(..., description="Invoice UUID to apply deposit to")
    amount: int = Field(..., gt=0, description="Amount to apply in IDR")


class ApplyCustomerDepositRequest(BaseModel):
    """Request body for applying customer deposit to invoice(s)."""
    applications: List[ApplyDepositItem] = Field(..., min_length=1)
    application_date: Optional[date] = Field(None, description="Application date, defaults to today")


class RefundCustomerDepositRequest(BaseModel):
    """Request body for refunding deposit to customer."""
    amount: int = Field(..., gt=0, description="Refund amount in IDR")
    refund_date: date
    payment_method: Literal["cash", "transfer", "check", "other"]
    account_id: str = Field(..., description="Kas/Bank account UUID (CoA)")
    bank_account_id: Optional[str] = Field(None, description="Bank account UUID if transfer")
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class VoidCustomerDepositRequest(BaseModel):
    """Request body for voiding a customer deposit."""
    reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - Applications and Refunds
# =============================================================================

class DepositApplicationResponse(BaseModel):
    """Deposit application in response."""
    id: str
    invoice_id: str
    invoice_number: Optional[str] = None
    amount_applied: int
    application_date: str
    created_at: str


class DepositRefundResponse(BaseModel):
    """Deposit refund in response."""
    id: str
    amount: int
    refund_date: str
    payment_method: str
    account_id: str
    reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: str


# =============================================================================
# RESPONSE MODELS - Customer Deposit
# =============================================================================

class CustomerDepositListItem(BaseModel):
    """Customer deposit item for list responses."""
    id: str
    deposit_number: str
    customer_id: Optional[str] = None
    customer_name: str
    deposit_date: str
    amount: int
    amount_applied: int = 0
    amount_refunded: int = 0
    remaining_amount: int = 0
    status: str
    payment_method: str
    reference: Optional[str] = None
    created_at: str


class CustomerDepositDetail(BaseModel):
    """Full customer deposit detail."""
    id: str
    deposit_number: str
    customer_id: Optional[str] = None
    customer_name: str

    # Amounts
    amount: int
    amount_applied: int = 0
    amount_refunded: int = 0
    remaining_amount: int = 0

    # Payment details
    deposit_date: str
    payment_method: str
    account_id: str
    account_code: Optional[str] = None
    account_name: Optional[str] = None
    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None

    # Status
    status: str

    # Accounting links
    journal_id: Optional[str] = None
    journal_number: Optional[str] = None

    # Applications and refunds
    applications: List[DepositApplicationResponse] = []
    refunds: List[DepositRefundResponse] = []

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

class CustomerDepositResponse(BaseModel):
    """Generic customer deposit operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class CustomerDepositDetailResponse(BaseModel):
    """Response for get customer deposit detail."""
    success: bool = True
    data: CustomerDepositDetail


class CustomerDepositListResponse(BaseModel):
    """Response for list customer deposits."""
    items: List[CustomerDepositListItem]
    total: int
    has_more: bool = False


class CustomerDepositSummaryResponse(BaseModel):
    """Response for customer deposits summary."""
    success: bool = True
    data: Dict[str, Any]
