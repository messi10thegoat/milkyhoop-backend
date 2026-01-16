"""
Pydantic schemas for Bank Transfers module.

Bank Transfers handle inter-bank transfers with optional transfer fees.

Flow:
1. Create draft transfer
2. Post to accounting (creates journal entry + bank transactions)
3. Void if needed (creates reversal journal)

Journal Entry on POST:
    Dr. Bank Tujuan (to_bank coa)         amount
    Dr. Biaya Transfer (fee_account)      fee_amount (if any)
        Cr. Bank Asal (from_bank coa)         amount + fee_amount
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateBankTransferRequest(BaseModel):
    """Request body for creating a bank transfer."""
    from_bank_id: str = Field(..., description="Source bank account UUID")
    to_bank_id: str = Field(..., description="Destination bank account UUID")
    amount: int = Field(..., gt=0, description="Transfer amount in IDR")
    fee_amount: int = Field(0, ge=0, description="Bank transfer fee in IDR")
    transfer_date: date = Field(..., description="Date of transfer")
    ref_no: Optional[str] = Field(None, max_length=100, description="External reference number")
    notes: Optional[str] = None
    auto_post: bool = Field(False, description="Automatically post after creation")

    @model_validator(mode='after')
    def validate_different_banks(self):
        if self.from_bank_id == self.to_bank_id:
            raise ValueError('Source and destination bank accounts must be different')
        return self


class UpdateBankTransferRequest(BaseModel):
    """Request body for updating a draft bank transfer."""
    from_bank_id: Optional[str] = None
    to_bank_id: Optional[str] = None
    amount: Optional[int] = Field(None, gt=0)
    fee_amount: Optional[int] = Field(None, ge=0)
    transfer_date: Optional[date] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode='after')
    def validate_different_banks(self):
        if self.from_bank_id and self.to_bank_id and self.from_bank_id == self.to_bank_id:
            raise ValueError('Source and destination bank accounts must be different')
        return self


class VoidBankTransferRequest(BaseModel):
    """Request body for voiding a bank transfer."""
    reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class BankInfo(BaseModel):
    """Bank account info in transfer responses."""
    id: str
    account_name: str
    account_number: Optional[str] = None
    bank_name: Optional[str] = None
    current_balance: int


class BankTransferListItem(BaseModel):
    """Bank transfer item for list responses."""
    id: str
    transfer_number: str
    from_bank_id: str
    from_bank_name: str
    to_bank_id: str
    to_bank_name: str
    amount: int
    fee_amount: int
    total_amount: int
    transfer_date: str
    status: str
    ref_no: Optional[str] = None
    created_at: str


class BankTransferDetail(BaseModel):
    """Full bank transfer detail."""
    id: str
    transfer_number: str

    # Banks
    from_bank: BankInfo
    to_bank: BankInfo

    # Amounts
    amount: int
    fee_amount: int
    total_amount: int

    # Fee account
    fee_account_id: Optional[str] = None
    fee_account_code: Optional[str] = None
    fee_account_name: Optional[str] = None

    # Status
    status: str
    transfer_date: str

    # Reference
    ref_no: Optional[str] = None
    notes: Optional[str] = None

    # Accounting
    journal_id: Optional[str] = None
    journal_number: Optional[str] = None
    from_transaction_id: Optional[str] = None
    to_transaction_id: Optional[str] = None

    # Status tracking
    posted_at: Optional[str] = None
    posted_by: Optional[str] = None
    voided_at: Optional[str] = None
    voided_reason: Optional[str] = None

    # Audit
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class BankTransferResponse(BaseModel):
    """Generic bank transfer operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class BankTransferDetailResponse(BaseModel):
    """Response for get bank transfer detail."""
    success: bool = True
    data: BankTransferDetail


class BankTransferListResponse(BaseModel):
    """Response for list bank transfers."""
    items: List[BankTransferListItem]
    total: int
    has_more: bool = False


class BankTransferSummaryResponse(BaseModel):
    """Response for bank transfers summary."""
    success: bool = True
    data: Dict[str, Any]
