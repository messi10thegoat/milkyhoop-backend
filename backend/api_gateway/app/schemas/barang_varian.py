"""Skema barang MATRIKS / varian (BUG-159 B1, 12 Nov 2026). Daftar dimensi sengaja TIDAK dibatasi di skema: batas + pesan ada di
services/barang_varian.rakit (satu bentuk galat VARIAN_TAK_VALID, bukan format galat bawaan pydantic)."""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DimensiVarian(BaseModel):
    nama: str = Field(..., description="Nama dimensi, mis. ukuran / warna (kunci `ukuran`/`size` dibaca pencarian barang chat)")
    nilai: List[str] = Field(..., description="Nilai dimensi, mis. S, M, L")


class BuatMatriksRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Nama barang induk")
    base_unit: str = Field(..., min_length=1, max_length=50)
    kategori: Optional[str] = Field(None, max_length=100)
    deskripsi: Optional[str] = None
    sales_price: Optional[float] = Field(None, ge=0, description="Harga jual awal semua varian (ubah per varian lewat PATCH /api/items/{id})")
    purchase_price: Optional[float] = Field(None, ge=0)
    dimensi: List[DimensiVarian]


class TambahVarianRequest(BaseModel):
    dimensi: List[DimensiVarian]


class RencanaVarian(BaseModel):
    name: str
    atribut: Dict[str, str]


class BlokVarian(BaseModel):
    code: str
    message: str
    name: Optional[str] = None


class DataPratinjau(BaseModel):
    ok: bool
    induk: Dict[str, str]
    varian: List[RencanaVarian]
    blocks: List[BlokVarian]
    jumlah: int


class PratinjauResponse(BaseModel):
    success: bool = True
    data: DataPratinjau


class VarianDibuat(BaseModel):
    id: str
    name: str
    atribut: Dict[str, str]


class HasilBuat(BaseModel):
    id: str
    name: str
    varian: List[VarianDibuat]


class BuatMatriksResponse(BaseModel):
    success: bool = True
    message: str
    data: HasilBuat


class MatriksRingkas(BaseModel):
    id: str
    name: str
    item_code: Optional[str] = None
    kategori: Optional[str] = None
    base_unit: Optional[str] = None
    sales_price: Optional[float] = None
    purchase_price: Optional[float] = None
    jumlah_varian: int = 0


class DaftarMatriksResponse(BaseModel):
    success: bool = True
    items: List[MatriksRingkas]
    total: int
    has_more: bool


class VarianDetail(BaseModel):
    id: str
    name: str
    atribut: Dict[str, str]
    item_code: Optional[str] = None
    sku: Optional[str] = None
    sales_price: Optional[float] = None
    purchase_price: Optional[float] = None
    status: Optional[str] = None


class DetailMatriks(MatriksRingkas):
    varian: List[VarianDetail]


class DetailMatriksResponse(BaseModel):
    success: bool = True
    data: DetailMatriks
