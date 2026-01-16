"""
Warehouses Schemas
==================
Pydantic models for multi-warehouse/location management.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# REQUEST MODELS
# ============================================================================

class CreateWarehouseRequest(BaseModel):
    """Create a new warehouse/location"""
    code: str = Field(..., min_length=1, max_length=50, description="Unique warehouse code")
    name: str = Field(..., min_length=1, max_length=100, description="Warehouse name")

    # Address
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    country: str = "Indonesia"

    # Contact
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=100)
    manager_name: Optional[str] = Field(None, max_length=100)

    # Settings
    is_default: bool = False
    is_active: bool = True
    is_branch: bool = False
    branch_code: Optional[str] = Field(None, max_length=50)


class UpdateWarehouseRequest(BaseModel):
    """Update warehouse details"""
    code: Optional[str] = Field(None, min_length=1, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    manager_name: Optional[str] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    is_branch: Optional[bool] = None
    branch_code: Optional[str] = None


class UpdateWarehouseStockRequest(BaseModel):
    """Update reorder settings for item in warehouse"""
    reorder_level: Optional[Decimal] = None
    reorder_quantity: Optional[Decimal] = None
    min_stock: Optional[Decimal] = None
    max_stock: Optional[Decimal] = None


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class WarehouseData(BaseModel):
    """Warehouse details"""
    id: UUID
    code: str
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    manager_name: Optional[str] = None
    is_default: bool
    is_active: bool
    is_branch: bool
    branch_code: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class WarehouseStockItem(BaseModel):
    """Stock item in a warehouse"""
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal
    unit: Optional[str] = None
    unit_cost: Optional[int] = None
    total_value: Optional[int] = None
    reorder_level: Optional[Decimal] = None
    reorder_quantity: Optional[Decimal] = None
    last_stock_date: Optional[datetime] = None


class WarehouseStockResponse(BaseModel):
    """Response for warehouse stock list"""
    success: bool = True
    data: List[WarehouseStockItem]
    total: int
    warehouse_id: UUID
    warehouse_name: str


class WarehouseStockValueResponse(BaseModel):
    """Total stock value in warehouse"""
    success: bool = True
    warehouse_id: UUID
    warehouse_name: str
    total_items: int
    total_quantity: Decimal
    total_value: int


class ItemStockByWarehouse(BaseModel):
    """Item stock per warehouse"""
    warehouse_id: UUID
    warehouse_code: str
    warehouse_name: str
    quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal


class ItemStockByWarehouseResponse(BaseModel):
    """Response for item stock across warehouses"""
    success: bool = True
    item_id: UUID
    item_name: str
    total_quantity: Decimal
    warehouses: List[ItemStockByWarehouse]


class LowStockItem(BaseModel):
    """Item below reorder level"""
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    warehouse_id: UUID
    warehouse_name: str
    quantity: Decimal
    reorder_level: Decimal
    shortage: Decimal  # reorder_level - quantity


class LowStockResponse(BaseModel):
    """Response for low stock items"""
    success: bool = True
    data: List[LowStockItem]
    total: int


class WarehouseListResponse(BaseModel):
    """Response for warehouse list"""
    success: bool = True
    data: List[WarehouseData]
    total: int


class WarehouseDetailResponse(BaseModel):
    """Response for single warehouse"""
    success: bool = True
    data: WarehouseData


class CreateWarehouseResponse(BaseModel):
    """Response for warehouse creation"""
    success: bool = True
    data: WarehouseData
    message: str = "Warehouse created successfully"


class UpdateWarehouseResponse(BaseModel):
    """Response for warehouse update"""
    success: bool = True
    data: WarehouseData
    message: str = "Warehouse updated successfully"


class DeleteWarehouseResponse(BaseModel):
    """Response for warehouse deletion (soft delete)"""
    success: bool = True
    message: str = "Warehouse deactivated successfully"
