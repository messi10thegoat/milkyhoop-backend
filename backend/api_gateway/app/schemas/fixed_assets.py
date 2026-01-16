"""
Fixed Assets (Aset Tetap) Schemas
"""
from datetime import datetime, date
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class DepreciationMethod(str, Enum):
    straight_line = "straight_line"
    declining_balance = "declining_balance"
    units_of_production = "units_of_production"


class AssetStatus(str, Enum):
    draft = "draft"
    active = "active"
    fully_depreciated = "fully_depreciated"
    disposed = "disposed"
    sold = "sold"


class DisposalMethod(str, Enum):
    sold = "sold"
    scrapped = "scrapped"
    donated = "donated"
    lost = "lost"


class MaintenanceType(str, Enum):
    repair = "repair"
    service = "service"
    upgrade = "upgrade"
    inspection = "inspection"


class DepreciationStatus(str, Enum):
    scheduled = "scheduled"
    posted = "posted"
    reversed = "reversed"


# ============================================
# Asset Category Schemas
# ============================================

class AssetCategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    code: Optional[str] = Field(None, max_length=50)
    depreciation_method: DepreciationMethod = DepreciationMethod.straight_line
    useful_life_months: Optional[int] = None
    salvage_value_percent: float = 0
    asset_account_id: Optional[UUID] = None
    depreciation_account_id: Optional[UUID] = None
    accumulated_depreciation_account_id: Optional[UUID] = None


class AssetCategoryCreate(AssetCategoryBase):
    pass


class AssetCategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    code: Optional[str] = Field(None, max_length=50)
    depreciation_method: Optional[DepreciationMethod] = None
    useful_life_months: Optional[int] = None
    salvage_value_percent: Optional[float] = None
    asset_account_id: Optional[UUID] = None
    depreciation_account_id: Optional[UUID] = None
    accumulated_depreciation_account_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class AssetCategoryResponse(AssetCategoryBase):
    id: UUID
    tenant_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    asset_account_code: Optional[str] = None
    asset_account_name: Optional[str] = None
    depreciation_account_code: Optional[str] = None
    depreciation_account_name: Optional[str] = None
    accumulated_depreciation_account_code: Optional[str] = None
    accumulated_depreciation_account_name: Optional[str] = None

    class Config:
        from_attributes = True


# ============================================
# Fixed Asset Schemas
# ============================================

class FixedAssetBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    purchase_date: date
    purchase_price: int
    vendor_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    warehouse_id: Optional[UUID] = None
    location_detail: Optional[str] = Field(None, max_length=255)
    depreciation_method: DepreciationMethod = DepreciationMethod.straight_line
    useful_life_months: int
    salvage_value: int = 0
    depreciation_start_date: date
    asset_account_id: Optional[UUID] = None
    depreciation_account_id: Optional[UUID] = None
    accumulated_depreciation_account_id: Optional[UUID] = None


class FixedAssetCreate(FixedAssetBase):
    pass


class FixedAssetUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    warehouse_id: Optional[UUID] = None
    location_detail: Optional[str] = Field(None, max_length=255)
    # Note: depreciation settings can only be changed while in draft


class FixedAssetResponse(FixedAssetBase):
    id: UUID
    tenant_id: str
    asset_number: str
    current_value: int
    accumulated_depreciation: int
    status: AssetStatus
    disposal_date: Optional[date] = None
    disposal_method: Optional[DisposalMethod] = None
    disposal_price: Optional[int] = None
    disposal_journal_id: Optional[UUID] = None
    gain_loss_amount: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None
    category_name: Optional[str] = None
    vendor_name: Optional[str] = None
    warehouse_name: Optional[str] = None

    class Config:
        from_attributes = True


class FixedAssetDetailResponse(FixedAssetResponse):
    depreciation_history: List["AssetDepreciationResponse"] = []
    maintenance_history: List["AssetMaintenanceResponse"] = []


# ============================================
# Asset Depreciation Schemas
# ============================================

class AssetDepreciationResponse(BaseModel):
    id: UUID
    asset_id: UUID
    depreciation_date: date
    period_year: int
    period_month: int
    depreciation_amount: int
    accumulated_amount: int
    book_value: int
    status: DepreciationStatus
    journal_id: Optional[UUID] = None
    created_at: datetime
    posted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DepreciationScheduleResponse(BaseModel):
    asset: FixedAssetResponse
    schedule: List[AssetDepreciationResponse]
    total_depreciation: int
    months_remaining: int


# ============================================
# Asset Maintenance Schemas
# ============================================

