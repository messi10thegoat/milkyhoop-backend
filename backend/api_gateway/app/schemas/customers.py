"""
Pydantic schemas for Customers module.

This module defines request and response models for the /api/customers endpoints.
Following QB/Xero/Zoho patterns for customer master data.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
import re


# =============================================================================
# REQUEST MODELS
# =============================================================================


class CreateCustomerRequest(BaseModel):
    """Request body for creating a customer."""

    # Basic info
    code: Optional[str] = Field(
        None, max_length=50, description="Customer code (e.g., CUST-001)"
    )
    name: str = Field(
        ..., min_length=1, max_length=255, description="Customer name (required)"
    )
    company_name: Optional[str] = Field(None, max_length=255, description="Company/business name")
    display_name: Optional[str] = Field(None, max_length=255, description="Display name for invoices")
    
    # Contact info
    contact_person: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    mobile_phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    website: Optional[str] = Field(None, max_length=255)
    
    # Address
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    
    # Tax info (Indonesian compliance)
    tax_id: Optional[str] = Field(None, max_length=50, description="NPWP")
    nik: Optional[str] = Field(None, max_length=20, description="NIK for ORANG_PRIBADI")
    is_pkp: bool = Field(False, description="Pengusaha Kena Pajak status")
    customer_type: Optional[Literal["BADAN", "ORANG_PRIBADI", "LUAR_NEGERI"]] = Field(
        "BADAN", description="Business type for tax purposes"
    )
    
    # Financial
    currency: Optional[str] = Field("IDR", max_length=3, description="Default currency")
    payment_terms_days: int = Field(
        0, ge=0, le=365, description="Default payment terms in days (0 = cash)"
    )
    credit_limit: Optional[int] = Field(None, ge=0, description="Credit limit in Rupiah")
    
    # Opening balance (AR)
    ar_opening_balance: Optional[int] = Field(
        None, ge=0, description="Opening AR balance in Rupiah"
    )
    opening_balance_date: Optional[str] = Field(
        None, description="Opening balance date YYYY-MM-DD"
    )
    opening_balance_notes: Optional[str] = None
    
    # Other
    notes: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError("Customer name is required")
        return v.strip()


class UpdateCustomerRequest(BaseModel):
    """Request body for updating a customer (partial update)."""

    # Basic info
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    company_name: Optional[str] = None
    display_name: Optional[str] = None
    
    # Contact info
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    
    # Address
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    
    # Tax info
    tax_id: Optional[str] = None
    nik: Optional[str] = None
    is_pkp: Optional[bool] = None
    customer_type: Optional[Literal["BADAN", "ORANG_PRIBADI", "LUAR_NEGERI"]] = None
    
    # Financial
    currency: Optional[str] = None
    payment_terms_days: Optional[int] = Field(None, ge=0, le=365)
    credit_limit: Optional[int] = None
    
    # Opening balance
    ar_opening_balance: Optional[int] = None
    opening_balance_date: Optional[str] = None
    opening_balance_notes: Optional[str] = None
    
    # Other
    notes: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        return v


# =============================================================================
# RESPONSE MODELS - List Item
# =============================================================================


class CustomerListItem(BaseModel):
    """Customer item for list responses."""

    id: str
    code: Optional[str] = None
    name: str
    company_name: Optional[str] = None
    customer_type: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    tax_id: Optional[str] = None
    is_pkp: bool = False
    payment_terms_days: int = 0
    credit_limit: Optional[int] = None
    points: Optional[int] = 0
    total_transactions: Optional[int] = 0
    total_value: Optional[int] = 0
    outstanding_balance: Optional[int] = 0
    is_active: bool = True
    created_at: Optional[str] = None


class CustomerListResponse(BaseModel):
    """Response for list customers endpoint."""

    items: List[CustomerListItem]
    total: int
    has_more: bool


# =============================================================================
# RESPONSE MODELS - Detail
# =============================================================================


class CustomerDetail(BaseModel):
    """Full customer detail."""

    id: str
    code: Optional[str] = None
    name: str
    company_name: Optional[str] = None
    display_name: Optional[str] = None
    
    # Contact
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    mobile_phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    
    # Address
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    
    # Tax info
    tax_id: Optional[str] = None
    nik: Optional[str] = None
    is_pkp: bool = False
    customer_type: Optional[str] = None
    
    # Financial
    currency: Optional[str] = "IDR"
    payment_terms_days: int = 0
    credit_limit: Optional[int] = None
    
    # Opening balance
    ar_opening_balance: Optional[int] = 0
    opening_balance_date: Optional[str] = None
    opening_balance_notes: Optional[str] = None
    
    # Statistics
    points: Optional[int] = 0
    points_per_50k: Optional[int] = 0
    total_transactions: Optional[int] = 0
    total_value: Optional[int] = 0
    outstanding_balance: Optional[int] = 0
    last_transaction_at: Optional[str] = None
    
    # Metadata
    default_currency_id: Optional[str] = None
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    notes: Optional[str] = None


class CustomerDetailResponse(BaseModel):
    """Response for get customer detail endpoint."""

    success: bool = True
    data: CustomerDetail


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================


class CustomerResponse(BaseModel):
    """Generic customer operation response (create, update, delete)."""

    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# AUTOCOMPLETE RESPONSE
# =============================================================================


class CustomerAutocompleteItem(BaseModel):
    """Customer item for autocomplete responses."""

    id: str
    name: str
    code: Optional[str] = None
    phone: Optional[str] = None
    company_name: Optional[str] = None


class CustomerAutocompleteResponse(BaseModel):
    """Response for customer autocomplete endpoint."""

    items: List[CustomerAutocompleteItem]


# =============================================================================
# BALANCE RESPONSE (AR Integration)
# =============================================================================


class CustomerBalanceResponse(BaseModel):
    """Response for customer balance endpoint (AR balance)."""

    success: bool = True
    data: Dict[str, Any]


# =============================================================================
# DUPLICATE CHECK RESPONSE
# =============================================================================


class CustomerDuplicateItem(BaseModel):
    """Customer match item for duplicate check."""

    id: str
    name: str
    company: Optional[str] = None
    npwp: Optional[str] = None


class CustomerDuplicateCheckResponse(BaseModel):
    """Response for customer duplicate check endpoint."""

    byName: List[CustomerDuplicateItem] = Field(default_factory=list)
    byNpwp: List[CustomerDuplicateItem] = Field(default_factory=list)
