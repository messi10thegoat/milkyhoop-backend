"""
Pydantic schemas for Expenses (Biaya & Pengeluaran) module.

This module defines request and response models for the /api/expenses endpoints.
Supports both single expenses and itemized expenses with multiple accounts.
"""

from pydantic import BaseModel, Field, field_validator
from .aksi_tersedia import AksiTersediaPeta
from typing import Optional, List, Literal, Dict, Any
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal


# =============================================================================
# REQUEST MODELS
# =============================================================================


class ExpenseItemRequest(BaseModel):
    """Single line item for itemized expense."""

    account_id: UUID = Field(..., description="FK to chart_of_accounts")
    account_name: Optional[str] = Field(None, description="Account name for display")
    amount: int = Field(..., ge=0, description="Amount in Rupiah")
    notes: Optional[str] = Field(None, max_length=500, description="Item description")


class CreateExpenseRequest(BaseModel):
    """Request body for creating a new expense."""

    expense_date: date = Field(..., description="Expense date")
    paid_through_id: UUID = Field(..., description="Bank/Cash account used for payment")

    # Single expense mode (for non-itemized)
    account_id: Optional[UUID] = Field(
        None, description="Expense account (required if not itemized)"
    )
    account_name: Optional[str] = Field(None, description="Account name for display")
    amount: Optional[int] = Field(
        None, ge=0, description="Amount (required if not itemized)"
    )

    # Itemized mode
    is_itemized: bool = Field(
        False, description="True if expense has multiple line items"
    )
    line_items: Optional[List[ExpenseItemRequest]] = Field(
        None, description="Line items for itemized expense"
    )

    # Vendor (optional)
    vendor_id: Optional[UUID] = Field(None, description="FK to vendors")
    vendor_name: Optional[str] = Field(None, description="Vendor name for display")

    # Tax (PPN Masukan)
    tax_id: Optional[UUID] = Field(None, description="FK to tax_codes")
    tax_name: Optional[str] = Field(None, description="Tax name (e.g., PPN 11%)")
    tax_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="Tax rate %")

    # PPh withholding
    pph_type: Optional[Literal["PPH_21", "PPH_23", "PPH_4_2"]] = Field(
        None, description="PPh type for withholding"
    )
    pph_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="PPh rate %")

    # Other
    currency: str = Field("IDR", max_length=3, description="Currency code")
    is_billable: bool = Field(False, description="Can be billed to customer")
    billed_to_customer_id: Optional[UUID] = Field(
        None, description="Customer to bill this expense to"
    )
    reference: Optional[str] = Field(
        None, max_length=100, description="Receipt/reference number"
    )
    notes: Optional[str] = Field(None, max_length=500, description="Notes")
    has_receipt: bool = Field(False, description="Has receipt attached")

    # Attachments
    attachment_ids: Optional[List[UUID]] = Field(
        None, max_length=5, description="List of document IDs to attach (max 5)"
    )

    @field_validator("line_items")
    @classmethod
    def validate_line_items(cls, v, info):
        is_itemized = info.data.get("is_itemized", False)
        if is_itemized and (not v or len(v) == 0):
            raise ValueError("At least one line item is required for itemized expense")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v, info):
        is_itemized = info.data.get("is_itemized", False)
        if not is_itemized and (v is None or v <= 0):
            raise ValueError("Amount is required for non-itemized expense")
        return v

    status: Literal["draft", "posted"] = Field(
        "posted",
        description=(
            "draft: disimpan TANPA jurnal dan TANPA efek saldo bank; "
            "posted: langsung terbit (bawaan, menjaga kompatibilitas)."
        ),
    )


