"""
Pydantic schemas for Customers module.

This module defines request and response models for the /api/customers endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
import re


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateCustomerRequest(BaseModel):
    """Request body for creating a customer."""
    code: Optional[str] = Field(None, max_length=50, description="Customer code (e.g., CUST-001)")
    name: str = Field(..., min_length=1, max_length=255, description="Customer name (required)")
    contact_person: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    tax_id: Optional[str] = Field(None, max_length=50, description="NPWP")
    payment_terms_days: int = Field(0, ge=0, le=365, description="Default payment terms in days (0 = cash)")
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
            raise ValueError('Customer name is required')
        return v.strip()


class UpdateCustomerRequest(BaseModel):
    """Request body for updating a customer (partial update)."""
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

class CustomerListItem(BaseModel):
    """Customer item for list responses."""
    id: str
    code: Optional[str] = None
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    payment_terms_days: int
    is_active: bool
    created_at: str


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
