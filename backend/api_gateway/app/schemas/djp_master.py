"""
DJP Master Data Schemas — Pydantic models for DJP reference data.
"""

from pydantic import BaseModel
from typing import List, Optional


class DJPKodeBarangJasa(BaseModel):
    id: str
    kode: str
    nama: str
    jenis: str


class DJPSatuanUkur(BaseModel):
    id: str
    kode: str
    nama: str


class DJPKodeTransaksi(BaseModel):
    id: str
    kode: str
    nama: str
    deskripsi: Optional[str] = None
    requires_cap_fasilitas: bool
    requires_keterangan: bool
    uses_dpp_nilai_lain: bool


class DJPKodeBarangJasaListResponse(BaseModel):
    data: List[DJPKodeBarangJasa]
    total: int


class DJPSatuanUkurListResponse(BaseModel):
    data: List[DJPSatuanUkur]
    total: int


class DJPKodeTransaksiListResponse(BaseModel):
    data: List[DJPKodeTransaksi]
    total: int
