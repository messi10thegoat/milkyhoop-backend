"""
Pydantic schemas for Purchase Orders module (Pesanan Pembelian).

Purchase Orders (PO) track procurement from vendors.
IMPORTANT: PO does NOT create journal entries - journal is created only when Bill is created.

Flow: draft -> sent -> partial_received/received -> partial_billed/billed -> closed
                                                                          ↓
                                                                    cancelled

Endpoints support:
- CRUD for PO and items
- Send PO to vendor
- Receive goods (partial or full)
- Convert to Bill
- Cancel/Close PO
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import date


# =============================================================================
# REQUEST MODELS - Items
# =============================================================================

class PurchaseOrderItemCreate(BaseModel):
    """Item for creating a purchase order."""
    item_id: Optional[str] = Field(None, description="Product UUID")
    item_code: Optional[str] = Field(None, max_length=50)
    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0)
    unit: Optional[str] = Field(None, max_length=20)
    unit_price: int = Field(..., ge=0, description="Price per unit in IDR")
    discount_percent: float = Field(0, ge=0, le=100)
    discount_amount: int = Field(0, ge=0)
    tax_code: Optional[str] = Field(None, max_length=20)
    tax_rate: float = Field(0, ge=0, le=100)

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if not v or not v.strip():
            raise ValueError('Description is required')
        return v.strip()


class PurchaseOrderItemUpdate(BaseModel):
    """Item for updating a purchase order."""
    id: Optional[str] = Field(None, description="Existing item ID to update")
    item_id: Optional[str] = None
    item_code: Optional[str] = None
    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0)
    unit: Optional[str] = None
    unit_price: int = Field(..., ge=0)
    discount_percent: float = Field(0, ge=0, le=100)
    discount_amount: int = Field(0, ge=0)
    tax_code: Optional[str] = None
    tax_rate: float = Field(0, ge=0, le=100)


# =============================================================================
# REQUEST MODELS - Purchase Order
# =============================================================================

class CreatePurchaseOrderRequest(BaseModel):
    """Request body for creating a purchase order (draft)."""
    vendor_id: Optional[str] = Field(None, description="Vendor UUID")
    vendor_name: str = Field(..., min_length=1, max_length=255)
    po_date: date
    expected_date: Optional[date] = Field(None, description="Expected delivery date")
    ship_to_address: Optional[str] = None
    ref_no: Optional[str] = Field(None, max_length=100, description="External reference")
    notes: Optional[str] = None
    items: List[PurchaseOrderItemCreate] = Field(..., min_length=1)
    discount_percent: float = Field(0, ge=0, le=100, description="Overall discount percent")
    discount_amount: int = Field(0, ge=0, description="Overall discount amount")
    tax_rate: float = Field(0, ge=0, le=100, description="Overall tax rate")

    @field_validator('vendor_name')
    @classmethod
    def validate_vendor_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Vendor name is required')
        return v.strip()


class UpdatePurchaseOrderRequest(BaseModel):
    """Request body for updating a purchase order (draft/sent only)."""
    vendor_id: Optional[str] = None
    vendor_name: Optional[str] = Field(None, max_length=255)
    po_date: Optional[date] = None
    expected_date: Optional[date] = None
    ship_to_address: Optional[str] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[PurchaseOrderItemUpdate]] = None
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    discount_amount: Optional[int] = Field(None, ge=0)
    tax_rate: Optional[float] = Field(None, ge=0, le=100)


# =============================================================================
# REQUEST MODELS - Operations
# =============================================================================

class ReceiveItemRequest(BaseModel):
    """Single item to receive."""
    po_item_id: str = Field(..., description="PO item UUID")
    quantity_received: float = Field(..., gt=0)


class ReceiveGoodsRequest(BaseModel):
    """Request body for receiving goods."""
    receive_date: date
    items: List[ReceiveItemRequest] = Field(..., min_length=1)
    notes: Optional[str] = None


class ToBillItemRequest(BaseModel):
    """Single item to include in bill."""
    po_item_id: str = Field(..., description="PO item UUID")
    quantity_to_bill: Optional[float] = Field(None, gt=0, description="Quantity to bill, defaults to unbilled qty")


class ConvertToBillRequest(BaseModel):
    """Request body for converting PO to Bill."""
    bill_date: date
    due_date: Optional[date] = None
    items: Optional[List[ToBillItemRequest]] = Field(None, description="Items to bill, defaults to all unbilled")
    notes: Optional[str] = None


class CancelPurchaseOrderRequest(BaseModel):
    """Request body for cancelling a purchase order."""
    reason: str = Field(..., min_length=1, max_length=500)


# =============================================================================
# RESPONSE MODELS - Items
# =============================================================================

class PurchaseOrderItemResponse(BaseModel):
    """Purchase order item in response."""
    id: str
    item_id: Optional[str] = None
    item_code: Optional[str] = None
    description: str
    quantity: float
    quantity_received: float = 0
    quantity_billed: float = 0
    unit: Optional[str] = None
    unit_price: int
    discount_percent: float = 0
    discount_amount: int = 0
    tax_code: Optional[str] = None
    tax_rate: float = 0
    tax_amount: int = 0
    subtotal: int
    total: int
    line_number: int = 1


# =============================================================================
# RESPONSE MODELS - Purchase Order
# =============================================================================

class PurchaseOrderListItem(BaseModel):
    """Purchase order item for list responses."""
    id: str
    po_number: str
    vendor_id: Optional[str] = None
    vendor_name: str
    po_date: str
    expected_date: Optional[str] = None
    total_amount: int
    amount_received: int = 0
    amount_billed: int = 0
    status: str
    ref_no: Optional[str] = None
    created_at: str


class PurchaseOrderDetail(BaseModel):
    """Full purchase order detail."""
    id: str
    po_number: str
    vendor_id: Optional[str] = None
    vendor_name: str

    # Amounts
    subtotal: int
    discount_percent: float = 0
    discount_amount: int = 0
    tax_rate: float = 0
    tax_amount: int = 0
    total_amount: int
    amount_received: int = 0
    amount_billed: int = 0

    # Status & dates
    status: str
    po_date: str
    expected_date: Optional[str] = None
    ship_to_address: Optional[str] = None
    ref_no: Optional[str] = None
    notes: Optional[str] = None

    # Items
    items: List[PurchaseOrderItemResponse] = []

    # Linked bills
    bills: List[Dict[str, Any]] = []

    # Status tracking
    sent_at: Optional[str] = None
    sent_by: Optional[str] = None
    cancelled_at: Optional[str] = None
    cancelled_by: Optional[str] = None
    cancelled_reason: Optional[str] = None
    closed_at: Optional[str] = None
    closed_by: Optional[str] = None

    # Audit
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class PurchaseOrderResponse(BaseModel):
    """Generic purchase order operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class PurchaseOrderDetailResponse(BaseModel):
    """Response for get purchase order detail."""
    success: bool = True
    data: PurchaseOrderDetail


class PurchaseOrderListResponse(BaseModel):
    """Response for list purchase orders."""
    items: List[PurchaseOrderListItem]
    total: int
    has_more: bool = False


class PurchaseOrderSummaryResponse(BaseModel):
    """Response for purchase orders summary."""
    success: bool = True
    data: Dict[str, Any]
