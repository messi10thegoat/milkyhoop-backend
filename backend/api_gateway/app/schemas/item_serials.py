"""
Item Serials Schemas
====================
Pydantic models for serial number tracking.
"""
from datetime import date, datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# TYPES
# ============================================================================

SerialStatus = Literal["available", "reserved", "sold", "returned", "damaged", "scrapped"]
SerialCondition = Literal["new", "refurbished", "used", "damaged"]
MovementType = Literal["received", "transferred", "reserved", "sold", "returned", "adjusted", "damaged", "scrapped", "warranty_claim"]


# ============================================================================
# REQUEST MODELS
# ============================================================================

class CreateItemSerialRequest(BaseModel):
    """Create a single serial number"""
    item_id: UUID
    serial_number: str = Field(..., min_length=1, max_length=100)
    warehouse_id: Optional[UUID] = None
    received_date: Optional[date] = None
    warranty_start_date: Optional[date] = None
    warranty_expiry: Optional[date] = None
    unit_cost: int = 0
    selling_price: int = 0
    purchase_order_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    supplier_serial: Optional[str] = Field(None, max_length=100)
    batch_id: Optional[UUID] = None
    condition: SerialCondition = "new"
    condition_notes: Optional[str] = None
    notes: Optional[str] = None


class BulkCreateSerialsRequest(BaseModel):
    """Create multiple serial numbers at once"""
    item_id: UUID
    warehouse_id: Optional[UUID] = None
    serial_numbers: List[str] = Field(..., min_length=1, max_length=100)
    received_date: Optional[date] = None
    unit_cost: int = 0
    purchase_order_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    condition: SerialCondition = "new"


class UpdateItemSerialRequest(BaseModel):
    """Update serial details"""
    serial_number: Optional[str] = Field(None, min_length=1, max_length=100)
    warranty_start_date: Optional[date] = None
    warranty_expiry: Optional[date] = None
    selling_price: Optional[int] = None
    supplier_serial: Optional[str] = None
    condition: Optional[SerialCondition] = None
    condition_notes: Optional[str] = None
    notes: Optional[str] = None


class TransferSerialRequest(BaseModel):
    """Transfer serial to another warehouse"""
    to_warehouse_id: UUID
    notes: Optional[str] = None


class AdjustSerialRequest(BaseModel):
    """Adjust serial status"""
    status: SerialStatus
    reason: str = Field(..., min_length=1, max_length=500)
    notes: Optional[str] = None


class SearchSerialRequest(BaseModel):
    """Search serial numbers"""
    serial_number: str = Field(..., min_length=1)


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class ItemSerialData(BaseModel):
    """Serial number details"""
    id: UUID
    item_id: UUID
    item_code: Optional[str] = None
    item_name: Optional[str] = None
    serial_number: str
    status: SerialStatus
    warehouse_id: Optional[UUID] = None
    warehouse_name: Optional[str] = None
    received_date: Optional[date] = None
    sold_date: Optional[date] = None
    warranty_start_date: Optional[date] = None
    warranty_expiry: Optional[date] = None
    unit_cost: int
    selling_price: int
    purchase_order_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    sales_invoice_id: Optional[UUID] = None
    sales_receipt_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    customer_name: Optional[str] = None
    supplier_serial: Optional[str] = None
    batch_id: Optional[UUID] = None
    batch_number: Optional[str] = None
    condition: SerialCondition
    condition_notes: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class SerialMovementData(BaseModel):
    """Serial movement history entry"""
    id: UUID
    movement_type: MovementType
    movement_date: datetime
    from_warehouse_id: Optional[UUID] = None
    from_warehouse_name: Optional[str] = None
    to_warehouse_id: Optional[UUID] = None
    to_warehouse_name: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[UUID] = None
    reference_number: Optional[str] = None
    performed_by: Optional[UUID] = None
    performed_by_name: Optional[str] = None
    notes: Optional[str] = None


class ItemSerialDetailData(ItemSerialData):
    """Serial with movement history"""
    movements: List[SerialMovementData]


class ItemSerialListResponse(BaseModel):
    """Response for serial list"""
    success: bool = True
    data: List[ItemSerialData]
    total: int
    has_more: bool = False


class ItemSerialDetailResponse(BaseModel):
    """Response for single serial"""
    success: bool = True
    data: ItemSerialDetailData


class CreateItemSerialResponse(BaseModel):
    """Response for serial creation"""
    success: bool = True
    data: ItemSerialData
    message: str = "Serial number created successfully"


class BulkCreateSerialsResponse(BaseModel):
    """Response for bulk serial creation"""
    success: bool = True
    data: List[ItemSerialData]
    created_count: int
    message: str = "Serial numbers created successfully"


class UpdateItemSerialResponse(BaseModel):
    """Response for serial update"""
    success: bool = True
    data: ItemSerialData
    message: str = "Serial number updated successfully"


class TransferSerialResponse(BaseModel):
    """Response for serial transfer"""
    success: bool = True
    data: ItemSerialData
    movement: SerialMovementData
    message: str = "Serial transferred successfully"


class AdjustSerialResponse(BaseModel):
    """Response for serial adjustment"""
    success: bool = True
    data: ItemSerialData
    movement: SerialMovementData
    message: str = "Serial status adjusted"


# ============================================================================
# SEARCH AND QUERY MODELS
# ============================================================================

class SerialSearchResult(BaseModel):
    """Search result for serial number"""
    serial_id: UUID
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    serial_number: str
    status: SerialStatus
    warehouse_id: Optional[UUID] = None
    warehouse_name: Optional[str] = None
    sold_date: Optional[date] = None
    customer_name: Optional[str] = None

# Alias for compatibility
SearchSerialResult = SerialSearchResult


class SearchSerialResponse(BaseModel):
    """Response for serial search"""
    success: bool = True
    query: str
    data: List[SerialSearchResult]
    total: int


class AvailableSerial(BaseModel):
    """Available serial for selection"""
    serial_id: UUID
    serial_number: str
    unit_cost: int
    condition: SerialCondition
    received_date: Optional[date] = None


class AvailableSerialsResponse(BaseModel):
    """Response for available serials"""
    success: bool = True
    item_id: UUID
    warehouse_id: UUID
    data: List[AvailableSerial]
    total: int


class SerialHistoryResponse(BaseModel):
    """Response for serial movement history"""
    success: bool = True
    serial_id: UUID
    serial_number: str
    data: List[SerialMovementData]
    total: int


# ============================================================================
# SUMMARY MODELS
# ============================================================================

class ItemSerialsSummary(BaseModel):
    """Summary of serials for an item"""
    item_id: UUID
    item_name: str
    total_serials: int
    available: int
    reserved: int
    sold: int
    returned: int
    damaged: int
    scrapped: int


class ItemSerialsForItemResponse(BaseModel):
    """Response for item serials"""
    success: bool = True
    summary: ItemSerialsSummary
    data: List[ItemSerialData]
    total: int


class WarehouseSerialCount(BaseModel):
    """Serial count per warehouse"""
    warehouse_id: UUID
    warehouse_name: str
    available_count: int


class SerialCountByWarehouseResponse(BaseModel):
    """Response for serial count by warehouse"""
    success: bool = True
    item_id: UUID
    item_name: str
    data: List[WarehouseSerialCount]
    total_available: int


class WarehouseSerialsResponse(BaseModel):
    """Response for serials in warehouse"""
    success: bool = True
    warehouse_id: UUID
    warehouse_name: str
    data: List[ItemSerialData]
    total: int
    available_count: int
