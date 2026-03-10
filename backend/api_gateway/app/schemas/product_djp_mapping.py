"""
Product DJP Mapping Schemas — Pydantic models for product-to-DJP code mapping.
"""

from pydantic import BaseModel
from typing import Optional, List


class ProductDJPMappingCreate(BaseModel):
    product_id: str
    djp_kode_barang_jasa_id: str
    djp_satuan_ukur_id: str


class ProductDJPMappingBulk(BaseModel):
    mappings: List[ProductDJPMappingCreate]


class ProductDJPMappingResponse(BaseModel):
    id: str
    product_id: str
    product_name: str
    djp_kode_barang_jasa_id: str
    kode_barang_jasa: str
    nama_barang_jasa: str
    jenis: str
    djp_satuan_ukur_id: str
    kode_satuan: str
    nama_satuan: str


class ProductDJPMappingListResponse(BaseModel):
    data: List[ProductDJPMappingResponse]
    unmapped_count: int
    total: int
