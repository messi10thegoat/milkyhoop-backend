"""
Item Batches Schemas
====================
Pydantic models for batch/lot tracking with expiry dates.
Default selection method: FEFO (First Expiry First Out).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# TYPES
# ============================================================================

BatchStatus = Literal["active", "expired", "depleted", "quarantine"]
SelectionMethod = Literal["FEFO", "FIFO"]  # Default FEFO


# ============================================================================
# REQUEST MODELS
# ============================================================================

class CreateItemBatchRequest(BaseModel):
    """Create a new batch/lot"""
    item_id: UUID
    batch_number: str = Field(..., min_length=1, max_length=100)
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    received_date: Optional[date] = None
    initial_quantity: Decimal = Field(..., gt=0)
    unit_cost: int = 0
    warehouse_id: Optional[UUID] = None  # Initial warehouse
    purchase_order_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    supplier_batch_number: Optional[str] = Field(None, max_length=100)
    quality_grade: Optional[str] = Field(None, max_length=50)
    quality_notes: Optional[str] = None


class UpdateItemBatchRequest(BaseModel):
    """Update batch details"""
    batch_number: Optional[str] = Field(None, min_length=1, max_length=100)
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    supplier_batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    quality_notes: Optional[str] = None
    status: Optional[BatchStatus] = None


class AdjustBatchQuantityRequest(BaseModel):
    """Adjust batch quantity in a warehouse"""
    warehouse_id: UUID
    quantity_change: Decimal  # Positive to add, negative to subtract
    reason: str = Field(..., min_length=1, max_length=500)


class GetAvailableBatchesRequest(BaseModel):
    """Request available batches for selection"""
    item_id: UUID
    warehouse_id: UUID
    quantity_needed: Decimal = Field(..., gt=0)
    method: SelectionMethod = "FEFO"


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class BatchWarehouseStock(BaseModel):
    """Batch quantity in a specific warehouse"""
    warehouse_id: UUID
    warehouse_name: str
    quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal


class ItemBatchData(BaseModel):
    """Batch details"""
    id: UUID
    item_id: UUID
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    batch_number: str
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    received_date: Optional[date] = None
    initial_quantity: Decimal
    current_quantity: Decimal
    unit_cost: int
    total_value: int
    status: BatchStatus
    purchase_order_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    supplier_batch_number: Optional[str] = None
    quality_grade: Optional[str] = None
    quality_notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class ItemBatchDetailData(ItemBatchData):
    """Batch with warehouse breakdown"""
    warehouse_stock: List[BatchWarehouseStock]


class ItemBatchListResponse(BaseModel):
    """Response for batch list"""
    success: bool = True
    data: List[ItemBatchData]
    total: int
    has_more: bool = False


class ItemBatchDetailResponse(BaseModel):
    """Response for single batch"""
    success: bool = True
    data: ItemBatchDetailData


class CreateItemBatchResponse(BaseModel):
    """Response for batch creation"""
    success: bool = True
    data: ItemBatchData
    message: str = "Batch created successfully"


class UpdateItemBatchResponse(BaseModel):
    """Response for batch update"""
    success: bool = True
    data: ItemBatchData
    message: str = "Batch updated successfully"


class AdjustBatchResponse(BaseModel):
    """Response for batch adjustment"""
    success: bool = True
    data: ItemBatchData
    warehouse_stock: BatchWarehouseStock
    message: str = "Batch quantity adjusted"


# ============================================================================
# SELECTION MODELS (FEFO/FIFO)
# ============================================================================

class AvailableBatch(BaseModel):
    """Batch available for selection"""
    batch_id: UUID
    batch_number: str
    expiry_date: Optional[date] = None
    days_until_expiry: Optional[int] = None
    available_quantity: Decimal
    quantity_to_use: Decimal  # Allocated from this batch
    unit_cost: int


class AvailableBatchesResponse(BaseModel):
    """Response for available batches (FEFO/FIFO selection)"""
    success: bool = True
    item_id: UUID
    warehouse_id: UUID
    quantity_requested: Decimal
    quantity_available: Decimal
    quantity_allocated: Decimal
    fully_satisfied: bool
    method: SelectionMethod
    batches: List[AvailableBatch]


# ============================================================================
# EXPIRY TRACKING MODELS
# ============================================================================

class ExpiringBatch(BaseModel):
    """Batch approaching expiry"""
    batch_id: UUID
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    batch_number: str
    expiry_date: date
    days_until_expiry: int
    quantity: Decimal
    total_value: int
    warehouse_id: UUID
    warehouse_name: str


class ExpiringBatchesResponse(BaseModel):
    """Response for expiring batches report"""
    success: bool = True
    days_ahead: int
    data: List[ExpiringBatch]
    total: int
    total_value: int


class ExpiredBatch(BaseModel):
    """Batch that has expired"""
    batch_id: UUID
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    batch_number: str
    expiry_date: date
    days_expired: int
    quantity: Decimal
    total_value: int
    warehouse_id: UUID
    warehouse_name: str


class ExpiredBatchesResponse(BaseModel):
    """Response for expired batches report"""
    success: bool = True
    data: List[ExpiredBatch]
    total: int
    total_value: int


# ============================================================================
# ITEM BATCHES SUMMARY
# ============================================================================

class ItemBatchesSummary(BaseModel):
    """Summary of batches for an item"""
    item_id: UUID
    item_name: str
    total_batches: int
    active_batches: int
    expired_batches: int
    depleted_batches: int
    total_quantity: Decimal
    total_value: int


class ItemBatchesSummaryResponse(BaseModel):
    """Response for item batches summary"""
    success: bool = True
    data: ItemBatchesSummary
    batches: List[ItemBatchData]


class WarehouseBatchesSummary(BaseModel):
    """Summary of batches in a warehouse"""
    warehouse_id: UUID
    warehouse_name: str
    total_batches: int
    expiring_soon: int  # Within 30 days
    expired: int
    total_value: int


class WarehouseBatchesResponse(BaseModel):
    """Response for warehouse batches"""
    success: bool = True
    summary: WarehouseBatchesSummary
    data: List[ItemBatchData]
    total: int
