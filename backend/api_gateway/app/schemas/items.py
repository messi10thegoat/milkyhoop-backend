"""
Pydantic schemas for Items (Master Data) module.

This module defines request and response models for the /api/items endpoints.
Items are the master data for goods and services used in transactions.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from uuid import UUID
from datetime import datetime


# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_UNITS = [
    'Pcs', 'Box', 'Karton', 'Lusin', 'Pack', 'Strip', 'Tablet',
    'Kg', 'Gram', 'Liter', 'Ml', 'Dus', 'Unit', 'Set'
]

SALES_ACCOUNTS = [
    'Sales', 'General Income', 'Interest Income', 'Late Fee Income',
    'Other Charges', 'Discount', 'Shipping Charge'
]

PURCHASE_ACCOUNTS = [
    'Cost of Goods Sold', 'Advertising And Marketing', 'Automobile Expense',
    'Bank Fees and Charges', 'Consultant Expense', 'IT and Internet Expenses',
    'Office Supplies', 'Rent Expense', 'Salaries and Employee Wages',
    'Travel Expense'
]

TAX_CODES = ['', 'PPN_11', 'PPN_12', 'PPH_23_2', 'PPH_23_15']


# =============================================================================
# REQUEST MODELS - Unit Conversion
# =============================================================================

class UnitConversionInput(BaseModel):
    """Input for a unit conversion entry."""
    conversion_unit: str = Field(..., min_length=1, max_length=50, description="Larger unit: dus, karton, pack")
    conversion_factor: int = Field(..., gt=0, le=10000, description="How many base units per conversion unit")
    purchase_price: Optional[float] = Field(None, ge=0, description="Purchase price for this unit")
    sales_price: Optional[float] = Field(None, ge=0, description="Sales price for this unit")


class UnitConversionUpdate(BaseModel):
    """Input for updating a unit conversion entry."""
    id: Optional[UUID] = None  # If provided, update existing; otherwise create new
    conversion_unit: str = Field(..., min_length=1, max_length=50)
    conversion_factor: int = Field(..., gt=0, le=10000)
    purchase_price: Optional[float] = Field(None, ge=0)
    sales_price: Optional[float] = Field(None, ge=0)
    is_active: bool = True


# =============================================================================
# REQUEST MODELS - Create/Update Item
# =============================================================================

class CreateItemRequest(BaseModel):
    """Request body for creating a new item."""
    name: str = Field(..., min_length=1, max_length=100, description="Item name")
    item_type: Literal['goods', 'service'] = Field('goods', description="Type of item")
    track_inventory: bool = Field(True, description="Whether to track stock levels (goods only)")
    base_unit: str = Field(..., min_length=1, max_length=50, description="Base unit of measure")
    barcode: Optional[str] = Field(None, max_length=100, description="Product barcode")
    kategori: Optional[str] = Field(None, max_length=100, description="Category")
    deskripsi: Optional[str] = Field(None, description="Description")
    is_returnable: bool = Field(True, description="Whether item can be returned (goods only)")

    # Accounts
    sales_account: str = Field('Sales', max_length=100, description="Default sales account")
    purchase_account: str = Field('Cost of Goods Sold', max_length=100, description="Default purchase account")
    sales_tax: Optional[str] = Field(None, max_length=50, description="Default sales tax code")
    purchase_tax: Optional[str] = Field(None, max_length=50, description="Default purchase tax code")

    # Base pricing
    sales_price: Optional[float] = Field(None, ge=0, description="Default sales price (base unit)")
    purchase_price: Optional[float] = Field(None, ge=0, description="Default purchase price (base unit)")

    # Unit conversions (goods only)
    conversions: List[UnitConversionInput] = Field(default_factory=list, description="Unit conversions")

    @field_validator('track_inventory')
    @classmethod
    def validate_track_inventory(cls, v, info):
        """Services cannot track inventory."""
        if info.data.get('item_type') == 'service' and v:
            return False
        return v

    @field_validator('is_returnable')
    @classmethod
    def validate_is_returnable(cls, v, info):
        """Services cannot be returned."""
        if info.data.get('item_type') == 'service' and v:
            return False
        return v

    @field_validator('conversions')
    @classmethod
    def validate_conversions(cls, v, info):
        """Services cannot have unit conversions."""
        if info.data.get('item_type') == 'service' and v:
            return []
        return v


class UpdateItemRequest(BaseModel):
    """Request body for updating an existing item."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    item_type: Optional[Literal['goods', 'service']] = None
    track_inventory: Optional[bool] = None
    base_unit: Optional[str] = Field(None, min_length=1, max_length=50)
    barcode: Optional[str] = Field(None, max_length=100)
    kategori: Optional[str] = Field(None, max_length=100)
    deskripsi: Optional[str] = None
    is_returnable: Optional[bool] = None

    # Accounts
    sales_account: Optional[str] = Field(None, max_length=100)
    purchase_account: Optional[str] = Field(None, max_length=100)
    sales_tax: Optional[str] = Field(None, max_length=50)
    purchase_tax: Optional[str] = Field(None, max_length=50)

    # Base pricing
    sales_price: Optional[float] = Field(None, ge=0)
    purchase_price: Optional[float] = Field(None, ge=0)

    # Unit conversions (replace all)
    conversions: Optional[List[UnitConversionUpdate]] = None