class UpdateExpenseRequest(BaseModel):
    """Request body for updating a DRAFT expense.

    Medan keuangan (amount/account/paid_through/line_items) boleh diubah SELAMA draf;
    subtotal/tax/pph/total dihitung ulang di server spt create. Semua Optional (PATCH
    parsial: yang tak dikirim memakai nilai lama).
    """

    expense_date: Optional[date] = None
    paid_through_id: Optional[UUID] = None
    account_id: Optional[UUID] = None
    account_name: Optional[str] = None
    amount: Optional[int] = Field(None, ge=0)
    is_itemized: Optional[bool] = None
    line_items: Optional[List[ExpenseItemRequest]] = None
    vendor_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    tax_id: Optional[UUID] = None
    tax_name: Optional[str] = None
    tax_rate: Optional[Decimal] = Field(None, ge=0, le=100)
    pph_type: Optional[Literal["PPH_21", "PPH_23", "PPH_4_2"]] = None
    pph_rate: Optional[Decimal] = Field(None, ge=0, le=100)
    currency: Optional[str] = Field(None, max_length=3)
    is_billable: Optional[bool] = None
    billed_to_customer_id: Optional[UUID] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    has_receipt: Optional[bool] = None


class VoidExpenseRequest(BaseModel):
    """Request body for voiding an expense."""

    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for voiding"
    )


# =============================================================================
# RESPONSE MODELS - Nested Objects
# =============================================================================


class VendorInfo(BaseModel):
    """Vendor information for expense responses."""

    id: Optional[UUID] = None
    name: Optional[str] = None


class AccountInfo(BaseModel):
    """Account information for expense responses."""

    id: UUID
    name: str


class ExpenseItemResponse(BaseModel):
    """Single line item in expense response."""

    id: UUID
    account_id: UUID
    account_name: Optional[str] = None
    amount: int
    notes: Optional[str] = None
    line_number: int


class ExpenseAttachmentResponse(BaseModel):
    """Attachment in expense response."""

    id: UUID
    file_name: str
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    uploaded_at: datetime


# =============================================================================
# RESPONSE MODELS - Main
# =============================================================================


class ExpenseListItem(BaseModel):
    """Expense item for list responses."""

    id: UUID
    expense_number: str
    expense_date: date
    paid_through_name: Optional[str] = None
    vendor: Optional[VendorInfo] = None
    account_name: Optional[str] = None
    subtotal: int
    tax_amount: int
    total_amount: int
    is_itemized: bool
    status: str
    is_billable: bool
    billed_to_customer_id: Optional[UUID] = None
    billed_to_customer_name: Optional[str] = None
    has_receipt: bool = False
    attachment_count: int = 0
    first_thumbnail_url: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    # pola BUG-097: dua medan ini SUDAH dirakit list_expenses tapi tak dideklarasikan -> dulu dibuang diam-diam (ditangkap penjaga AST)
    billed_invoice_id: Optional[str] = None
    accounting_status: Optional[str] = None
    # Biaya standar SO tahap 1 (services/biaya_daftar): teks/aksi/dokumen SERVER
    aksi: Optional[str] = None
    aksi_semua: List[str] = []
    position_text: Optional[str] = None
    documents: List[Dict[str, str]] = []
    doc_count: int = 0


class ExpenseListResponse(BaseModel):
    """Response for list expenses endpoint."""

    items: List[ExpenseListItem]
    total: int
    has_more: bool
    sum_total: Optional[float] = None  # ?with_totals=true: Σ total_amount baris terfilter tanpa void


class ExpenseDetailResponse(BaseModel):
    """Response for get expense detail endpoint."""

    success: bool = True
    data: Dict[str, Any]
    # Biaya t2: SATU bentuk aksi_tersedia (schemas/aksi_tersedia; penjaga test_aksi_tersedia_satu_bentuk) -- sejajar `data` supaya
    # bertipe (data = Dict bebas tak terlihat penjaga)
    aksi_tersedia: Optional[AksiTersediaPeta] = None


