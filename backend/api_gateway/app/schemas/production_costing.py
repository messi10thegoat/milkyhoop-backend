"""
Schemas for Production Costing (Kalkulasi Harga Produksi)
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class CreateStandardCostRequest(BaseModel):
    product_id: UUID
    effective_date: date
    material_cost: int = Field(..., ge=0)
    labor_cost: int = Field(..., ge=0)
    overhead_cost: int = Field(..., ge=0)
    source: Literal["bom_calculation", "actual_average", "manual"] = "manual"
    bom_id: Optional[UUID] = None


class StandardCostListItem(BaseModel):
    id: str
    product_id: str
    product_name: str
    effective_date: date
    end_date: Optional[date]
    material_cost: int
    labor_cost: int
    overhead_cost: int
    total_cost: int
    source: Optional[str]


class StandardCostListResponse(BaseModel):
    items: List[StandardCostListItem]
    total: int
    has_more: bool


class CreateCostPoolRequest(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    pool_type: Optional[str] = None
    allocation_basis: Literal["direct_labor_hours", "machine_hours", "units_produced", "material_cost"]
    budgeted_amount: int = Field(0, ge=0)
    budgeted_basis_quantity: Decimal = Field(Decimal("0"), ge=0)
    fiscal_year: int


class CostPoolListItem(BaseModel):
    id: str
    code: str
    name: str
    pool_type: Optional[str]
    allocation_basis: str
    budgeted_amount: int
    actual_amount: int
    rate_per_unit: int
    is_active: bool


class CostPoolListResponse(BaseModel):
    items: List[CostPoolListItem]
    total: int


class VarianceAnalysisItem(BaseModel):
    category: str
    standard: int
    actual: int
    variance: int
    variance_type: str  # favorable, unfavorable


class VarianceSummaryResponse(BaseModel):
    success: bool = True
    product_id: str
    product_name: str
    period_year: int
    period_month: int
    produced_quantity: Decimal
    analysis: List[VarianceAnalysisItem]
    total_variance: int


class CostingResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
