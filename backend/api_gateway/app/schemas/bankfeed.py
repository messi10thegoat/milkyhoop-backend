"""Skema Bank Feed (BF-1a). Respons memakai Dict[str, Any] untuk `data` (bentuk dikunci penentu + uji), tetapi DIDEKLARASIKAN supaya FastAPI tak
membuang medan diam-diam; permintaan bersekema (bukan body mentah)."""
from typing import Any, Dict, List, Optional
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
