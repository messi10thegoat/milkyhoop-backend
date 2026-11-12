"""Skema Bank Feed (BF-1a). Respons memakai Dict[str, Any] untuk `data` (bentuk dikunci penentu + uji), tetapi DIDEKLARASIKAN supaya FastAPI tak
membuang medan diam-diam; permintaan bersekema (bukan body mentah)."""
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ImporMutasiRequest(BaseModel):
    document_id: UUID = Field(..., description="Berkas dari POST /api/documents/upload (csv); diunggah <= 24 jam oleh pengguna yang sama")
    peta_kolom: Optional[Dict[str, Any]] = Field(None, description="CSV generik: {tanggal, keterangan, jumlah|debit|kredit, arah?, saldo?} indeks/nama kolom")
    hash_isi: Optional[str] = Field(None, description="sha256 isi berkas dari pratinjau; beda saat simpan -> 409 BERKAS_BERUBAH")


class PratinjauImporResponse(BaseModel):
    success: bool = True
    data: Dict[str, Any]


class ImporMutasiResponse(BaseModel):
    success: bool = True
    message: str
    data: Dict[str, Any]


class DaftarMutasiResponse(BaseModel):
    success: bool = True
    data: List[Dict[str, Any]]
    cacah: Dict[str, int]
    total: Dict[str, Any]
    has_more: bool


class CalonMutasiResponse(BaseModel):
    success: bool = True
    data: List[Dict[str, Any]]
    jumlah: int


class AlokasiCocok(BaseModel):
    bank_transaction_id: UUID
    nominal: Decimal = Field(..., gt=0, description="Bagian nominal dokumen yang dialokasikan ke baris ini")


class CocokkanRequest(BaseModel):
    bank_transaction_ids: List[UUID] = Field(..., min_length=1, description="Dokumen (bank_transactions) yang dicocokkan; 1 sampai 4")
    alokasi: Optional[List[AlokasiCocok]] = Field(None, description="Opsional; bawaan = nominal penuh/sisa dokumen")
    versi: Optional[str] = Field(None, max_length=64, description="`payload.versi` dari pratinjau; beda saat simpan -> 409 RENCANA_BERUBAH")


class KecualikanRequest(BaseModel):
    alasan: Literal["duplikat", "bukan_transaksi", "lain"]
    catatan: Optional[str] = Field(None, max_length=300, description="Wajib (>= 3 huruf) bila alasan = lain")
    versi: Optional[str] = Field(None, max_length=64)


class LepaskanRequest(BaseModel):
    versi: Optional[str] = Field(None, max_length=64)


class PratinjauAksiResponse(BaseModel):
    success: bool = True
    data: Dict[str, Any]


class AksiMutasiResponse(BaseModel):
    success: bool = True
    message: str
    data: Dict[str, Any]


class RiwayatMutasiResponse(BaseModel):
    success: bool = True
    data: List[Dict[str, Any]]


class TakDiBankResponse(BaseModel):
    success: bool = True
    rentang_berkas: Optional[Dict[str, str]] = None
    dinilai: Optional[Dict[str, str]] = None
    data: List[Dict[str, Any]]
    total: Dict[str, Any]
    catatan: List[str]
