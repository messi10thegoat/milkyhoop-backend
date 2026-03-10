"""
Tax Invoice Schemas — Pydantic models for faktur pajak CRUD.
"""

from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import date, datetime


class TaxInvoiceCreate(BaseModel):
    source_type: str = "sales_invoice"
    source_ids: List[str]
    faktur_date: Optional[date] = None
    kode_transaksi: Optional[str] = None
    email_pembeli: Optional[str] = None

    @validator('source_type')
    def validate_source_type(cls, v):
        if v not in ('sales_invoice', 'bill', 'credit_note', 'vendor_credit'):
            raise ValueError('source_type harus: sales_invoice, bill, atau credit_note')
        return v

    @validator('source_ids')
    def validate_source_ids(cls, v):
        if not v:
            raise ValueError('source_ids tidak boleh kosong')
        return v


class TaxInvoiceBulkCreate(BaseModel):
    direction: str = "keluaran"
    masa_pajak: str
    tahun_pajak: int
    kode_transaksi: Optional[str] = "01"
    source_document_type: Optional[str] = None


class TaxInvoiceStatusUpdate(BaseModel):
    status: str

    @validator('status')
    def validate_status(cls, v):
        allowed = ('exported', 'uploaded', 'approved')
        if v not in allowed:
            raise ValueError(f'Status update hanya: {allowed}. Gunakan endpoint khusus untuk cancel/replace.')
        return v


class TaxInvoiceCancel(BaseModel):
    reason: Optional[str] = None


class TaxInvoiceReplace(BaseModel):
    faktur_date: Optional[date] = None
    reason: Optional[str] = None


class BulkAssignNSFP(BaseModel):
    tax_invoice_ids: Optional[List[str]] = None


class TaxInvoiceItemResponse(BaseModel):
    id: str
    line_number: int
    barang_jasa: str
    kode_barang_jasa: str
    nama_barang_jasa: str
    satuan_ukur: str
    harga_satuan: float
    jumlah: float
    diskon: float
    dpp: float
    dpp_nilai_lain: float
    tarif_ppn: float
    ppn: float
    tarif_ppnbm: float
    ppnbm: float
    harga_total: float


class TaxInvoiceSourceResponse(BaseModel):
    source_type: str
    source_id: str
    invoice_number: Optional[str] = None


class TaxInvoiceListItem(BaseModel):
    id: str
    direction: str
    faktur_number: Optional[str] = None
    faktur_date: str
    masa_pajak: Optional[str] = None
    tahun_pajak: Optional[int] = None
    kode_transaksi: str
    nama_pembeli: str
    npwp_pembeli: Optional[str] = None
    referensi: Optional[str] = None
    dpp: float
    ppn: float
    grand_total: float
    status: str
    created_at: str
    source_invoices: List[str]


class TaxInvoiceDetailResponse(BaseModel):
    id: str
    direction: str
    faktur_number: Optional[str] = None
    faktur_date: str
    masa_pajak: Optional[str] = None
    tahun_pajak: Optional[int] = None
    kode_transaksi: str
    fg_pengganti: int
    replaces_id: Optional[str] = None
    npwp_penjual: Optional[str] = None
    nitku_penjual: Optional[str] = None
    nama_penjual: str
    alamat_penjual: Optional[str] = None
    npwp_pembeli: Optional[str] = None
    nik_pembeli: Optional[str] = None
    jenis_id_pembeli: Optional[str] = None
    negara_pembeli: Optional[str] = None
    nama_pembeli: str
    alamat_pembeli: Optional[str] = None
    email_pembeli: Optional[str] = None
    referensi: Optional[str] = None
    dpp: float
    dpp_nilai_lain: Optional[float] = 0
    ppn: float
    ppnbm: float
    grand_total: float
    status: str
    cancellation_reason: Optional[str] = None
    items: List[TaxInvoiceItemResponse]
    sources: List[TaxInvoiceSourceResponse]
    created_at: str
    retur_of_tax_invoice_id: Optional[str] = None
    retur_of_faktur_number: Optional[str] = None
    source_document_type: Optional[str] = None
