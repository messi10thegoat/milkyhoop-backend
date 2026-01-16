"""
Schemas for Bill of Materials (BOM)
Define product structure/recipe for manufacturing
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# WORK CENTERS
# ============================================================================

class CreateWorkCenterRequest(BaseModel):
    """Request to create work center"""
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    capacity_per_hour: Optional[Decimal] = Field(None, gt=0)
    hours_per_day: Decimal = Field(Decimal("8"), gt=0, le=24)
    labor_rate_per_hour: int = Field(0, ge=0)
    overhead_rate_per_hour: int = Field(0, ge=0)


class UpdateWorkCenterRequest(BaseModel):
    """Request to update work center"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    warehouse_id: Optional[UUID] = None
    capacity_per_hour: Optional[Decimal] = Field(None, gt=0)
    hours_per_day: Optional[Decimal] = Field(None, gt=0, le=24)
    labor_rate_per_hour: Optional[int] = Field(None, ge=0)
    overhead_rate_per_hour: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class WorkCenterListItem(BaseModel):
    """Work center in list view"""
    id: str
    code: str
    name: str
    warehouse_name: Optional[str]
    capacity_per_hour: Optional[Decimal]
    labor_rate_per_hour: int
    is_active: bool


class WorkCenterListResponse(BaseModel):
    """Response for listing work centers"""
    items: List[WorkCenterListItem]
    total: int
    has_more: bool


class WorkCenterDetail(BaseModel):
    """Work center detail"""
    id: str
    code: str
    name: str
    description: Optional[str]
    warehouse_id: Optional[str]
    warehouse_name: Optional[str]
    capacity_per_hour: Optional[Decimal]
    hours_per_day: Decimal
    labor_rate_per_hour: int
    overhead_rate_per_hour: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WorkCenterDetailResponse(BaseModel):
    """Response for work center detail"""
    success: bool = True
    data: WorkCenterDetail


# ============================================================================
# BOM COMPONENTS
# ============================================================================

class BOMComponentInput(BaseModel):
    """Input for BOM component"""
    component_product_id: UUID
    quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = None
    wastage_percent: Decimal = Field(Decimal("0"), ge=0, le=100)
    operation_id: Optional[UUID] = None
    unit_cost: int = Field(0, ge=0)
    notes: Optional[str] = None
    sequence_order: int = Field(0, ge=0)


class BOMComponentDetail(BaseModel):
    """BOM component detail"""
    id: str
    component_product_id: str
    component_product_name: str
    component_product_sku: Optional[str]
    quantity: Decimal
    unit: Optional[str]
    wastage_percent: Decimal
    operation_id: Optional[str]
    operation_name: Optional[str]
    unit_cost: int
    extended_cost: int
    notes: Optional[str]
    sequence_order: int
    is_substitute: bool
    substitute_for_id: Optional[str]


# ============================================================================
# BOM OPERATIONS
# ============================================================================

class BOMOperationInput(BaseModel):
    """Input for BOM operation"""
    operation_number: int = Field(..., ge=1)
    operation_name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    work_center_id: Optional[UUID] = None
    setup_time_minutes: int = Field(0, ge=0)
    run_time_minutes: Optional[int] = Field(None, ge=0)
    labor_rate_per_hour: int = Field(0, ge=0)
    overhead_rate_per_hour: int = Field(0, ge=0)
    instructions: Optional[str] = None


class BOMOperationDetail(BaseModel):
    """BOM operation detail"""
    id: str
    operation_number: int
    operation_name: str
    description: Optional[str]
    work_center_id: Optional[str]
    work_center_name: Optional[str]
    setup_time_minutes: int
    run_time_minutes: Optional[int]
    labor_rate_per_hour: int
    overhead_rate_per_hour: int
    instructions: Optional[str]


# ============================================================================
# BILL OF MATERIALS
# ============================================================================

class CreateBOMRequest(BaseModel):
    """Request to create BOM"""
    product_id: UUID
    bom_code: str = Field(..., min_length=1, max_length=50)
    bom_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    output_quantity: Decimal = Field(Decimal("1"), gt=0)
    output_unit: Optional[str] = None
    estimated_time_minutes: Optional[int] = Field(None, ge=0)
    work_center_id: Optional[UUID] = None
    effective_date: Optional[date] = None
    components: List[BOMComponentInput] = Field(default_factory=list)
    operations: List[BOMOperationInput] = Field(default_factory=list)


