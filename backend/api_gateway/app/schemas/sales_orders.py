"""
Sales Orders Schemas
Order management with shipment tracking.
NO journal entries - accounting impact happens on Invoice creation.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Dict, Any
from datetime import date
from decimal import Decimal
from uuid import UUID


# ============================================================================
# SALES ORDER ITEM SCHEMAS
# ============================================================================

class SalesOrderItemCreate(BaseModel):
    """Schema for creating a sales order line item."""
    item_id: Optional[str] = Field(None, description="Reference to items table (optional)")
    description: str = Field(..., min_length=1, max_length=500, description="Item description")
    quantity: float = Field(1, gt=0, description="Quantity ordered")
    unit: Optional[str] = Field(None, max_length=50, description="Unit of measure")
    unit_price: int = Field(..., ge=0, description="Unit price in smallest currency unit")
    discount_percent: float = Field(0, ge=0, le=100, description="Line discount percentage")
    tax_id: Optional[str] = Field(None, description="Tax code reference")
    tax_rate: float = Field(0, ge=0, le=100, description="Tax rate percentage")
    warehouse_id: Optional[str] = Field(None, description="Warehouse for inventory")
    sort_order: int = Field(0, ge=0, description="Display order")

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if not v or not v.strip():
            raise ValueError('Description is required')
        return v.strip()


class SalesOrderItemUpdate(BaseModel):
    """Schema for updating a sales order line item."""
    id: Optional[str] = Field(None, description="Item ID for existing items")
    item_id: Optional[str] = None
    description: Optional[str] = Field(None, max_length=500)
    quantity: Optional[float] = Field(None, gt=0)
    unit: Optional[str] = Field(None, max_length=50)
    unit_price: Optional[int] = Field(None, ge=0)
    discount_percent: Optional[float] = Field(None, ge=0, le=100)
    tax_id: Optional[str] = None
    tax_rate: Optional[float] = Field(None, ge=0, le=100)
    warehouse_id: Optional[str] = None
    sort_order: Optional[int] = Field(None, ge=0)


class SalesOrderItemResponse(BaseModel):
    """Response schema for sales order line item."""
    id: str
    item_id: Optional[str] = None
    description: str
    quantity: float
    quantity_shipped: float = 0
    quantity_invoiced: float = 0
    quantity_remaining: float = 0  # quantity - shipped
    unit: Optional[str] = None
    unit_price: int
    discount_percent: float = 0
    tax_id: Optional[str] = None
    tax_rate: float = 0
    tax_amount: int = 0
    line_total: int
    warehouse_id: Optional[str] = None
    sort_order: int = 0


# ============================================================================
# SHIPMENT SCHEMAS
# ============================================================================

class ShipmentItemCreate(BaseModel):
    """Schema for creating a shipment item."""
    sales_order_item_id: str = Field(..., description="Sales order item ID")
    quantity_shipped: float = Field(..., gt=0, description="Quantity to ship")


class CreateShipmentRequest(BaseModel):
    """Schema for creating a new shipment."""
    shipment_date: Optional[date] = Field(None, description="Shipment date (defaults to today)")
    carrier: Optional[str] = Field(None, max_length=100, description="Carrier name")
    tracking_number: Optional[str] = Field(None, max_length=100, description="Tracking number")
    items: List[ShipmentItemCreate] = Field(..., min_length=1, description="Items to ship")


class ShipmentItemResponse(BaseModel):
    """Response schema for shipment item."""
    id: str
    sales_order_item_id: str
    description: str
    quantity_shipped: float


class ShipmentDetail(BaseModel):
    """Full detail schema for shipment."""
    id: str
    shipment_number: str
    shipment_date: str
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    status: str
    items: List[ShipmentItemResponse] = []
    created_at: str
    shipped_at: Optional[str] = None
    delivered_at: Optional[str] = None


# ============================================================================
# SALES ORDER REQUEST SCHEMAS
# ============================================================================

class CreateSalesOrderRequest(BaseModel):
    """Schema for creating a new sales order."""
    order_date: date = Field(..., description="Order date")
    expected_ship_date: Optional[date] = Field(None, description="Expected shipping date")
    customer_id: str = Field(..., description="Customer UUID")
    customer_name: str = Field(..., min_length=1, max_length=255, description="Customer name")
    quote_id: Optional[str] = Field(None, description="Reference to quote if converted")
    reference: Optional[str] = Field(None, max_length=100, description="External reference")
    shipping_address: Optional[str] = Field(None, description="Shipping address")
    shipping_method: Optional[str] = Field(None, max_length=100, description="Shipping method")
    shipping_amount: int = Field(0, ge=0, description="Shipping cost")
    discount_amount: int = Field(0, ge=0, description="Order discount")
    notes: Optional[str] = Field(None, description="Notes to customer")
    internal_notes: Optional[str] = Field(None, description="Internal notes")
    items: List[SalesOrderItemCreate] = Field(..., min_length=1, description="Order line items")

    @field_validator('customer_name')
    @classmethod
    def validate_customer_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Customer name is required')
        return v.strip()


class UpdateSalesOrderRequest(BaseModel):
    """Schema for updating an existing sales order (draft only)."""
    order_date: Optional[date] = None
    expected_ship_date: Optional[date] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    reference: Optional[str] = Field(None, max_length=100)
    shipping_address: Optional[str] = None
    shipping_method: Optional[str] = Field(None, max_length=100)
    shipping_amount: Optional[int] = Field(None, ge=0)
    discount_amount: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    items: Optional[List[SalesOrderItemUpdate]] = None


class CancelSalesOrderRequest(BaseModel):
    """Schema for cancelling a sales order."""
    reason: Optional[str] = Field(None, max_length=500, description="Cancellation reason")


class ConvertToInvoiceRequest(BaseModel):
    """Schema for converting sales order to invoice."""
    invoice_date: Optional[date] = Field(None, description="Invoice date (defaults to today)")
    due_date: Optional[date] = Field(None, description="Invoice due date")
    items: Optional[List[Dict[str, Any]]] = Field(None, description="Specific items {so_item_id, quantity}")


# ============================================================================
# SALES ORDER RESPONSE SCHEMAS
# ============================================================================

class SalesOrderListItem(BaseModel):
    """Summary schema for sales order list."""
    id: str
    order_number: str
    order_date: str
    expected_ship_date: Optional[str] = None
    customer_id: str
    customer_name: str
    subtotal: int
    discount_amount: int
    tax_amount: int
    shipping_amount: int
    total_amount: int
    status: str
    shipped_qty: float = 0
    invoiced_qty: float = 0
    created_at: str


class SalesOrderDetail(BaseModel):
    """Full detail schema for single sales order."""
    id: str
    order_number: str
    order_date: str
    expected_ship_date: Optional[str] = None
    customer_id: str
    customer_name: str
    quote_id: Optional[str] = None
    reference: Optional[str] = None
    shipping_address: Optional[str] = None
    shipping_method: Optional[str] = None
    subtotal: int
    discount_amount: int
    tax_amount: int
    shipping_amount: int
    total_amount: int
    status: str
    shipped_qty: float = 0
    invoiced_qty: float = 0
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    items: List[SalesOrderItemResponse] = []
    shipments: List[ShipmentDetail] = []
    invoices: List[Dict[str, Any]] = []  # List of related invoices
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None


class SalesOrderListResponse(BaseModel):
    """Response for sales order list endpoint."""
    items: List[SalesOrderListItem]
    total: int
    has_more: bool = False


class SalesOrderDetailResponse(BaseModel):
    """Response for sales order detail endpoint."""
    success: bool
    data: SalesOrderDetail


class SalesOrderResponse(BaseModel):
    """Generic sales order operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class SalesOrderSummary(BaseModel):
    """Sales order statistics summary."""
    total_orders: int
    draft_count: int
    confirmed_count: int
    partial_shipped_count: int
    shipped_count: int
    partial_invoiced_count: int
    invoiced_count: int
    completed_count: int
    cancelled_count: int
    total_value: int
    pending_shipment_value: int  # confirmed + partial_shipped
    pending_invoice_value: int  # shipped + partial_invoiced


class SalesOrderSummaryResponse(BaseModel):
    """Response for sales order summary endpoint."""
    success: bool
    data: SalesOrderSummary


class PendingOrderItem(BaseModel):
    """Order pending action."""
    id: str
    order_number: str
    customer_name: str
    order_date: str
    total_amount: int
    status: str
    pending_qty: float  # quantity - shipped or quantity - invoiced
    pending_action: str  # 'shipment' or 'invoice'


class PendingOrdersResponse(BaseModel):
    """Response for pending orders endpoint."""
    success: bool
    data: List[PendingOrderItem]
    total: int
