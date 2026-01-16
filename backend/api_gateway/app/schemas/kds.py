"""
Schemas for Kitchen Display System (KDS)
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# =============================================================================
# KDS STATIONS
# =============================================================================

class CreateKDSStationRequest(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    station_type: Literal["kitchen", "bar", "prep", "expeditor", "dessert"] = "kitchen"
    display_mode: Literal["ticket", "item", "summary"] = "ticket"
    auto_bump_minutes: Optional[int] = Field(None, ge=1, le=60)
    alert_threshold_minutes: int = Field(10, ge=1, le=60)
    sort_order: Literal["time", "priority", "table"] = "time"


class UpdateKDSStationRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    station_type: Optional[Literal["kitchen", "bar", "prep", "expeditor", "dessert"]] = None
    display_mode: Optional[Literal["ticket", "item", "summary"]] = None
    auto_bump_minutes: Optional[int] = None
    alert_threshold_minutes: Optional[int] = None
    sort_order: Optional[Literal["time", "priority", "table"]] = None
    is_active: Optional[bool] = None


class KDSStationItem(BaseModel):
    id: str
    code: str
    name: str
    station_type: str
    display_mode: str
    auto_bump_minutes: Optional[int]
    alert_threshold_minutes: int
    sort_order: str
    is_active: bool
    pending_orders: int
    avg_completion_time: Optional[int]


class KDSStationListResponse(BaseModel):
    items: List[KDSStationItem]
    total: int


class KDSStationDetailResponse(BaseModel):
    success: bool = True
    id: str
    code: str
    name: str
    station_type: str
    display_mode: str
    auto_bump_minutes: Optional[int]
    alert_threshold_minutes: int
    sort_order: str
    is_active: bool
    category_filters: List[str]


# =============================================================================
# KDS ORDERS
# =============================================================================

class KDSOrderItemInput(BaseModel):
    menu_item_id: UUID
    quantity: int = Field(..., ge=1)
    notes: Optional[str] = None
    modifiers: Optional[List[str]] = None
    priority: Literal["normal", "rush", "fire"] = "normal"


class CreateKDSOrderRequest(BaseModel):
    source_type: Literal["pos", "online", "table"] = "pos"
    source_reference: Optional[str] = None
    table_id: Optional[UUID] = None
    server_name: Optional[str] = None
    items: List[KDSOrderItemInput]
    notes: Optional[str] = None


class KDSOrderItemDetail(BaseModel):
    id: str
    menu_item_id: str
    menu_item_name: str
    quantity: int
    notes: Optional[str]
    modifiers: Optional[List[str]]
    priority: str
    status: str
    station_id: Optional[str]
    station_name: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    elapsed_seconds: Optional[int]


class KDSOrderListItem(BaseModel):
    id: str
    order_number: str
    source_type: str
    source_reference: Optional[str]
    table_number: Optional[str]
    server_name: Optional[str]
    status: str
    priority: str
    item_count: int
    items_pending: int
    items_completed: int
    created_at: datetime
    elapsed_seconds: int
    is_overdue: bool


class KDSOrderListResponse(BaseModel):
    items: List[KDSOrderListItem]
    total: int


class KDSOrderDetailResponse(BaseModel):
    success: bool = True
    id: str
    order_number: str
    source_type: str
    source_reference: Optional[str]
    table_id: Optional[str]
    table_number: Optional[str]
    server_name: Optional[str]
    status: str
    items: List[KDSOrderItemDetail]
    notes: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    total_elapsed_seconds: int


# =============================================================================
# KDS DISPLAY
# =============================================================================

class KDSDisplayOrderItem(BaseModel):
    item_id: str
    name: str
    quantity: int
    modifiers: Optional[List[str]]
    notes: Optional[str]
    priority: str
    status: str
    elapsed_seconds: int


class KDSDisplayOrder(BaseModel):
    order_id: str
    order_number: str
    table_number: Optional[str]
    server_name: Optional[str]
    priority: str
    items: List[KDSDisplayOrderItem]
    created_at: datetime
    elapsed_seconds: int
    alert_level: Literal["normal", "warning", "critical"]


class KDSDisplayResponse(BaseModel):
    success: bool = True
    station_id: str
    station_name: str
    station_type: str
    orders: List[KDSDisplayOrder]
    total_pending: int
    timestamp: datetime


# =============================================================================
# KDS ACTIONS
# =============================================================================

class BumpItemRequest(BaseModel):
    item_id: UUID
    station_id: Optional[UUID] = None


class BumpOrderRequest(BaseModel):
    order_id: UUID
    station_id: Optional[UUID] = None


class RecallOrderRequest(BaseModel):
    order_id: UUID
    reason: Optional[str] = None


class StartItemRequest(BaseModel):
    item_id: UUID
    station_id: UUID


# =============================================================================
# KDS ALERTS
# =============================================================================

class KDSAlertItem(BaseModel):
    id: str
    station_id: str
    station_name: str
    alert_type: str  # overdue, low_stock, equipment
    severity: str  # warning, critical
    message: str
    order_id: Optional[str]
    item_id: Optional[str]
    created_at: datetime
    acknowledged_at: Optional[datetime]


class KDSAlertListResponse(BaseModel):
    items: List[KDSAlertItem]
    total: int
    unacknowledged: int


# =============================================================================
# KDS METRICS
# =============================================================================

class KDSStationMetrics(BaseModel):
    station_id: str
    station_name: str
    orders_completed: int
    items_completed: int
    avg_ticket_time_seconds: int
    avg_item_time_seconds: int
    overdue_count: int
    recall_count: int


class KDSDailyMetrics(BaseModel):
    date: str
    total_orders: int
    total_items: int
    avg_ticket_time_seconds: int
    peak_hour: int
    peak_hour_orders: int
    stations: List[KDSStationMetrics]


class KDSMetricsResponse(BaseModel):
    success: bool = True
    period_start: str
    period_end: str
    metrics: List[KDSDailyMetrics]
    summary: Dict[str, Any]


# =============================================================================
# COMMON
# =============================================================================

class KDSResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
