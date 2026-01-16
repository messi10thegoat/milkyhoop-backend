"""
Pydantic schemas for Bank Accounts module.

Bank Accounts are linked to Chart of Accounts (CoA) for proper accounting integration.
Each bank account tracks its own balance and transaction history.

Flow:
- Create bank account (linked to CoA ASSET account)
- Record transactions (via payments, transfers, adjustments)
- Track running balance
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date
from uuid import UUID


# =============================================================================
# REQUEST MODELS - Bank Account
# =============================================================================

class CreateBankAccountRequest(BaseModel):
    """Request body for creating a bank account."""
    account_name: str = Field(..., min_length=1, max_length=100, description="Display name e.g., 'BCA Utama'")
    account_number: Optional[str] = Field(None, max_length=50, description="Bank account number")
    bank_name: Optional[str] = Field(None, max_length=100, description="Bank name e.g., 'Bank BCA'")
    bank_branch: Optional[str] = Field(None, max_length=100, description="Branch name")
    swift_code: Optional[str] = Field(None, max_length=20, description="SWIFT/BIC code")
    coa_id: str = Field(..., description="Chart of Accounts UUID (must be ASSET type)")
    opening_balance: int = Field(0, ge=0, description="Initial balance in IDR")
    opening_date: Optional[date] = Field(None, description="Opening balance date (default: today)")
    account_type: Literal["bank", "cash", "petty_cash", "e_wallet"] = Field("bank")
    currency: str = Field("IDR", max_length=3)
    is_default: bool = Field(False, description="Set as default bank account")
    notes: Optional[str] = None

    @field_validator('account_name')
    @classmethod
    def validate_account_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Account name is required')
        return v.strip()


class UpdateBankAccountRequest(BaseModel):
    """Request body for updating a bank account."""
    account_name: Optional[str] = Field(None, max_length=100)
    account_number: Optional[str] = Field(None, max_length=50)
    bank_name: Optional[str] = Field(None, max_length=100)
    bank_branch: Optional[str] = Field(None, max_length=100)
    swift_code: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    notes: Optional[str] = None


class AdjustBalanceRequest(BaseModel):
    """Request body for manual balance adjustment."""
    adjustment_date: date = Field(..., description="Date of adjustment")
    adjustment_amount: int = Field(..., description="Adjustment amount (positive=increase, negative=decrease)")
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for adjustment")


# =============================================================================
# RESPONSE MODELS - Bank Account
# =============================================================================

class BankAccountListItem(BaseModel):
    """Bank account item for list responses."""
    id: str
    account_name: str
    account_number: Optional[str] = None
    bank_name: Optional[str] = None
    account_type: str
    coa_id: str
    coa_code: Optional[str] = None
    coa_name: Optional[str] = None
    current_balance: int
    is_active: bool
    is_default: bool
    created_at: str


class BankAccountDetail(BaseModel):
    """Full bank account detail."""
    id: str
    account_name: str
    account_number: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    swift_code: Optional[str] = None
    account_type: str
    currency: str

    # CoA link
    coa_id: str
    coa_code: Optional[str] = None
    coa_name: Optional[str] = None

    # Balances
    opening_balance: int
    current_balance: int
    last_reconciled_balance: int = 0
    last_reconciled_date: Optional[str] = None

    # Status
    is_active: bool
    is_default: bool

    # Metadata
    notes: Optional[str] = None

    # Audit
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


class BankTransactionListItem(BaseModel):
    """Bank transaction item for list responses."""
    id: str
    transaction_date: str
    transaction_type: str
    amount: int
    running_balance: int
    description: Optional[str] = None
    payee_payer: Optional[str] = None
    reference_type: Optional[str] = None
    reference_number: Optional[str] = None
    is_reconciled: bool
    created_at: str


class BankAccountBalanceInfo(BaseModel):
    """Balance information for a bank account."""
    id: str
    account_name: str
    opening_balance: int
    current_balance: int
    total_deposits: int
    total_withdrawals: int
    transaction_count: int
    unreconciled_count: int
    last_transaction_date: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class BankAccountResponse(BaseModel):
    """Generic bank account operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class BankAccountDetailResponse(BaseModel):
    """Response for get bank account detail."""
    success: bool = True
    data: BankAccountDetail


class BankAccountListResponse(BaseModel):
    """Response for list bank accounts."""
    items: List[BankAccountListItem]
    total: int
    has_more: bool = False


class BankTransactionListResponse(BaseModel):
    """Response for list bank transactions."""
    items: List[BankTransactionListItem]
    total: int
    has_more: bool = False


class BankAccountBalanceResponse(BaseModel):
    """Response for bank account balance."""
    success: bool = True
    data: BankAccountBalanceInfo
