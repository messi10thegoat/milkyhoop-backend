"""
PKP Settings Schemas — Pydantic models for PKP configuration.
"""

from pydantic import BaseModel, validator
from typing import Optional
import re


class PKPSettingsResponse(BaseModel):
    is_pkp: bool = False
    npwp_pkp: Optional[str] = None
    npwp_pkp_15: Optional[str] = None
    nitku: Optional[str] = None
    nama_pkp: Optional[str] = None
    alamat_pkp: Optional[str] = None
    default_kode_transaksi: str = "01"
    negara: str = "IDN"
    status_wp: Optional[str] = None
    tahun_terdaftar: Optional[int] = None


class PKPSettingsUpdate(BaseModel):
    is_pkp: Optional[bool] = None
    npwp_pkp: Optional[str] = None
    npwp_pkp_15: Optional[str] = None
    nitku: Optional[str] = None
    nama_pkp: Optional[str] = None
    alamat_pkp: Optional[str] = None
    default_kode_transaksi: Optional[str] = None
    negara: Optional[str] = None

    @validator('npwp_pkp')
    def validate_npwp(cls, v):
        if v is not None and not re.match(r'^[0-9]{15,16}$', v):
            raise ValueError('NPWP harus 15 atau 16 digit angka')
        return v

    @validator('npwp_pkp_15')
    def validate_npwp_15(cls, v):
        if v is not None and not re.match(r'^[0-9]{15}$', v):
            raise ValueError('NPWP 15 digit harus exactly 15 digit angka')
        return v

    @validator('nitku')
    def validate_nitku(cls, v):
        if v is not None and not re.match(r'^[0-9]{22}$', v):
            raise ValueError('NITKU harus exactly 22 digit angka')
        return v

    @validator('default_kode_transaksi')
    def validate_kode_transaksi(cls, v):
        valid = ('01', '02', '03', '04', '05', '06', '07', '08', '09')
        if v is not None and v not in valid:
            raise ValueError(f'Kode transaksi harus salah satu dari: {valid}')
        return v
