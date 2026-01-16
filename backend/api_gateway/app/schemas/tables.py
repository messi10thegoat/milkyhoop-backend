"""
Schemas for Table Management (Manajemen Meja Restoran)
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# =============================================================================
# TABLE AREAS
# =============================================================================

class CreateTableAreaRequest(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    floor_number: int = Field(1, ge=-5, le=100)
    is_outdoor: bool = False
    is_smoking: bool = False
    display_order: int = 0


class UpdateTableAreaRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    floor_number: Optional[int] = None
    is_outdoor: Optional[bool] = None
    is_smoking: Optional[bool] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class TableAreaItem(BaseModel):
    id: str
    code: str
    name: str
    description: Optional[str]
    floor_number: int
    is_outdoor: bool
    is_smoking: bool
    display_order: int
    is_active: bool
    table_count: int
    available_count: int


class TableAreaListResponse(BaseModel):
    items: List[TableAreaItem]
    total: int


# =============================================================================
# TABLES
# =============================================================================

class CreateTableRequest(BaseModel):
    table_number: str = Field(..., max_length=20)
    area_id: UUID
    capacity_min: int = Field(1, ge=1)
    capacity_max: int = Field(..., ge=1)
    table_shape: Literal["round", "square", "rectangle", "booth", "bar"] = "square"
    position_x: Optional[int] = None
    position_y: Optional[int] = None
    is_reservable: bool = True
    is_combinable: bool = True


class UpdateTableRequest(BaseModel):
    table_number: Optional[str] = Field(None, max_length=20)
    area_id: Optional[UUID] = None
    capacity_min: Optional[int] = None
    capacity_max: Optional[int] = None
    table_shape: Optional[Literal["round", "square", "rectangle", "booth", "bar"]] = None
    position_x: Optional[int] = None
    position_y: Optional[int] = None
    is_reservable: Optional[bool] = None
    is_combinable: Optional[bool] = None
    is_active: Optional[bool] = None


class TableListItem(BaseModel):
    id: str
    table_number: str
    area_id: str
    area_name: str
    capacity_min: int
    capacity_max: int
    table_shape: str
    status: str  # available, occupied, reserved, blocked
    current_session_id: Optional[str]
    current_guests: Optional[int]
    session_duration_minutes: Optional[int]
    is_reservable: bool
    is_active: bool


class TableListResponse(BaseModel):
    items: List[TableListItem]
    total: int


class TableDetailResponse(BaseModel):
    success: bool = True
    id: str
    table_number: str
    area_id: str
    area_name: str
    capacity_min: int
    capacity_max: int
    table_shape: str
    position_x: Optional[int]
    position_y: Optional[int]
    status: str
    is_reservable: bool
    is_combinable: bool
    is_active: bool
    current_session: Optional[Dict[str, Any]]
    upcoming_reservations: List[Dict[str, Any]]


# =============================================================================
# RESERVATIONS
# =============================================================================

class CreateReservationRequest(BaseModel):
    reservation_date: date
    reservation_time: time
    party_size: int = Field(..., ge=1)
    customer_name: str = Field(..., max_length=100)
    customer_phone: str = Field(..., max_length=50)
    customer_email: Optional[str] = Field(None, max_length=100)
    duration_minutes: int = Field(90, ge=30, le=480)
    table_id: Optional[UUID] = None
    area_preference: Optional[UUID] = None
    special_requests: Optional[str] = None
    occasion: Optional[str] = None  # birthday, anniversary, business, etc.


class UpdateReservationRequest(BaseModel):
    reservation_date: Optional[date] = None
    reservation_time: Optional[time] = None
    party_size: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    duration_minutes: Optional[int] = None
    table_id: Optional[UUID] = None
    special_requests: Optional[str] = None
    occasion: Optional[str] = None
    status: Optional[Literal["confirmed", "cancelled", "no_show"]] = None


class ReservationListItem(BaseModel):
    id: str
    reservation_number: str
    reservation_date: date
    reservation_time: time
    party_size: int
    customer_name: str
    customer_phone: str
    table_id: Optional[str]
    table_number: Optional[str]
    area_name: Optional[str]
    status: str
    duration_minutes: int
    occasion: Optional[str]
    created_at: datetime


class ReservationListResponse(BaseModel):
    items: List[ReservationListItem]
    total: int
    has_more: bool


class ReservationDetailResponse(BaseModel):
    success: bool = True
    id: str
    reservation_number: str
    reservation_date: date
    reservation_time: time
    end_time: time
    party_size: int
    customer_name: str
    customer_phone: str
    customer_email: Optional[str]
    table_id: Optional[str]
    table_number: Optional[str]
    area_id: Optional[str]
    area_name: Optional[str]
    status: str
    duration_minutes: int
    special_requests: Optional[str]
    occasion: Optional[str]
    confirmed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    seated_at: Optional[datetime]
    created_at: datetime


# =============================================================================
# TABLE SESSIONS
# =============================================================================

class CreateTableSessionRequest(BaseModel):
    table_id: UUID
    guest_count: int = Field(..., ge=1)
    reservation_id: Optional[UUID] = None
    server_id: Optional[UUID] = None
    notes: Optional[str] = None


class UpdateTableSessionRequest(BaseModel):
    guest_count: Optional[int] = None
    server_id: Optional[UUID] = None
    notes: Optional[str] = None


class TableSessionListItem(BaseModel):
    id: str
    table_id: str
    table_number: str
    area_name: str
    guest_count: int
    server_name: Optional[str]
    status: str  # active, closed, transferred
    started_at: datetime
    duration_minutes: int
    order_count: int
    total_amount: int
    reservation_id: Optional[str]


class TableSessionListResponse(BaseModel):
    items: List[TableSessionListItem]
    total: int


class TableSessionDetailResponse(BaseModel):
    success: bool = True
    id: str
    table_id: str
    table_number: str
    area_name: str
    guest_count: int
    server_id: Optional[str]
    server_name: Optional[str]
    status: str
    started_at: datetime
    closed_at: Optional[datetime]
    duration_minutes: int
    reservation_id: Optional[str]
    orders: List[Dict[str, Any]]
    total_amount: int
    notes: Optional[str]


# =============================================================================
# WAITLIST
# =============================================================================

class CreateWaitlistEntryRequest(BaseModel):
    customer_name: str = Field(..., max_length=100)
    customer_phone: str = Field(..., max_length=50)
    party_size: int = Field(..., ge=1)
    area_preference: Optional[UUID] = None
    seating_preference: Optional[Literal["indoor", "outdoor", "any"]] = "any"
    notes: Optional[str] = None


class WaitlistEntryItem(BaseModel):
    id: str
    queue_number: int
    customer_name: str
    customer_phone: str
    party_size: int
    area_preference: Optional[str]
    seating_preference: str
    status: str  # waiting, notified, seated, cancelled, no_show
    estimated_wait_minutes: int
    actual_wait_minutes: Optional[int]
    created_at: datetime


class WaitlistResponse(BaseModel):
    items: List[WaitlistEntryItem]
    total_waiting: int
    avg_wait_minutes: int


# =============================================================================
# TABLE LAYOUT / FLOOR PLAN
# =============================================================================

class TableLayoutItem(BaseModel):
    id: str
    table_number: str
    capacity_max: int
    table_shape: str
    position_x: int
    position_y: int
    status: str
    current_guests: Optional[int]
    session_duration_minutes: Optional[int]


class FloorPlanResponse(BaseModel):
    success: bool = True
    area_id: str
    area_name: str
    floor_number: int
    tables: List[TableLayoutItem]
    dimensions: Dict[str, int]


# =============================================================================
# TABLE AVAILABILITY
# =============================================================================

class TimeSlotAvailability(BaseModel):
    time: time
    available_tables: int
    table_ids: List[str]


class TableAvailabilityRequest(BaseModel):
    date: date
    party_size: int = Field(..., ge=1)
    duration_minutes: int = Field(90, ge=30, le=480)
    area_id: Optional[UUID] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None


class TableAvailabilityResponse(BaseModel):
    success: bool = True
    date: date
    party_size: int
    time_slots: List[TimeSlotAvailability]


# =============================================================================
# TABLE STATISTICS
# =============================================================================

class TableTurnoverStats(BaseModel):
    table_id: str
    table_number: str
    area_name: str
    sessions_count: int
    total_guests: int
    total_revenue: int
    avg_session_minutes: int
    turnover_rate: Decimal


class TableStatsResponse(BaseModel):
    success: bool = True
    period_start: date
    period_end: date
    tables: List[TableTurnoverStats]
    summary: Dict[str, Any]


# =============================================================================
# COMMON
# =============================================================================

class TableResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
