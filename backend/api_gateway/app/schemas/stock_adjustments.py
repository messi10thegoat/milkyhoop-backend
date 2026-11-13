"""
Pydantic schemas for Stock Adjustments module.

Stock Adjustments are used to:
- Record inventory recounts (opname)
- Handle damaged/expired goods write-offs
- Make manual corrections to inventory

Flow: draft -> posted -> void (optional)
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
from decimal import Decimal
from datetime import date

from .aksi_tersedia import AksiTersediaPeta


# =============================================================================
# REQUEST MODELS - Items
# =============================================================================

class StockAdjustmentItemCreate(BaseModel):
    """Item for creating a stock adjustment."""
    product_id: str = Field(..., description="Product UUID")
    quantity_adjustment: Decimal = Field(..., description="Adjustment quantity (positive=increase, negative=decrease)")
    reason_detail: Optional[str] = Field(None, max_length=500)
    physical_quantity: Optional[Decimal] = Field(None, description="Actual counted quantity for recount")

    @field_validator('product_id')
    @classmethod
    def validate_product_id(cls, v):
        if not v or not v.strip():
            raise ValueError('Product ID is required')
        return v.strip()


class StockAdjustmentItemUpdate(BaseModel):
    """Item for updating a stock adjustment."""
    product_id: str
    quantity_adjustment: Decimal
    reason_detail: Optional[str] = None
    physical_quantity: Optional[Decimal] = None


# =============================================================================
# REQUEST MODELS - Stock Adjustment
# =============================================================================

class CreateStockAdjustmentRequest(BaseModel):
    """Request body for creating a stock adjustment (draft)."""
    adjustment_date: date
    adjustment_type: Literal["increase", "decrease", "recount", "damaged", "expired"]
    storage_location_id: Optional[str] = Field(None, description="Storage location UUID")
    warehouse_id: Optional[str] = Field(None, description="Gudang penyesuaian; kosong = gudang UTAMA (putusan pemilik 9 Nov)")
    reference_no: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None
    items: List[StockAdjustmentItemCreate] = Field(..., min_length=1)


class UpdateStockAdjustmentRequest(BaseModel):
    """Request body for updating a stock adjustment (draft only)."""
    adjustment_date: Optional[date] = None
    adjustment_type: Optional[Literal["increase", "decrease", "recount", "damaged", "expired"]] = None
    storage_location_id: Optional[str] = None
    warehouse_id: Optional[str] = None  # gudang penyesuaian (putusan pemilik 9 Nov); None = tetap
    reference_no: Optional[str] = None
    notes: Optional[str] = None
    items: Optional[List[StockAdjustmentItemUpdate]] = None


class PostStockAdjustmentRequest(BaseModel):
    """Body posting/pratinjau posting (opsional seluruhnya). counter_account_id = akun lawan jurnal pilihan pengguna (putusan pemilik 10 Nov #7a);
    kosong = akun peran bawaan (penyesuaian persediaan). Divalidasi server (services.stok_sesuai.akun_lawan)."""
    counter_account_id: Optional[str] = Field(None, description="Akun lawan jurnal; kosong = bawaan")


class VoidStockAdjustmentRequest(BaseModel):
    """Request body for voiding a stock adjustment."""
    reason: str = Field(..., min_length=1, max_length=500)


class VoidStockAdjustmentPreviewRequest(BaseModel):
    """Pratinjau batal (BUG-197, pola Biaya B3): alasan OPSIONAL -- kosong -> 200 + blok PENYESUAIAN_ALASAN_WAJIB, jurnal pembalik tetap terlihat. /void tulis tetap wajib alasan."""
    reason: Optional[str] = Field(None, max_length=500)


# =============================================================================
# RESPONSE MODELS - Items
# =============================================================================

class StockAdjustmentItemResponse(BaseModel):
    """Stock adjustment item in response."""
    id: str
    product_id: str
    product_code: Optional[str] = None
    product_name: str
    quantity_before: float
    quantity_adjustment: float
    quantity_after: float
    unit: Optional[str] = None
    unit_cost: int
    total_value: int
    reason_detail: Optional[str] = None
    system_quantity: Optional[float] = None
    physical_quantity: Optional[float] = None
    line_number: int = 1


# =============================================================================
# RESPONSE MODELS - Stock Adjustment
# =============================================================================

class StockAdjustmentListItem(BaseModel):
    """Stock adjustment item for list responses."""
    id: str
    adjustment_number: str
    adjustment_date: str
    adjustment_type: str
    storage_location_name: Optional[str] = None
    total_value: int
    item_count: int
    status: str
    reference_no: Optional[str] = None
    created_at: str
    # standar SO (9 Nov 2026): services/stok_sesuai
    jenis_text: Optional[str] = None
    position_text: Optional[str] = None
    aksi: Optional[str] = None
    aksi_semua: List[str] = []
    documents: List[Dict[str, Any]] = []
    doc_count: int = 0


class StockAdjustmentDetail(BaseModel):
    """Full stock adjustment detail."""
    id: str
    adjustment_number: str
    adjustment_date: str
    adjustment_type: str
    storage_location_id: Optional[str] = None
    storage_location_name: Optional[str] = None
    reference_no: Optional[str] = None
    notes: Optional[str] = None
    total_value: int
    item_count: int
    status: str
    journal_id: Optional[str] = None
    journal_number: Optional[str] = None
    items: List[StockAdjustmentItemResponse] = []
    posted_at: Optional[str] = None
    posted_by: Optional[str] = None
    voided_at: Optional[str] = None
    voided_reason: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    # standar SO (9 Nov 2026): gudang penyesuaian + teks + aksi dari penentu tulis (services/stok_sesuai)
    warehouse_id: Optional[str] = None
    warehouse_name: Optional[str] = None
    jenis_text: Optional[str] = None
    position_text: Optional[str] = None
    aksi_tersedia: Optional[AksiTersediaPeta] = None


# =============================================================================
# RESPONSE MODELS - Generic
# =============================================================================

class StockAdjustmentResponse(BaseModel):
    """Generic stock adjustment operation response."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class StockAdjustmentDetailResponse(BaseModel):
    """Response for get stock adjustment detail."""
    success: bool = True
    data: StockAdjustmentDetail


class StockAdjustmentListResponse(BaseModel):
    """Response for list stock adjustments."""
    items: List[StockAdjustmentListItem]
    total: int
    has_more: bool
    sum_total: Optional[str] = None  # with_totals: Σ total_value tanpa void


class StockAdjustmentSummaryResponse(BaseModel):
    """Response for stock adjustments summary."""
    success: bool = True
    data: Dict[str, Any]
