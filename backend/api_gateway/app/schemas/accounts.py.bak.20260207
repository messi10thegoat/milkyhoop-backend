"""
Pydantic schemas for Chart of Accounts (CoA) module.

This module defines request and response models for the /api/accounts endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal


# =============================================================================
# CONSTANTS
# =============================================================================

ACCOUNT_TYPES = ["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"]
NORMAL_BALANCES = ["DEBIT", "CREDIT"]


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateAccountRequest(BaseModel):
    """Request body for creating an account."""
    code: str = Field(..., min_length=1, max_length=20, description="Account code (e.g., 1-10100)")
    name: str = Field(..., min_length=1, max_length=100, description="Account name")
    type: Literal["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"] = Field(
        ..., description="Account type"
    )
    normal_balance: Literal["DEBIT", "CREDIT"] = Field(
        ..., description="Normal balance side"
    )
    parent_id: Optional[str] = Field(None, description="Parent account ID for hierarchy")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        if not v or not v.strip():
            raise ValueError('Account code is required')
        return v.strip()

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Account name is required')
        return v.strip()


class UpdateAccountRequest(BaseModel):
    """Request body for updating an account (partial update)."""
    code: Optional[str] = Field(None, max_length=20)
    name: Optional[str] = Field(None, max_length=100)
    parent_id: Optional[str] = None
    is_active: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


# =============================================================================
# RESPONSE MODELS - List Item
# =============================================================================

class AccountListItem(BaseModel):
    """Account item for list responses."""
    id: str
    code: str
    name: str
    type: str
    normal_balance: str
    parent_code: Optional[str] = None
    is_active: bool
    is_header: bool = False
    level: int = 0


class AccountListResponse(BaseModel):
    """Response for list accounts endpoint."""
    items: List[AccountListItem]
    total: int


# =============================================================================
# RESPONSE MODELS - Tree Item
# =============================================================================

class AccountTreeItem(BaseModel):
    """Account item with children for tree view."""
    id: str
    code: str
    name: str
    type: str
    normal_balance: str
    is_active: bool
    is_header: bool = False
    children: List["AccountTreeItem"] = []


class AccountTreeResponse(BaseModel):
    """Response for accounts tree endpoint."""
    items: List[AccountTreeItem]


# =============================================================================
# RESPONSE MODELS - Detail
# =============================================================================

class AccountDetail(BaseModel):
    """Full account detail."""
    id: str
    code: str
    name: str
    type: str
    normal_balance: str
    parent_code: Optional[str] = None
    parent_name: Optional[str] = None
    is_active: bool
    is_header: bool = False
    description: Optional[str] = None
    category: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AccountDetailResponse(BaseModel):
    """Response for get account detail endpoint."""
    success: bool = True
    data: AccountDetail


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class AccountResponse(BaseModel):
    """Generic account operation response (create, update, delete)."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# BALANCE RESPONSE
# =============================================================================

class AccountBalanceItem(BaseModel):
    """Account with balance information."""
    id: str
    code: str
    name: str
    type: str
    normal_balance: str
    debit_total: int
    credit_total: int
    balance: int


class AccountBalanceResponse(BaseModel):
    """Response for account balance endpoint."""
    success: bool = True
    data: AccountBalanceItem


# =============================================================================
# DROPDOWN RESPONSE
# =============================================================================

class AccountDropdownItem(BaseModel):
    """Account item for dropdown/select components."""
    id: str
    code: str
    name: str
    type: str
    full_name: str  # code + name combined


class AccountDropdownResponse(BaseModel):
    """Response for account dropdown endpoint."""
    items: List[AccountDropdownItem]
