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

from .aksi_tersedia import AksiTersediaPeta
from decimal import Decimal
from datetime import date
from uuid import UUID


# =============================================================================
# REQUEST MODELS - Items
# =============================================================================

class PurchaseOrderItemCreate(BaseModel):
    """Item for creating a purchase order."""
    item_id: Optional[str] = Field(None, description="Product UUID")
    item_code: Optional[str] = Field(None, max_length=50)
    description: str = Field(..., min_length=1, max_length=500)
    quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = Field(None, max_length=20)
    unit_price: Decimal = Field(..., ge=0, max_digits=18, decimal_places=2, description="Harga satuan (2 desimal; bulat tetap bulat)")
    discount_percent: float = Field(0, ge=0, le=100)
    discount_amount: Decimal = Field(0, ge=0, max_digits=18, decimal_places=2)
    tax_code: Optional[str] = Field(None, max_length=20)
    tax_rate: float = Field(0, ge=0, le=100)

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        if not v or not v.strip():
            raise ValueError('Keterangan wajib diisi.')
        return v.strip()


class PurchaseOrderItemUpdate(BaseModel):
    """Item for updating a purchase order."""
    id: Optional[str] = Field(None, description="Existing item ID to update")
    item_id: Optional[str] = None
    item_code: Optional[str] = None
    description: str = Field(..., min_length=1, max_length=500)
    quantity: Decimal = Field(..., gt=0)
    unit: Optional[str] = None
    unit_price: Decimal = Field(..., ge=0, max_digits=18, decimal_places=2)
    discount_percent: float = Field(0, ge=0, le=100)
    discount_amount: Decimal = Field(0, ge=0, max_digits=18, decimal_places=2)
    tax_code: Optional[str] = None
    tax_rate: float = Field(0, ge=0, le=100)


# =============================================================================
# REQUEST MODELS - Purchase Order
# =============================================================================

