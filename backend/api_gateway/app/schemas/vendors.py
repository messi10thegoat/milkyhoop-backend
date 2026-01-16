"""
Pydantic schemas for Vendors module.

This module defines request and response models for the /api/vendors endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
import re


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateVendorRequest(BaseModel):
    """Request body for creating a vendor."""
    code: Optional[str] = Field(None, max_length=50, description="Vendor code (e.g., PBF-001)")
    name: str = Field(..., min_length=1, max_length=255, description="Vendor name (required)")
    contact_person: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    tax_id: Optional[str] = Field(None, max_length=50, description="NPWP")
    payment_terms_days: int = Field(30, ge=0, le=365, description="Default payment terms in days")
    credit_limit: Optional[int] = Field(None, ge=0, description="Credit limit in Rupiah")
    notes: Optional[str] = None

    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        if v and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v):
            raise ValueError('Invalid email format')
        return v

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Vendor name is required')
        return v.strip()


class UpdateVendorRequest(BaseModel):
    """Request body for updating a vendor (partial update)."""
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    tax_id: Optional[str] = None
    payment_terms_days: Optional[int] = Field(None, ge=0, le=365)
    credit_limit: Optional[int] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator('email')
    @classmethod
    def validate_email(cls, v):
        if v and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v):
            raise ValueError('Invalid email format')
        return v


# =============================================================================
# RESPONSE MODELS - List Item
# =============================================================================

class VendorListItem(BaseModel):
    """Vendor item for list responses."""
    id: str
    code: Optional[str] = None
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    payment_terms_days: int
    is_active: bool
    created_at: str


class VendorListResponse(BaseModel):
    """Response for list vendors endpoint."""
    items: List[VendorListItem]
    total: int
    has_more: bool


# =============================================================================
# RESPONSE MODELS - Detail
# =============================================================================

class VendorDetail(BaseModel):
    """Full vendor detail."""
    id: str
    code: Optional[str] = None
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    tax_id: Optional[str] = None
    payment_terms_days: int
    credit_limit: Optional[int] = None
    notes: Optional[str] = None
    is_active: bool
    created_at: str
    updated_at: str


class VendorDetailResponse(BaseModel):
    """Response for get vendor detail endpoint."""
    success: bool = True
    data: VendorDetail


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class VendorResponse(BaseModel):
    """Generic vendor operation response (create, update, delete)."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# AUTOCOMPLETE RESPONSE
# =============================================================================

class VendorAutocompleteItem(BaseModel):
    """Vendor item for autocomplete responses."""
    id: str
    name: str
    code: Optional[str] = None
    phone: Optional[str] = None


class VendorAutocompleteResponse(BaseModel):
    """Response for vendor autocomplete endpoint."""
    items: List[VendorAutocompleteItem]


# =============================================================================
# BALANCE RESPONSE (AP Balance)
# =============================================================================

class VendorBalanceData(BaseModel):
    """Vendor AP balance data."""
    vendor_id: str
    vendor_name: str
    total_balance: int  # Outstanding amount in IDR
    unpaid_bills: int   # Count of unpaid bills
    partial_bills: int  # Count of partially paid bills
    overdue_bills: int  # Count of overdue bills
    overdue_amount: int  # Total overdue amount
    total_billed: int   # Total amount billed (all time)
    total_paid: int     # Total amount paid (all time)


class VendorBalanceResponse(BaseModel):
    """Response for vendor balance endpoint (AP balance)."""
    success: bool = True
    data: VendorBalanceData
