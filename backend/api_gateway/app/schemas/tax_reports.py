"""
Tax Reports Schemas — Fase 3 Tax Reporting
"""

from pydantic import BaseModel
from typing import List, Optional
from decimal import Decimal


class PPNTransaction(BaseModel):
    journal_number: str
    journal_date: str
    description: str
    source_type: str
    source_id: Optional[str] = None
    amount: Decimal
    dpp: Decimal
    tax_rate: Decimal
    counterpart_name: Optional[str] = None
    document_number: Optional[str] = None


class PPNSection(BaseModel):
    total: Decimal
    count: int
    transactions: List[PPNTransaction]


class PPNCrossCheck(BaseModel):
    document_tax_lines_keluaran: Decimal
    document_tax_lines_masukan: Decimal
    drift_keluaran: Decimal
    drift_masukan: Decimal


class PPNReportResponse(BaseModel):
    success: bool = True
    period: str
    ppn_keluaran: PPNSection
    ppn_masukan: PPNSection
    net_ppn: Decimal
    cross_check: PPNCrossCheck


class PPhTransaction(BaseModel):
    journal_number: str
    journal_date: str
    vendor_name: Optional[str] = None
    vendor_npwp: Optional[str] = None
    document_number: Optional[str] = None
    dpp: Decimal
    rate: Decimal
    pph_amount: Decimal
    source_type: str
    source_id: Optional[str] = None


class PPhByType(BaseModel):
    tax_code: str
    tax_code_id: Optional[str] = None
    rate: Decimal
    total_pph: Decimal
    total_dpp: Decimal
    count: int
    transactions: List[PPhTransaction]


class PPhCrossCheck(BaseModel):
    withholding_records_total: Decimal
    drift: Decimal


class PPhReportResponse(BaseModel):
    success: bool = True
    period: str
    by_type: List[PPhByType]
    grand_total_pph: Decimal
    grand_total_dpp: Decimal
    cross_check: PPhCrossCheck