class ExpenseDocAnchor(BaseModel):
    """Biaya t2: jangkar bundel dokumen (services/biaya_riwayat; position_text = biaya_daftar.position_text, SAMA dgn daftar)."""

    kind: Literal["expense"] = "expense"
    id: str
    number: Optional[str] = None
    date: Optional[str] = None
    position_text: str
    total: str
    account_name: Optional[str] = None


class ExpenseDocumentsData(BaseModel):
    anchor: ExpenseDocAnchor
    default: Dict[str, Any]
    groups: List[Dict[str, Any]]


class ExpenseDocumentsResponse(BaseModel):
    success: bool = True
    data: ExpenseDocumentsData


class ExpenseHistoryData(BaseModel):
    """Bentuk SAMA dgn riwayat SO/tagihan (so_riwayat._selesaikan)."""

    expense_id: str
    expense_number: Optional[str] = None
    events: List[Dict[str, Any]]
    total: int
    omitted: List[str]


class ExpenseHistoryResponse(BaseModel):
    success: bool = True
    data: ExpenseHistoryData


class CreateExpenseResponse(BaseModel):
    """Response for create expense endpoint."""

    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class UpdateExpenseResponse(BaseModel):
    """Response for update expense endpoint."""

    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class DeleteExpenseResponse(BaseModel):
    """Response for delete expense endpoint."""

    success: bool
    message: str


class VoidExpenseResponse(BaseModel):
    """Response for void expense endpoint."""

    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


# =============================================================================
# SUMMARY RESPONSE
# =============================================================================


class TopAccount(BaseModel):
    """Top expense account in summary."""

    account_id: UUID
    account_name: Optional[str] = None
    total_amount: int
    count: int


class PaidThroughBawaan(BaseModel):
    """BUG-055: rekening bayar bawaan form biaya (services/rekening_bawaan) -- sumber 'terakhir' | 'utama' | 'kas'."""

    id: UUID
    nama: str
    sumber: Literal["terakhir", "utama", "kas"]


class ExpenseSummaryData(BaseModel):
    """Summary data structure."""

    period: str
    total_count: int
    total_amount: int
    total_tax: int
    vendor_count: int
    billable_count: int
    billable_amount: int
    top_accounts: List[TopAccount]
    paid_through_bawaan: Optional[PaidThroughBawaan] = None
    # Biaya standar SO tahap 1: kartu/pill = predikat SAMA dgn GET /api/expenses ?aksi= (services/biaya_daftar)
    draf_count: int = 0
    draf_value: float = 0
    tanpa_bukti_count: int = 0
    tanpa_bukti_value: float = 0
    bulan_ini_count: int = 0
    bulan_ini_value: float = 0


class ExpenseSummaryResponse(BaseModel):
    """Response for expenses summary endpoint."""

    success: bool = True
    data: ExpenseSummaryData


# =============================================================================
# CALCULATION RESPONSE
# =============================================================================


class ExpenseCalculationResult(BaseModel):
    """Calculated totals for expense preview."""

    subtotal: int = Field(..., description="Sum of line items or single amount")
    tax_amount: int = Field(..., description="PPN Masukan (subtotal * tax_rate / 100)")
    pph_amount: int = Field(..., description="PPh withheld (subtotal * pph_rate / 100)")
    total_amount: int = Field(..., description="Total = subtotal + tax - pph")


class CalculateExpenseResponse(BaseModel):
    """Response for expense calculation preview."""

    success: bool = True
    blocks: List[Dict[str, Any]] = []  # B8: KAS_MINUS bila biaya posted dari kas membuat saldo minus
    calculation: ExpenseCalculationResult


# =============================================================================
# AUTOCOMPLETE RESPONSE
# =============================================================================


class ExpenseAutocompleteItem(BaseModel):
    """Single item for autocomplete."""

    id: UUID
    expense_number: str
    expense_date: date
    total_amount: int
    vendor_name: Optional[str] = None


class ExpenseAutocompleteResponse(BaseModel):
    """Response for expense autocomplete endpoint."""

    items: List[ExpenseAutocompleteItem]
