"""
Pydantic schemas for Price Lists module.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import date


class PriceListItemCreate(BaseModel):
    item_id: str
    item_code: Optional[str] = None
    unit: Optional[str] = Field(None, max_length=20)
    price: int = Field(..., ge=0)
    min_quantity: float = Field(1, ge=0)
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class PriceListItemResponse(BaseModel):
    id: str
    item_id: str
    item_code: Optional[str] = None
    unit: Optional[str] = None
    price: int
    min_quantity: float = 1
    discount_percent: Optional[float] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: bool = True


class CreatePriceListRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    price_type: Literal["fixed", "discount_percent", "markup_percent"] = "fixed"
    default_discount: float = Field(0, ge=0, le=100)
    default_markup: float = Field(0, ge=0, le=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    priority: int = Field(100, ge=1)
    description: Optional[str] = None
    is_default: bool = False
    items: Optional[List[PriceListItemCreate]] = None


class UpdatePriceListRequest(BaseModel):
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, max_length=255)
    price_type: Optional[Literal["fixed", "discount_percent", "markup_percent"]] = None
    default_discount: Optional[float] = Field(None, ge=0, le=100)
    default_markup: Optional[float] = Field(None, ge=0, le=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    priority: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class PriceListListItem(BaseModel):
    id: str
    code: str
    name: str
    price_type: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: bool
    is_default: bool
    item_count: int = 0


class PriceListListResponse(BaseModel):
    items: List[PriceListListItem]
    total: int
    has_more: bool


class PriceListDetail(BaseModel):
    id: str
    code: str
    name: str
    price_type: str
    default_discount: float = 0
    default_markup: float = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    priority: int = 100
    description: Optional[str] = None
    is_active: bool
    is_default: bool
    items: List[PriceListItemResponse] = []
    created_at: str
    updated_at: str


class PriceListDetailResponse(BaseModel):
    success: bool = True
    data: PriceListDetail


class PriceListResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class PriceListDropdownItem(BaseModel):
    id: str
    code: str
    name: str
    is_default: bool


class PriceListDropdownResponse(BaseModel):
    items: List[PriceListDropdownItem]


class ItemPriceRequest(BaseModel):
    item_id: str
    customer_id: Optional[str] = None
    quantity: float = 1
    unit: Optional[str] = None


class ItemPriceResponse(BaseModel):
    success: bool = True
    data: Dict[str, Any]