class CreatePurchaseOrderRequest(BaseModel):
    """Request body for creating a purchase order (draft)."""
    vendor_id: Optional[str] = Field(None, description="Vendor UUID")
    vendor_name: str = Field(..., min_length=1, max_length=255)
    # Nomor manual (3 Nov 2026): kosong/absen = otomatis (generate_purchase_order_number melompati terpakai); terpakai -> 409
    po_number: Optional[str] = Field(None, max_length=50, description="Nomor PO manual; kosong = otomatis")
    po_date: date
    expected_date: Optional[date] = Field(None, description="Expected delivery date")
    ship_to_address: Optional[str] = None
    ref_no: Optional[str] = Field(None, max_length=100, description="External reference")
    notes: Optional[str] = None
    items: List[PurchaseOrderItemCreate] = Field(..., min_length=1)
    discount_percent: float = Field(0, ge=0, le=100, description="Overall discount percent")
    discount_amount: Decimal = Field(0, ge=0, max_digits=18, decimal_places=2, description="Diskon header (nominal)")
    tax_rate: float = Field(0, ge=0, le=100, description="Overall tax rate")
    # BUG-205: syarat bayar PO (hari; 0 = Tunai); absen/null = bawaan termin vendor saat PO dibuat (disimpan sebagai snapshot)
    payment_terms_days: Optional[int] = Field(None, ge=0, le=365, description="Syarat bayar (hari; 0 = Tunai); kosong = bawaan termin vendor")

    @field_validator('vendor_name')
    @classmethod
    def validate_vendor_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Nama vendor wajib diisi.')
        return v.strip()

    @field_validator('po_number')
    @classmethod
    def rapikan_po_number(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v


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
    discount_amount: Optional[Decimal] = Field(None, ge=0, max_digits=18, decimal_places=2)
    tax_rate: Optional[float] = Field(None, ge=0, le=100)
    # BUG-205: ubah syarat bayar (hanya angka; mengosongkan TIDAK didukung -- putusan MASTER 13 Nov)
    payment_terms_days: Optional[int] = Field(None, ge=0, le=365)

    @field_validator("payment_terms_days")
    @classmethod
    def _termin_tak_kosong(cls, v):
        if v is None:
            raise ValueError("Syarat bayar tidak bisa dikosongkan; isi jumlah hari (0 = Tunai).")
        return v


# =============================================================================
# REQUEST MODELS - Operations
# =============================================================================

def normal_sj_vendor(v):
    """No. SJ vendor: strip; kosong -> None (kolom CHECK menolak string kosong)."""
    if v is None:
        return None
    v = str(v).strip()
    return v or None


class ReceiveItemRequest(BaseModel):
    """Single item to receive."""
    po_item_id: str = Field(..., description="PO item UUID")
    # P-U1 G1: Decimal (dulu float). TERIMA-TABEL: 0 boleh (baris hanya ditolak); negatif/kosong-semua = BLOK berkalimat penentu (bukan 422)
    quantity_received: Optional[Decimal] = Field(None, max_digits=18, decimal_places=4)  # absen = sisa - ditolak (penentu)
    quantity_rejected: Decimal = Field(Decimal("0"), max_digits=18, decimal_places=4)  # dicatat di GRN saja; bukan stok/HPP/tagih
    reject_reason: Optional[str] = Field(None, max_length=200)


class ReceiveGoodsRequest(BaseModel):
    """Request body for receiving goods."""
    receive_date: date
    items: List[ReceiveItemRequest]   # BUG-203: kosong ditolak handler dgn 422 BERKODE ITEMS_KOSONG (dulu 422 skema umum); tulis tak pernah menerima "semua sisa"
    notes: Optional[str] = None
    # GRN (flag penerimaan_barang_v1, 23 Okt): gudang penerima barang ber-stok; kosong = gudang PO -> gudang bawaan tenant
    warehouse_id: Optional[UUID] = None
    # GRN-SJ-VENDOR (V472, 2 Nov 2026): nomor surat jalan vendor (pola SAP Delivery Note); spasi dibuang, kosong = None
    vendor_delivery_note: Optional[str] = Field(None, max_length=100)

    @field_validator("vendor_delivery_note", mode="before")
    @classmethod
    def _sj_vendor(cls, v):
        return normal_sj_vendor(v)


class ToBillItemRequest(BaseModel):
    """Single item to include in bill."""
    po_item_id: str = Field(..., description="PO item UUID")
    quantity_to_bill: Optional[Decimal] = Field(None, gt=0, description="Qty ditagih; kosong = seluruh sisa belum tertagih")


class ConvertToBillRequest(BaseModel):
    """Request body for converting PO to Bill."""
    bill_date: date
    due_date: Optional[date] = None
    items: Optional[List[ToBillItemRequest]] = Field(None, description="Items to bill, defaults to all unbilled")
    notes: Optional[str] = None


class CancelPurchaseOrderRequest(BaseModel):
    """Request body for cancelling a purchase order."""
    reason: str = Field(..., min_length=1, max_length=500)


# Kontrak PO tahap 2 (22 Okt 2026): badan PRATINJAU boleh tak lengkap -- kekurangan jadi BLOK (200), bukan 422.
class ReceivePreviewRequest(BaseModel):
    """POST /{id}/receive/preview: kosong = SEMUA sisa per baris pada tanggal bisnis hari ini."""
    receive_date: Optional[date] = None
    items: Optional[List[ReceiveItemRequest]] = None
    warehouse_id: Optional[UUID] = None  # GRN (flag penerimaan_barang_v1)
    # GRN-SJ-VENDOR: catatan + No. SJ vendor ikut pratinjau -> payload membawanya (FE tak perlu menyisip sendiri)
    notes: Optional[str] = None
    vendor_delivery_note: Optional[str] = Field(None, max_length=100)

    @field_validator("vendor_delivery_note", mode="before")
    @classmethod
    def _sj_vendor(cls, v):
        return normal_sj_vendor(v)


class ToBillPreviewRequest(BaseModel):
    """POST /{id}/to-bill/preview: kosong = semua sisa belum tertagih, tanggal tagihan = hari ini bisnis."""
    bill_date: Optional[date] = None
    due_date: Optional[date] = None
    items: Optional[List[ToBillItemRequest]] = None
    notes: Optional[str] = None


class CancelPreviewRequest(BaseModel):
    """POST /{id}/cancel/preview: alasan kosong -> blok ALASAN_WAJIB."""
    reason: Optional[str] = Field(None, max_length=500)


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
    quantity_billed: float = 0          # 1 Nov 2026: bill POSTED
    quantity_billed_draft: float = 0    # porsi bill draf (terpisah)
    unit: Optional[str] = None
    # 22 Okt 2026 (temuan WORKSPACE): uang baris = float (dulu int -> PO ber-sen [unit_price 2dp sejak P-U2] membuat GET detail 500)
    unit_price: float
    discount_percent: float = 0
    discount_amount: float = 0
    tax_code: Optional[str] = None
    tax_rate: float = 0
    tax_amount: float = 0
    subtotal: float
    total: float
    line_number: int = 1


# =============================================================================
# RESPONSE MODELS - Purchase Order
# =============================================================================

class FakturDrafPo(BaseModel):
    """Faktur DRAF bertaut baris PO (services/po_daftar.faktur_draf_po) -> pill "Buka faktur draf"."""
    id: str
    bill_number: Optional[str] = None


class PurchaseOrderListItem(BaseModel):
    """Purchase order item for list responses."""
    id: str
    po_number: str
    vendor_id: Optional[str] = None
    vendor_name: str
    po_date: str
    expected_date: Optional[str] = None
    total_amount: float
    amount_received: float = 0
    amount_billed: float = 0
    sisa_ditagih: float = 0
    draft_bill_amount: float = 0  # 1 Nov 2026: porsi bill draf (ditagih/sisa = bill POSTED)
    status: str
    ref_no: Optional[str] = None
    created_at: str
    # P-U2 (22 Okt 2026): keadaan/aksi/position_text/documents dihitung SERVER (services/po_daftar); aditif
    terima_terbuka: bool = False
    terima_hari: Optional[int] = None
    terima_keadaan: Literal["telat", "hari_ini", "akan", "selesai", "tanpa"] = "tanpa"
    terima_teks: str = "—"
    position_text: Optional[str] = None
    aksi: Optional[str] = None
    # BUG-097: dihitung rute (po_daftar) tapi dulu tak dideklarasikan -> dibuang response_model; FE pill draf/jasa buta
    belum_ditagih_sesudah_draf: Optional[float] = None
    draft_bills: List[FakturDrafPo] = []
    terima_tersedia: Optional[bool] = None
    tagih_tersedia: Optional[bool] = None
    documents: List[Dict[str, str]] = []
    doc_count: int = 0


class PurchaseOrderDetail(BaseModel):
    """Full purchase order detail."""
    payment_terms_days: Optional[int] = None   # BUG-205: snapshot syarat bayar (NULL = tak tercatat)
    syarat_bayar_text: Optional[str] = None    # "30 hari" | "Tunai" | None
    # gelombang 3 BELI (10 Nov 2026): kelayakan aksi tulis detail = penentu tulisnya (schemas/aksi_tersedia)
    aksi_tersedia: AksiTersediaPeta = {}
    id: str
    po_number: str
    vendor_id: Optional[str] = None
    vendor_name: str

    # Amounts
    subtotal: float
    discount_percent: float = 0
    discount_amount: float = 0
    tax_rate: float = 0
    tax_amount: float = 0
    total_amount: float
    amount_received: float = 0
    amount_billed: float = 0
    sisa_ditagih: float = 0
    draft_bill_amount: float = 0  # 1 Nov 2026: porsi bill draf
    # P-U2: paritas detail == baris (fungsi sama: services/po_daftar)
    terima_terbuka: bool = False
    terima_hari: Optional[int] = None
    terima_keadaan: Literal["telat", "hari_ini", "akan", "selesai", "tanpa"] = "tanpa"
    terima_teks: str = "—"
    position_text: Optional[str] = None
    aksi: Optional[str] = None
    # BUG-097: dihitung rute (po_daftar) tapi dulu tak dideklarasikan -> dibuang response_model; FE pill draf/jasa buta
    belum_ditagih_sesudah_draf: Optional[float] = None
    draft_bills: List[FakturDrafPo] = []
    terima_tersedia: Optional[bool] = None
    tagih_tersedia: Optional[bool] = None
    documents: List[Dict[str, str]] = []
    doc_count: int = 0

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
    sum_total: Optional[float] = None   # opt-in ?with_totals=true: Σ total_amount hasil filter tanpa cancelled
    sum_sisa: Optional[float] = None    # idem: Σ sisa_ditagih (total - ditagih) tanpa cancelled


class PurchaseOrderSummaryResponse(BaseModel):
    """Response for purchase orders summary."""
    success: bool = True
    data: Dict[str, Any]
