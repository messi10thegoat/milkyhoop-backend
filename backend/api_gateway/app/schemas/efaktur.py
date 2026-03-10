from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import datetime


class EfakturPeriodRequest(BaseModel):
    export_type: str
    masa_pajak: str
    tahun_pajak: int

    @validator('export_type')
    def validate_export_type(cls, v):
        if v not in ('keluaran', 'masukan'):
            raise ValueError('export_type harus keluaran atau masukan')
        return v

    @validator('masa_pajak')
    def validate_masa(cls, v):
        if v not in [f"{i:02d}" for i in range(1, 13)]:
            raise ValueError('masa_pajak harus 01-12')
        return v


class ValidationIssue(BaseModel):
    tax_invoice_id: str
    faktur_number: Optional[str] = None
    referensi: Optional[str] = None
    issues: List[str]


class ValidationResponse(BaseModel):
    valid: bool
    total_invoices: int
    valid_count: int
    invalid_count: int
    errors: List[ValidationIssue]
    ready_for_export: bool


class ExportRecord(BaseModel):
    id: str
    export_type: str
    masa_pajak: str
    tahun_pajak: int
    invoice_count: int
    file_name: Optional[str] = None
    exported_at: Optional[datetime] = None
    exported_by: Optional[str] = None