class AssetMaintenanceBase(BaseModel):
    maintenance_date: date
    description: str
    cost: Optional[int] = None
    vendor_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    maintenance_type: Optional[MaintenanceType] = None
    next_maintenance_date: Optional[date] = None


class AssetMaintenanceCreate(AssetMaintenanceBase):
    pass


class AssetMaintenanceResponse(AssetMaintenanceBase):
    id: UUID
    asset_id: UUID
    created_at: datetime
    vendor_name: Optional[str] = None

    class Config:
        from_attributes = True


# ============================================
# Activate Asset
# ============================================

class ActivateAssetRequest(BaseModel):
    payment_account_id: UUID  # Kas/Bank/Hutang account


class ActivateAssetResponse(BaseModel):
    asset_id: UUID
    asset_number: str
    status: AssetStatus
    journal_id: UUID
    journal_number: str
    depreciation_schedule_count: int


# ============================================
# Dispose/Sell Asset
# ============================================

class DisposeAssetRequest(BaseModel):
    disposal_date: date
    disposal_method: DisposalMethod


class DisposeAssetResponse(BaseModel):
    asset_id: UUID
    asset_number: str
    status: AssetStatus
    disposal_method: DisposalMethod
    book_value_at_disposal: int
    loss_amount: int
    journal_id: UUID


class SellAssetRequest(BaseModel):
    sale_date: date
    sale_price: int
    receivable_account_id: UUID  # Kas/Bank/Piutang


class SellAssetResponse(BaseModel):
    asset_id: UUID
    asset_number: str
    status: AssetStatus
    sale_price: int
    book_value_at_sale: int
    gain_loss_amount: int  # positive = gain, negative = loss
    journal_id: UUID


# ============================================
# Calculate/Post Depreciation (Batch)
# ============================================

class CalculateDepreciationRequest(BaseModel):
    year: int
    month: int


class CalculateDepreciationItem(BaseModel):
    asset_id: UUID
    asset_number: str
    asset_name: str
    depreciation_amount: int
    accumulated_amount: int
    book_value: int


class CalculateDepreciationResponse(BaseModel):
    year: int
    month: int
    items: List[CalculateDepreciationItem]
    total_depreciation: int
    asset_count: int


class PostDepreciationRequest(BaseModel):
    year: int
    month: int


class PostDepreciationResult(BaseModel):
    asset_id: UUID
    asset_number: str
    depreciation_amount: int
    journal_id: Optional[UUID] = None
    success: bool
    error: Optional[str] = None


class PostDepreciationResponse(BaseModel):
    year: int
    month: int
    posted: int
    failed: int
    total_depreciation: int
    results: List[PostDepreciationResult]


# ============================================
# Asset Reports
# ============================================

class AssetRegisterItem(BaseModel):
    id: UUID
    asset_number: str
    name: str
    category_name: Optional[str] = None
    purchase_date: date
    purchase_price: int
    depreciation_method: DepreciationMethod
    useful_life_months: int
    salvage_value: int
    current_value: int
    accumulated_depreciation: int
    status: AssetStatus
    location_detail: Optional[str] = None


class AssetRegisterResponse(BaseModel):
    items: List[AssetRegisterItem]
    total_purchase_price: int
    total_current_value: int
    total_accumulated_depreciation: int
    asset_count: int


class AssetsByCategoryItem(BaseModel):
    category_id: Optional[UUID] = None
    category_name: str
    asset_count: int
    total_purchase_price: int
    total_current_value: int
    total_accumulated_depreciation: int


class AssetsByCategoryResponse(BaseModel):
    items: List[AssetsByCategoryItem]


class AssetsByLocationItem(BaseModel):
    warehouse_id: Optional[UUID] = None
    warehouse_name: str
    asset_count: int
    total_purchase_price: int
    total_current_value: int


class AssetsByLocationResponse(BaseModel):
    items: List[AssetsByLocationItem]


# ============================================
# Maintenance Due
# ============================================

class MaintenanceDueItem(BaseModel):
    asset_id: UUID
    asset_number: str
    asset_name: str
    last_maintenance_date: Optional[date] = None
    next_maintenance_date: date
    maintenance_type: Optional[MaintenanceType] = None
    days_until: int


class MaintenanceDueResponse(BaseModel):
    days_ahead: int
    items: List[MaintenanceDueItem]


# List responses
class AssetCategoryListResponse(BaseModel):
    items: List[AssetCategoryResponse]
    total: int


class FixedAssetListResponse(BaseModel):
    items: List[FixedAssetResponse]
    total: int


# Forward reference update
FixedAssetDetailResponse.model_rebuild()
