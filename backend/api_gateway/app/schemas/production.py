"""
Schemas for Production Orders (Perintah Produksi / Work Orders)
Execute manufacturing based on BOM with material and labor tracking
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# PRODUCTION ORDER MATERIALS
# ============================================================================

class ProductionMaterialInput(BaseModel):
    """Input for production material"""
    product_id: UUID
    quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    serial_ids: Optional[List[UUID]] = None


class ProductionMaterialDetail(BaseModel):
    """Production material detail"""
    id: str
    product_id: str
    product_name: str
    product_sku: Optional[str]
    planned_quantity: Decimal
    unit: Optional[str]
    planned_cost: int
    issued_quantity: Decimal
    actual_cost: int
    returned_quantity: Decimal
    variance_quantity: Decimal
    variance_cost: int
    batch_id: Optional[str]
    batch_number: Optional[str]
    issued_date: Optional[date]
    warehouse_id: Optional[str]


# ============================================================================
# PRODUCTION ORDER LABOR
# ============================================================================

class ProductionLaborInput(BaseModel):
    """Input for production labor"""
    operation_id: Optional[UUID] = None
    operation_name: str = Field(..., max_length=100)
    actual_hours: Decimal = Field(..., ge=0)
    worker_id: Optional[UUID] = None
    worker_name: Optional[str] = Field(None, max_length=100)
    hourly_rate: int = Field(0, ge=0)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    notes: Optional[str] = None


class ProductionLaborDetail(BaseModel):
    """Production labor detail"""
    id: str
    operation_id: Optional[str]
    operation_name: str
    planned_hours: Decimal
    planned_cost: int
    actual_hours: Decimal
    actual_cost: int
    worker_id: Optional[str]
    worker_name: Optional[str]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    hourly_rate: int
    notes: Optional[str]
    created_at: datetime


# ============================================================================
# PRODUCTION COMPLETIONS
# ============================================================================

class ProductionCompletionInput(BaseModel):
    """Input for production completion"""
    good_quantity: Decimal = Field(..., gt=0)
    scrap_quantity: Decimal = Field(Decimal("0"), ge=0)
    quality_status: Literal["passed", "failed", "rework"] = "passed"
    inspection_notes: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None


class ProductionCompletionDetail(BaseModel):
    """Production completion detail"""
    id: str
    completion_date: date
    good_quantity: Decimal
    scrap_quantity: Decimal
    quality_status: str
    inspection_notes: Optional[str]
    unit_cost: int
    total_cost: int
    warehouse_id: Optional[str]
    batch_id: Optional[str]
    batch_number: Optional[str]
    journal_id: Optional[str]
    completed_by: Optional[str]
    created_at: datetime


# ============================================================================
# PRODUCTION ORDERS
# ============================================================================

class CreateProductionOrderRequest(BaseModel):
    """Request to create production order"""
    product_id: UUID
    bom_id: UUID
    planned_quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = None
    planned_start_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    work_center_id: Optional[UUID] = None
    warehouse_id: Optional[UUID] = None
    sales_order_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    priority: int = Field(5, ge=1, le=10)
    notes: Optional[str] = None


class UpdateProductionOrderRequest(BaseModel):
    """Request to update production order"""
    planned_quantity: Optional[Decimal] = Field(None, gt=0)
    planned_start_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    work_center_id: Optional[UUID] = None
    warehouse_id: Optional[UUID] = None
    priority: Optional[int] = Field(None, ge=1, le=10)
    notes: Optional[str] = None


class ProductionOrderListItem(BaseModel):
    """Production order in list view"""
    id: str
    order_number: str
    order_date: date
    product_id: str
    product_name: str
    product_sku: Optional[str]
    planned_quantity: Decimal
    completed_quantity: Decimal
    status: str
    priority: int
    planned_start_date: Optional[date]
    planned_end_date: Optional[date]
    completion_percent: Decimal
    created_at: datetime


class ProductionOrderListResponse(BaseModel):
    """Response for listing production orders"""
    items: List[ProductionOrderListItem]
    total: int
    has_more: bool


class ProductionOrderDetail(BaseModel):
    """Detailed production order"""
    id: str
    order_number: str
    order_date: date
    product_id: str
    product_name: str
    product_sku: Optional[str]
    bom_id: str
    bom_code: str
    planned_quantity: Decimal
    completed_quantity: Decimal
    scrapped_quantity: Decimal
    unit: Optional[str]
    planned_start_date: Optional[date]
    planned_end_date: Optional[date]
    actual_start_date: Optional[date]
    actual_end_date: Optional[date]
    work_center_id: Optional[str]
    work_center_name: Optional[str]
    warehouse_id: Optional[str]
    warehouse_name: Optional[str]
    sales_order_id: Optional[str]
    customer_id: Optional[str]
    planned_material_cost: int
    planned_labor_cost: int
    planned_overhead_cost: int
    actual_material_cost: int
    actual_labor_cost: int
    actual_overhead_cost: int
    variance_amount: int
    status: str
    priority: int
    material_issue_journal_id: Optional[str]
    labor_journal_id: Optional[str]
    completion_journal_id: Optional[str]
    notes: Optional[str]
    materials: List[ProductionMaterialDetail]
    labor: List[ProductionLaborDetail]
    completions: List[ProductionCompletionDetail]
    created_at: datetime
    updated_at: datetime


class ProductionOrderDetailResponse(BaseModel):
    """Response for production order detail"""
    success: bool = True
    data: ProductionOrderDetail


# ============================================================================
# COST ANALYSIS
# ============================================================================

class CostAnalysisItem(BaseModel):
    """Cost analysis item"""
    category: str  # material, labor, overhead
    planned: int
    actual: int
    variance: int
    variance_percent: Decimal


class CostAnalysisResponse(BaseModel):
    """Response for cost analysis"""
    success: bool = True
    order_number: str
    product_name: str
    planned_quantity: Decimal
    completed_quantity: Decimal
    analysis: List[CostAnalysisItem]
    total_planned: int
    total_actual: int
    total_variance: int
    unit_cost: int


# ============================================================================
# SCHEDULE & CAPACITY
# ============================================================================

class ScheduleItem(BaseModel):
    """Production schedule item"""
    order_id: str
    order_number: str
    product_name: str
    planned_quantity: Decimal
    planned_start: Optional[date]
    planned_end: Optional[date]
    work_center_name: Optional[str]
    status: str
    priority: int


class ProductionScheduleResponse(BaseModel):
    """Response for production schedule"""
    success: bool = True
    start_date: date
    end_date: date
    items: List[ScheduleItem]
    total_orders: int


class CapacityItem(BaseModel):
    """Capacity utilization item"""
    work_center_id: str
    work_center_name: str
    available_hours: Decimal
    planned_hours: Decimal
    utilization_percent: Decimal


class CapacityResponse(BaseModel):
    """Response for capacity utilization"""
    success: bool = True
    period_start: date
    period_end: date
    items: List[CapacityItem]


# ============================================================================
# GENERIC RESPONSES
# ============================================================================

class ProductionResponse(BaseModel):
    """Generic response for production operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
