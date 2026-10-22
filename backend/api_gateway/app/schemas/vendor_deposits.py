"""
Vendor Deposits (Uang Muka Vendor) Schemas
"""
from datetime import datetime, date
from decimal import Decimal
from typing import Annotated, Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class VendorDepositStatus(str, Enum):
    draft = "draft"
    posted = "posted"
    partial = "partial"
    applied = "applied"
    void = "void"


class PaymentMethod(str, Enum):
    cash = "cash"
    transfer = "transfer"
    check = "check"
    giro = "giro"


# P-U3b (22 Okt 2026): nominal REQUEST = Decimal > 0, maks 2 desimal (lebih -> 422; dulu int -> sen ditolak/terpotong, Law 9);
# nominal RESPONS = float (pola PO; Decimal di JSON pydantic v2 = string).
Uang = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]


# ============================================
# Vendor Deposit Schemas
# ============================================

class VendorDepositBase(BaseModel):
    deposit_date: date
    vendor_id: UUID
    payment_method: PaymentMethod = PaymentMethod.transfer
    bank_account_id: Optional[UUID] = None
    reference: Optional[str] = Field(None, max_length=100)
    purchase_order_id: Optional[UUID] = None
    notes: Optional[str] = None


class VendorDepositCreate(VendorDepositBase):
    amount: Uang
    # CW penuh (22 Okt 2026): "Simpan & posting" satu langkah (pola QBO/NetSuite Vendor Prepayment); bawaan = draf (perilaku lama)
    simpan_dan_posting: bool = False


class VendorDepositUpdate(BaseModel):
    deposit_date: Optional[date] = None
    amount: Optional[Uang] = None
    payment_method: Optional[PaymentMethod] = None
    bank_account_id: Optional[UUID] = None
    reference: Optional[str] = Field(None, max_length=100)
    purchase_order_id: Optional[UUID] = None
    notes: Optional[str] = None


class VendorDepositResponse(VendorDepositBase):
    id: UUID
    tenant_id: str
    deposit_number: str
    amount: float
    applied_amount: float
    remaining_amount: float
    status: VendorDepositStatus
    journal_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None
    vendor_name: Optional[str] = None
    vendor_code: Optional[str] = None
    bank_account_name: Optional[str] = None
    purchase_order_number: Optional[str] = None
    # P-U3 (22 Okt 2026): turunan JURNAL, dihitung SERVER (services/uang_muka_vendor_daftar); aditif -- kolom cache lama tetap
    sisa: Optional[float] = None
    terpakai: float = 0
    dikembalikan: float = 0
    sisa_keadaan: Optional[str] = None
    status_text: Optional[str] = None
    position_text: Optional[str] = None
    aksi: Optional[str] = None
    documents: List[dict] = []
    doc_count: int = 0

    class Config:
        from_attributes = True


class VendorDepositDetailResponse(VendorDepositResponse):
    applications: List["VendorDepositApplicationResponse"] = []
    refunds: List["VendorDepositRefundResponse"] = []


# ============================================
# Deposit Application Schemas
# ============================================

class VendorDepositApplicationCreate(BaseModel):
    bill_id: UUID
    amount: Uang
    applied_date: Optional[date] = None  # defaults to today


class VendorDepositApplicationResponse(BaseModel):
    id: UUID
    vendor_deposit_id: UUID
    bill_id: UUID
    bill_number: Optional[str] = None
    bill_date: Optional[date] = None
    amount: float
    applied_date: date
    journal_id: Optional[UUID] = None
    created_at: datetime
    created_by: Optional[UUID] = None
    # V437 / P-U3b: penerapan yang DILEPAS tetap tercatat (riwayat append-only)
    status: str = "active"
    reversed_by_id: Optional[UUID] = None
    reversed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============================================
# Deposit Refund Schemas
# ============================================

class VendorDepositRefundCreate(BaseModel):
    refund_date: date
    amount: Uang
    bank_account_id: Optional[UUID] = None
    # CW penuh: rekening (bank_account_id) ATAU akun kas/bank CoA (account_id, ASSET is_cash) WAJIB -- kembar uang muka pelanggan
    account_id: Optional[UUID] = None
    reference: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class VendorDepositRefundResponse(BaseModel):
    id: UUID
    vendor_deposit_id: UUID
    refund_date: date
    amount: float
    bank_account_id: Optional[UUID] = None
    bank_account_name: Optional[str] = None
    reference: Optional[str] = None
    journal_id: Optional[UUID] = None
    notes: Optional[str] = None
    created_at: datetime
    created_by: Optional[UUID] = None

    class Config:
        from_attributes = True


# ============================================
# Apply to Bill Request/Response
# ============================================

class ApplyDepositRequest(BaseModel):
    bill_id: UUID
    amount: Uang
    applied_date: Optional[date] = None


class ApplyDepositResponse(BaseModel):
    application_id: UUID
    deposit_id: UUID
    deposit_number: str
    bill_id: UUID
    bill_number: str
    applied_amount: float
    deposit_remaining: float
    bill_remaining: float
    journal_id: UUID


# ============================================
# Available Deposits for Vendor
# ============================================

class AvailableDepositItem(BaseModel):
    id: UUID
    deposit_number: str
    deposit_date: date
    amount: float
    applied_amount: float
    remaining_amount: float
    reference: Optional[str] = None


class AvailableDepositsResponse(BaseModel):
    vendor_id: UUID
    vendor_name: str
    items: List[AvailableDepositItem]
    total_available: float


# ============================================
# Vendor Deposits List for Vendor
# ============================================

class VendorDepositsForVendorResponse(BaseModel):
    vendor_id: UUID
    vendor_name: str
    items: List[VendorDepositResponse]
    total_deposits: float
    total_applied: float
    total_remaining: float


# ============================================
# Summary
# ============================================

class VendorDepositSummary(BaseModel):
    total_deposits: float
    total_applied: float      # Σ terpakai JURNAL (P-U3), bukan kolom cache
    total_remaining: float    # Σ sisa JURNAL
    deposit_count: int
    pending_count: int        # == belum_diterapkan_count
    draft_count: int = 0
    void_count: int = 0
    belum_diterapkan_count: int = 0
    belum_diterapkan_value: float = 0


# ============================================
# Post/Void Requests
# ============================================

class PostDepositResponse(BaseModel):
    deposit_id: UUID
    deposit_number: str
    status: VendorDepositStatus
    journal_id: UUID
    journal_number: str


class VoidDepositRequest(BaseModel):
    """Alasan batal WAJIB di /void (VOID_REASON_REQUIRED, kembar pelanggan); opsional di skema supaya kekosongan jadi BLOK pratinjau."""
    reason: Optional[str] = Field(None, max_length=500)


class VoidDepositResponse(BaseModel):
    deposit_id: UUID
    deposit_number: str
    status: VendorDepositStatus
    reversal_journal_id: Optional[UUID] = None


# List response
class VendorDepositListResponse(BaseModel):
    items: List[VendorDepositResponse]
    total: int
    has_more: bool = False
    sum_total: Optional[float] = None   # opt-in ?with_totals=true: Σ amount tanpa void atas SEMUA hasil filter
    sum_sisa: Optional[float] = None    # idem: Σ sisa jurnal (terbit, bukan draf/void)


# Forward reference update
VendorDepositDetailResponse.model_rebuild()
