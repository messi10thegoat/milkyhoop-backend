"""
Stock Transfers Schemas
=======================
Pydantic models for inter-warehouse stock transfers.
NO journal entries - internal movement only.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# TYPES
# ============================================================================

TransferStatus = Literal["draft", "in_transit", "received", "cancelled"]


# ============================================================================
# REQUEST MODELS
# ============================================================================

class StockTransferItemCreate(BaseModel):
    """Line item for stock transfer"""
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    quantity_requested: Decimal = Field(..., gt=0, description="Quantity to transfer")
    unit: Optional[str] = None
    unit_cost: int = 0
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    serial_numbers: Optional[List[str]] = None
    notes: Optional[str] = None


class CreateStockTransferRequest(BaseModel):
    """Create a new stock transfer"""
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    transfer_date: date
    expected_date: Optional[date] = None
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    items: List[StockTransferItemCreate] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_warehouses(self):
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("Source and destination warehouse must be different")
        return self


class UpdateStockTransferRequest(BaseModel):
    """Update draft stock transfer"""
    from_warehouse_id: Optional[UUID] = None
    to_warehouse_id: Optional[UUID] = None
    transfer_date: Optional[date] = None
    expected_date: Optional[date] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[StockTransferItemCreate]] = None

    @model_validator(mode="after")
    def validate_warehouses(self):
        if self.from_warehouse_id and self.to_warehouse_id:
            if self.from_warehouse_id == self.to_warehouse_id:
                raise ValueError("Source and destination warehouse must be different")
        return self


class ShipTransferRequest(BaseModel):
    """Ship a stock transfer"""
    shipped_date: Optional[date] = None  # Defaults to today


class ReceiveItemRequest(BaseModel):
    """Receive quantity for a specific item"""
    item_id: UUID
    quantity_received: Decimal = Field(..., ge=0)


class ReceiveTransferRequest(BaseModel):
    """Receive a stock transfer"""
    received_date: Optional[date] = None  # Defaults to today
    items: Optional[List[ReceiveItemRequest]] = None  # Partial receive support


class CancelTransferRequest(BaseModel):
    """Cancel a stock transfer"""
    reason: str = Field(..., min_length=1, max_length=500)


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class StockTransferItemData(BaseModel):
    """Line item data"""
    id: UUID
    item_id: UUID
    item_code: Optional[str] = None
    item_name: str
    quantity_requested: Decimal
    quantity_shipped: Decimal
    quantity_received: Decimal
    quantity_variance: Decimal  # shipped - received
    unit: Optional[str] = None
    unit_cost: int
    total_value: int
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    serial_numbers: Optional[List[str]] = None
    notes: Optional[str] = None
    line_number: int


class StockTransferData(BaseModel):
    """Stock transfer details"""
    id: UUID
    transfer_number: str
    transfer_date: date
    from_warehouse_id: UUID
    from_warehouse_name: Optional[str] = None
    to_warehouse_id: UUID
    to_warehouse_name: Optional[str] = None
    status: TransferStatus
    shipped_date: Optional[date] = None
    received_date: Optional[date] = None
    expected_date: Optional[date] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    total_items: int
    total_quantity: Decimal
    total_value: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[UUID] = None
    shipped_by: Optional[UUID] = None
    received_by: Optional[UUID] = None


class StockTransferDetailData(StockTransferData):
    """Stock transfer with line items"""
    items: List[StockTransferItemData]


class StockTransferListResponse(BaseModel):
    """Response for transfer list"""
    success: bool = True
    data: List[StockTransferData]
    total: int
    has_more: bool = False


class StockTransferDetailResponse(BaseModel):
    """Response for single transfer"""
    success: bool = True
    data: StockTransferDetailData


class CreateStockTransferResponse(BaseModel):
    """Response for transfer creation"""
    success: bool = True
    data: StockTransferDetailData
    message: str = "Stock transfer created successfully"


class ShipTransferResponse(BaseModel):
    """Response for shipping transfer"""
    success: bool = True
    data: StockTransferData
    message: str = "Stock transfer shipped successfully"
    note: str = "No journal entry created - internal stock movement"


class ReceiveTransferResponse(BaseModel):
    """Response for receiving transfer"""
    success: bool = True
    data: StockTransferData
    message: str = "Stock transfer received successfully"
    note: str = "No journal entry created - internal stock movement"


class CancelTransferResponse(BaseModel):
    """Response for cancelling transfer"""
    success: bool = True
    message: str = "Stock transfer cancelled"


class InTransitSummary(BaseModel):
    """Summary of in-transit transfers"""
    total_transfers: int
    total_items: int
    total_value: int
    oldest_transfer_date: Optional[date] = None


class InTransitResponse(BaseModel):
    """Response for in-transit transfers"""
    success: bool = True
    summary: InTransitSummary
    data: List[StockTransferData]
