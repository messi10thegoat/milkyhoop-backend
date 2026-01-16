"""
Pydantic schemas for Cheque Management module (Manajemen Giro/Cek Mundur).

Manages post-dated cheques:
- Received from customers (reduces AR)
- Issued to vendors (reduces AP)
- Deposit to bank tracking
- Cleared/bounced status management
- Replacement cheque handling

HAS JOURNAL ENTRIES - See router for journal mappings.

Account Codes Used:
- 1-10600: Giro Diterima (Cheques Receivable - Asset)
- 2-10500: Giro Diberikan (Cheques Payable - Liability)
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date, datetime
from uuid import UUID


# =============================================================================
# REQUEST MODELS - Receive Cheque (from Customer)
# =============================================================================

class ReceiveChequeRequest(BaseModel):
    """Request to record a received cheque from customer."""
    cheque_number: str = Field(..., min_length=1, max_length=100)
    cheque_date: date = Field(..., description="Date on cheque (when it can be cashed)")
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_branch: Optional[str] = Field(None, max_length=100)
    amount: int = Field(..., gt=0, description="Cheque amount in IDR")
    customer_id: Optional[UUID] = Field(None, description="Customer UUID")
    party_name: str = Field(..., min_length=1, max_length=255, description="Name on cheque")
    reference_type: Optional[Literal["sales_invoice", "payment_receipt"]] = None
    reference_id: Optional[UUID] = None
    reference_number: Optional[str] = Field(None, max_length=100)
    received_date: date = Field(..., description="Date we received the cheque")
    notes: Optional[str] = None

    @field_validator('cheque_number')
    @classmethod
    def validate_cheque_number(cls, v):
        if not v or not v.strip():
            raise ValueError('Cheque number is required')
        return v.strip()


# =============================================================================
# REQUEST MODELS - Issue Cheque (to Vendor)
# =============================================================================

class IssueChequeRequest(BaseModel):
    """Request to record an issued cheque to vendor."""
    cheque_number: str = Field(..., min_length=1, max_length=100)
    cheque_date: date = Field(..., description="Date on cheque")
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_branch: Optional[str] = Field(None, max_length=100)
    amount: int = Field(..., gt=0, description="Cheque amount in IDR")
    vendor_id: UUID = Field(..., description="Vendor UUID")
    party_name: str = Field(..., min_length=1, max_length=255, description="Payee name on cheque")
    bank_account_id: UUID = Field(..., description="Our bank account the cheque is drawn from")
    reference_type: Optional[Literal["bill", "bill_payment"]] = None
    reference_id: Optional[UUID] = None
    reference_number: Optional[str] = Field(None, max_length=100)
    issued_date: date = Field(..., description="Date we issued the cheque")
    notes: Optional[str] = None


# =============================================================================
# REQUEST MODELS - Update Cheque
# =============================================================================

class UpdateChequeRequest(BaseModel):
    """Request to update a pending cheque."""
    cheque_date: Optional[date] = None
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_branch: Optional[str] = Field(None, max_length=100)
    party_name: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


# =============================================================================
# REQUEST MODELS - Status Changes
# =============================================================================

class DepositChequeRequest(BaseModel):
    """Request to deposit a cheque to bank."""
    bank_account_id: UUID = Field(..., description="Bank account to deposit into")
    deposited_date: date = Field(..., description="Date of deposit")
    notes: Optional[str] = None


class ClearChequeRequest(BaseModel):
    """Request to mark a cheque as cleared."""
    cleared_date: date = Field(..., description="Date cheque cleared")
    notes: Optional[str] = None


class BounceChequeRequest(BaseModel):
    """Request to mark a cheque as bounced."""
    bounced_date: date = Field(..., description="Date cheque bounced")
    bounce_reason: str = Field(..., min_length=1, max_length=500, description="Reason for bounce")
    bounce_charges: int = Field(0, ge=0, description="Bank charges for bounced cheque")
    notes: Optional[str] = None


class CancelChequeRequest(BaseModel):
    """Request to cancel a cheque."""
    reason: str = Field(..., min_length=1, max_length=500, description="Cancellation reason")


class ReplaceChequeRequest(BaseModel):
    """Request to replace a bounced cheque with a new one."""
    new_cheque_number: str = Field(..., min_length=1, max_length=100)
    new_cheque_date: date
    notes: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Cheque
# =============================================================================

class ChequeItem(BaseModel):
    """Cheque list item."""
    id: str
    cheque_number: str
    cheque_date: date
    bank_name: Optional[str] = None
    cheque_type: str  # received, issued
    amount: int
    party_name: Optional[str] = None
    status: str
    reference_number: Optional[str] = None
    days_until_due: Optional[int] = None


class ChequeDetail(BaseModel):
    """Detailed cheque information."""
    id: str
    cheque_number: str
    cheque_date: date
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    cheque_type: str

    amount: int
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = None
    party_name: Optional[str] = None

    bank_account_id: Optional[str] = None
    bank_account_name: Optional[str] = None

    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    reference_number: Optional[str] = None

    status: str

    # Dates
    received_date: Optional[date] = None
    issued_date: Optional[date] = None
    deposited_date: Optional[date] = None
    cleared_date: Optional[date] = None
    bounced_date: Optional[date] = None

    # Journal references
    receipt_journal_id: Optional[str] = None
    deposit_journal_id: Optional[str] = None
    clear_journal_id: Optional[str] = None
    bounce_journal_id: Optional[str] = None

    # Bounce handling
    replacement_cheque_id: Optional[str] = None
    bounce_charges: int = 0
    bounce_reason: Optional[str] = None

    notes: Optional[str] = None

    created_at: str
    updated_at: str
    created_by: Optional[str] = None

    # Status history
    history: Optional[List[Dict[str, Any]]] = None


class ChequeStatusHistoryItem(BaseModel):
    """Cheque status change history."""
    id: str
    old_status: Optional[str] = None
    new_status: str
    changed_at: datetime
    changed_by: Optional[str] = None
    notes: Optional[str] = None
    journal_id: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Summary & Reports
# =============================================================================

class ChequeSummaryItem(BaseModel):
    """Summary by cheque type and status."""
    cheque_type: str
    status: str
    count: int
    total_amount: int


class ChequeAgingItem(BaseModel):
    """Cheque aging bucket."""
    aging_bucket: str
    count: int
    total_amount: int


class ChequeSummary(BaseModel):
    """Overall cheque summary."""
    received_pending: int = 0
    received_pending_amount: int = 0
    received_deposited: int = 0
    received_deposited_amount: int = 0
    issued_pending: int = 0
    issued_pending_amount: int = 0
    bounced_count: int = 0
    bounced_amount: int = 0
    due_today_count: int = 0
    due_today_amount: int = 0


# =============================================================================
# GENERIC RESPONSE MODELS
# =============================================================================

class ChequeListResponse(BaseModel):
    """Response for listing cheques."""
    items: List[ChequeItem]
    total: int
    has_more: bool = False


class ChequeDetailResponse(BaseModel):
    """Response for cheque detail."""
    success: bool = True
    data: ChequeDetail


class ChequeSummaryResponse(BaseModel):
    """Response for cheque summary."""
    success: bool = True
    data: ChequeSummary


class ChequeAgingResponse(BaseModel):
    """Response for cheque aging report."""
    success: bool = True
    data: List[ChequeAgingItem]
    cheque_type: str


class ChequeActionResponse(BaseModel):
    """Response for cheque status change actions."""
    success: bool = True
    message: str
    data: Dict[str, Any]


class ChequeResponse(BaseModel):
    """Generic cheque operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