class UpdateBOMRequest(BaseModel):
    """Request to update BOM"""
    bom_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    output_quantity: Optional[Decimal] = Field(None, gt=0)
    output_unit: Optional[str] = None
    estimated_time_minutes: Optional[int] = Field(None, ge=0)
    work_center_id: Optional[UUID] = None
    effective_date: Optional[date] = None
    obsolete_date: Optional[date] = None


class BOMListItem(BaseModel):
    """BOM in list view"""
    id: str
    bom_code: str
    bom_name: Optional[str]
    product_id: str
    product_name: str
    product_sku: Optional[str]
    version: int
    is_current: bool
    output_quantity: Decimal
    total_cost: int
    component_count: int
    status: str
    created_at: datetime


class BOMListResponse(BaseModel):
    """Response for listing BOMs"""
    items: List[BOMListItem]
    total: int
    has_more: bool


class BOMDetail(BaseModel):
    """Detailed BOM with components and operations"""
    id: str
    bom_code: str
    bom_name: Optional[str]
    description: Optional[str]
    product_id: str
    product_name: str
    product_sku: Optional[str]
    version: int
    is_current: bool
    effective_date: Optional[date]
    obsolete_date: Optional[date]
    output_quantity: Decimal
    output_unit: Optional[str]
    standard_cost: int
    labor_cost: int
    overhead_cost: int
    total_cost: int
    estimated_time_minutes: Optional[int]
    work_center_id: Optional[str]
    work_center_name: Optional[str]
    status: str
    components: List[BOMComponentDetail]
    operations: List[BOMOperationDetail]
    created_at: datetime
    updated_at: datetime


class BOMDetailResponse(BaseModel):
    """Response for BOM detail"""
    success: bool = True
    data: BOMDetail


# ============================================================================
# COST BREAKDOWN
# ============================================================================

class CostBreakdownItem(BaseModel):
    """Cost breakdown item"""
    category: str  # material, labor, overhead
    description: str
    quantity: Optional[Decimal]
    unit_cost: int
    total_cost: int
    percent_of_total: Decimal


class CostBreakdownResponse(BaseModel):
    """Response for cost breakdown"""
    success: bool = True
    bom_code: str
    product_name: str
    output_quantity: Decimal
    unit_cost: int
    total_cost: int
    breakdown: List[CostBreakdownItem]


# ============================================================================
# MATERIALS REQUIRED
# ============================================================================

class MaterialRequiredItem(BaseModel):
    """Material required for production"""
    product_id: str
    product_name: str
    product_sku: Optional[str]
    required_quantity: Decimal
    unit: Optional[str]
    unit_cost: int
    total_cost: int
    available_quantity: Decimal
    shortage: Decimal  # if negative, means available


class MaterialsRequiredResponse(BaseModel):
    """Response for materials required"""
    success: bool = True
    bom_code: str
    production_quantity: Decimal
    materials: List[MaterialRequiredItem]
    total_material_cost: int
    has_shortage: bool


# ============================================================================
# BOM EXPLOSION
# ============================================================================

class BOMExplosionNode(BaseModel):
    """Node in BOM explosion tree"""
    level: int
    product_id: str
    product_name: str
    product_sku: Optional[str]
    quantity: Decimal
    unit: Optional[str]
    unit_cost: int
    extended_cost: int
    has_bom: bool
    bom_id: Optional[str]
    children: List["BOMExplosionNode"] = []


class BOMExplosionResponse(BaseModel):
    """Response for BOM explosion (multi-level)"""
    success: bool = True
    bom_code: str
    product_name: str
    explosion: List[BOMExplosionNode]
    total_material_cost: int
    max_level: int


# ============================================================================
# WHERE USED
# ============================================================================

class WhereUsedItem(BaseModel):
    """Item showing where component is used"""
    bom_id: str
    bom_code: str
    bom_name: Optional[str]
    parent_product_id: str
    parent_product_name: str
    quantity_per_unit: Decimal
    is_current: bool
    status: str


class WhereUsedResponse(BaseModel):
    """Response for where-used query"""
    success: bool = True
    product_id: str
    product_name: str
    used_in: List[WhereUsedItem]
    total_boms: int


# ============================================================================
# GENERIC RESPONSES
# ============================================================================

class BOMResponse(BaseModel):
    """Generic response for BOM operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# Update forward references
BOMExplosionNode.model_rebuild()
