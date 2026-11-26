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


from .aksi_tersedia import AksiTersediaPeta  # noqa: E402  satu bentuk (penjaga test_aksi_tersedia_satu_bentuk)


# ---- U-V1 (26 Nov, flag varian_v1): GET /api/matrix-items/{id}/variants
class AtributVarian(BaseModel):
    nama: str
    nilai: str


class BarisVarian(BaseModel):
    id: str
    nama: str
    item_code: Optional[str] = None
    sku: Optional[str] = None
    atribut: List[AtributVarian]
    atribut_text: str
    status: str = Field(..., description="aktif | nonaktif")
    status_text: str
    stok: float
    stok_text: Optional[str] = Field(None, description="None bila stok barang tak dilacak / nonaktif")
    stok_status: Optional[str] = Field(None, description="ada | habis | minus; None bila tak dilacak")
    harga_jual_text: str
    harga_beli_text: str
    aksi_tersedia: AksiTersediaPeta = Field(..., description="aksi barang biasa; jalankan lewat /api/items/{id}")


class RingkasanVarian(BaseModel):
    jumlah_varian: int
    jumlah_varian_text: str
    total_stok_text: str
    jumlah_ada_stok_text: str
    jumlah_habis_text: str


class NilaiSaring(BaseModel):
    nilai: str
    jumlah: int


class SaringAtribut(BaseModel):
    nama: str
    nilai: List[NilaiSaring]


class IndukRingkas(BaseModel):
    id: str
    nama: str


class DaftarVarianResponse(BaseModel):
    success: bool = True
    induk: IndukRingkas
    data: List[BarisVarian]
    total: int
    has_more: bool
    ringkasan: RingkasanVarian = Field(..., description="dihitung dari SEMUA varian tersaring (bukan satu halaman)")
    filter_atribut: List[SaringAtribut] = Field(..., description="dari SEMUA varian induk; jumlah = banyak varian per nilai")
