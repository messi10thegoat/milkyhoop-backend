"""
Pydantic schemas for KasBank V2 module.

KasBank V2 provides a unified interface for:
- Listing bank accounts with journal-derived balances
- Managing manual bank transactions (deposit/withdrawal) with DRAFT -> POST -> VOID flow
- Managing bank transfers with DRAFT -> POST -> VOID flow

All amounts are stored as bigint (Rupiah, no decimal places).
"""

from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Literal
from datetime import date, datetime


# === REQUEST MODELS ===

class CreateManualTransactionRequest(BaseModel):
    """Request body for creating a manual bank transaction (deposit or withdrawal)."""
    transaction_type: Literal[
        'other_income', 'interest_income', 'owner_contribution',
        'bank_admin_fee', 'owner_drawing', 'card_payment', 'expense'
    ] = Field(..., description="Type of manual transaction")
    amount: int = Field(..., gt=0, description="Transaction amount in IDR (positive)")
    transaction_date: date = Field(..., description="Date of transaction")
    description: str = Field(..., min_length=1, max_length=500, description="Transaction description")
    contra_account_id: Optional[str] = Field(None, description="Contra CoA account UUID. Auto-resolved for fixed types if omitted.")
    contact_name: Optional[str] = Field(None, max_length=255, description="Payee/payer name")
    # Atomic post: skip DRAFT, create POSTED transaction with journal in 1 call
    auto_post: bool = Field(False, description="If true, create and post atomically (no DRAFT step)")
    recon_session_id: Optional[str] = Field(None, description="Reconciliation session ID for auto-match")
    statement_line_id: Optional[str] = Field(None, description="Statement line ID for auto-match")


class VoidTransactionRequest(BaseModel):
    """Request body for voiding a posted bank transaction."""
    reason: str = Field(..., min_length=3, max_length=500, description="Reason for voiding")


class CreateTransferRequest(BaseModel):
    """Request body for creating a bank transfer."""
    from_bank_account_id: str = Field(..., description="Source bank account UUID")
    to_bank_account_id: str = Field(..., description="Destination bank account UUID")
    amount: int = Field(..., gt=0, description="Transfer amount in IDR")
    fee_amount: Optional[int] = Field(default=0, ge=0, description="Transfer fee in IDR")
    fee_account_id: Optional[str] = Field(None, description="Fee expense CoA account UUID")
    transfer_date: date = Field(..., description="Date of transfer")
    description: Optional[str] = Field(None, max_length=500, description="Transfer notes")

    @model_validator(mode='after')
    def validate_different_banks(self):
        if self.from_bank_account_id == self.to_bank_account_id:
            raise ValueError('Source and destination bank accounts must be different')
        return self

    @model_validator(mode='after')
    def validate_fee_account(self):
        if self.fee_amount and self.fee_amount > 0 and not self.fee_account_id:
            raise ValueError('fee_account_id is required when fee_amount > 0')
        return self


class VoidTransferRequest(BaseModel):
    """Request body for voiding a posted bank transfer."""
    reason: str = Field(..., min_length=3, max_length=500, description="Reason for voiding")


# === RESPONSE MODELS ===

class BankAccountListItem(BaseModel):
    """Bank account summary for list endpoint."""
    id: str
    account_name: str
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    account_type: str
    currency: str
    is_active: bool
    current_balance: int  # journal-derived balance


class BankAccountDetailResponse(BaseModel):
    """Bank account detail with extra fields."""
    id: str
    account_name: str
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    account_type: str
    currency: str
    is_active: bool
    current_balance: int
    opening_balance: int
    coa_id: str


class BankTransactionItem(BaseModel):
    """Bank transaction item for list/detail responses."""
    id: str
    transaction_number: Optional[str] = None
    transaction_date: str
    transaction_type: str
    amount: int
    running_balance: int
    description: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    reference_number: Optional[str] = None
    payee_payer: Optional[str] = None
    status: str
    origin_type: str
    source_module: Optional[str] = None
    is_reconciled: bool
    reconciliation_status: str
    journal_id: Optional[str] = None
    posted_at: Optional[str] = None
    voided_at: Optional[str] = None
    void_reason: Optional[str] = None
    created_at: str


class TransactionListResponse(BaseModel):
    """Paginated list of bank transactions."""
    items: List[BankTransactionItem]
    total: int
    page: int
    per_page: int
    total_pages: int


class TransferItem(BaseModel):
    """Bank transfer item for list/detail responses."""
    id: str
    transfer_number: str
    from_bank_account_id: str
    from_bank_name: Optional[str] = None
    to_bank_account_id: str
    to_bank_name: Optional[str] = None
    amount: int
    fee_amount: int
    total_amount: int
    transfer_date: str
    description: Optional[str] = None
    status: str
    journal_id: Optional[str] = None
    posted_at: Optional[str] = None
    voided_at: Optional[str] = None
    voided_reason: Optional[str] = None
    created_at: str


class GenericResponse(BaseModel):
    """Generic success/error response."""
    success: bool = True
    message: str = ""
    data: Optional[dict] = None
