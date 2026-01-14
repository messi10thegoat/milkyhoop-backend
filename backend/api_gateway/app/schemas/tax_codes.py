"""
Pydantic schemas for Tax Codes module.

This module defines request and response models for the /api/tax-codes endpoints.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from decimal import Decimal


# =============================================================================
# CONSTANTS
# =============================================================================

TAX_TYPES = ["ppn", "pph21", "pph23", "pph4_2", "custom", "none"]


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateTaxCodeRequest(BaseModel):
    """Request body for creating a tax code."""
    code: str = Field(..., min_length=1, max_length=20, description="Tax code (e.g., PPN-11)")
    name: str = Field(..., min_length=1, max_length=100, description="Tax name (e.g., PPN 11%)")
    rate: float = Field(..., ge=0, le=100, description="Tax rate percentage")
    tax_type: Literal["ppn", "pph21", "pph23", "pph4_2", "custom", "none"] = Field(
        ..., description="Tax type"
    )
    is_inclusive: bool = Field(False, description="Tax included in price by default")
    sales_tax_account: Optional[str] = Field(None, max_length=20, description="CoA code for sales tax")
    purchase_tax_account: Optional[str] = Field(None, max_length=20, description="CoA code for purchase tax")
    description: Optional[str] = None
    is_default: bool = Field(False, description="Set as default tax for this type")

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        if not v or not v.strip():
            raise ValueError('Tax code is required')
        return v.strip().upper()


class UpdateTaxCodeRequest(BaseModel):
    """Request body for updating a tax code (partial update)."""
    code: Optional[str] = Field(None, max_length=20)
    name: Optional[str] = Field(None, max_length=100)
    rate: Optional[float] = Field(None, ge=0, le=100)
    tax_type: Optional[Literal["ppn", "pph21", "pph23", "pph4_2", "custom", "none"]] = None
    is_inclusive: Optional[bool] = None
    sales_tax_account: Optional[str] = None
    purchase_tax_account: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None

    @field_validator('code')
    @classmethod
    def validate_code(cls, v):
        if v is not None:
            return v.strip().upper()
        return v


# =============================================================================
# RESPONSE MODELS - List Item
# =============================================================================

class TaxCodeListItem(BaseModel):
    """Tax code item for list responses."""
    id: str
    code: str
    name: str
    rate: float
    tax_type: str
    is_inclusive: bool
    is_active: bool
    is_default: bool


class TaxCodeListResponse(BaseModel):
    """Response for list tax codes endpoint."""
    items: List[TaxCodeListItem]
    total: int
    has_more: bool


# =============================================================================
# RESPONSE MODELS - Detail
# =============================================================================

class TaxCodeDetail(BaseModel):
    """Full tax code detail."""
    id: str
    code: str
    name: str
    rate: float
    tax_type: str
    is_inclusive: bool
    sales_tax_account: Optional[str] = None
    purchase_tax_account: Optional[str] = None
    description: Optional[str] = None
    is_active: bool
    is_default: bool
    created_at: str
    updated_at: str


class TaxCodeDetailResponse(BaseModel):
    """Response for get tax code detail endpoint."""
    success: bool = True
    data: TaxCodeDetail


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class TaxCodeResponse(BaseModel):
    """Generic tax code operation response (create, update, delete)."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# DROPDOWN RESPONSE
# =============================================================================

class TaxCodeDropdownItem(BaseModel):
    """Tax code item for dropdown/select components."""
    id: str
    code: str
    name: str
    rate: float
    is_default: bool = False


class TaxCodeDropdownResponse(BaseModel):
    """Response for tax code dropdown endpoint."""
    items: List[TaxCodeDropdownItem]