# =============================================================================
# REQUEST MODELS - Units
# =============================================================================

class CreateUnitRequest(BaseModel):
    """Request body for creating a custom unit."""
    name: str = Field(..., min_length=1, max_length=50, description="Unit name")


# =============================================================================
# RESPONSE MODELS - Unit Conversion
# =============================================================================

class UnitConversionResponse(BaseModel):
    """Unit conversion in item response."""
    id: UUID
    base_unit: str
    conversion_unit: str
    conversion_factor: int
    purchase_price: Optional[float] = None
    sales_price: Optional[float] = None
    is_active: bool = True


# =============================================================================
# RESPONSE MODELS - Item
# =============================================================================

class ItemListItem(BaseModel):
    """Item for list responses."""
    id: UUID
    name: str
    item_type: str
    track_inventory: bool
    base_unit: str
    barcode: Optional[str] = None
    kategori: Optional[str] = None
    is_returnable: bool
    sales_price: Optional[float] = None
    purchase_price: Optional[float] = None
    # Stock info (only for track_inventory=true)
    current_stock: Optional[float] = None
    stock_value: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class ItemListResponse(BaseModel):
    """Response for list items endpoint."""
    success: bool = True
    items: List[ItemListItem]
    total: int
    has_more: bool


class ItemDetailResponse(BaseModel):
    """Response for get item detail endpoint."""
    success: bool = True
    data: Dict[str, Any]


class CreateItemResponse(BaseModel):
    """Response for create item endpoint."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class UpdateItemResponse(BaseModel):
    """Response for update item endpoint."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class DeleteItemResponse(BaseModel):
    """Response for delete item endpoint."""
    success: bool
    message: str


# =============================================================================
# RESPONSE MODELS - Units
# =============================================================================

class UnitListResponse(BaseModel):
    """Response for list units endpoint."""
    success: bool = True
    default_units: List[str] = Field(default_factory=lambda: DEFAULT_UNITS)
    custom_units: List[str] = Field(default_factory=list)


class CreateUnitResponse(BaseModel):
    """Response for create unit endpoint."""
    success: bool
    message: str
    unit: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Accounts & Tax
# =============================================================================

class AccountOption(BaseModel):
    """Account option for dropdowns."""
    value: str
    label: str
    type: str  # 'income', 'expense', 'cogs'


class TaxOption(BaseModel):
    """Tax option for dropdowns."""
    value: str
    label: str
    rate: float  # percentage


class AccountsResponse(BaseModel):
    """Response for list accounts endpoint."""
    success: bool = True
    sales_accounts: List[AccountOption]
    purchase_accounts: List[AccountOption]


class TaxOptionsResponse(BaseModel):
    """Response for list tax options endpoint."""
    success: bool = True
    goods_taxes: List[TaxOption]
    service_taxes: List[TaxOption]


# =============================================================================
# RESPONSE MODELS - Summary
# =============================================================================

class ItemsSummaryResponse(BaseModel):
    """Response for items summary endpoint."""
    success: bool = True
    data: Dict[str, Any]
