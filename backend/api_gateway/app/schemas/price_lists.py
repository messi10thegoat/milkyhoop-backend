"""
Pydantic schemas for Price Lists module.
"""

from decimal import Decimal
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import date
from uuid import UUID

from .uang_json import UangJson


class PriceListItemCreate(BaseModel):
    item_id: str
    item_code: Optional[str] = None
    unit: Optional[str] = Field(None, max_length=20)  # kosong = satuan dasar barang
    price: Decimal = Field(..., ge=0, max_digits=18, decimal_places=2)  # DAFTAR-HARGA: 2 desimal (Law 9/25); dulu int
    min_quantity: Decimal = Field(Decimal("1"), gt=0)  # tingkat qty dalam SATUAN baris ini
    is_default_unit: bool = False  # satuan jual bawaan barang ini di daftar ini (opsional)
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class PriceListItemUpdate(BaseModel):
    """PATCH satu baris (ANDROID U3, 4 Nov 2026): medan yang dikirim menimpa baris tersimpan, lalu penulis YANG SAMA (tulis_baris_harga)."""
    unit: Optional[str] = Field(None, max_length=20)
    price: Optional[Decimal] = Field(None, ge=0, max_digits=18, decimal_places=2)
    min_quantity: Optional[Decimal] = Field(None, gt=0)
    is_default_unit: Optional[bool] = None
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class PriceListItemResponse(BaseModel):
    id: str
    item_id: str
    item_code: Optional[str] = None
    item_name: Optional[str] = None  # JOIN products (satu kueri; FE tak perlu GET /items)
    unit: Optional[str] = None
    is_default_unit: bool = False
    price: UangJson  # dulu int (memotong sen, Law 9)
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


# DAFTAR-HARGA (4 Nov 2026): penentu harga saran SATU di server (services/harga_jual)
class BarisHargaIn(BaseModel):
    key: Optional[str] = Field(None, max_length=100)
    item_id: UUID
    unit: Optional[str] = Field(None, max_length=50)
    quantity: Decimal = Field(Decimal("1"), gt=0)


class ResolveHargaRequest(BaseModel):
    customer_id: Optional[UUID] = None
    tanggal: Optional[date] = None  # kosong = tanggal bisnis tenant
    lines: List[BarisHargaIn] = Field(..., min_length=1, max_length=500)
