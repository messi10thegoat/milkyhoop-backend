"""Identitas pajak (Coretax) yang dipakai lintas modul master -- V420 (13 Okt 2026).

Pola Coretax / Accurate (tab Pajak) / NetSuite ID localization: identitas pajak disimpan di MASTER (pelanggan, pemasok)
lalu disnapshot ke faktur pajak saat terbit. Kolom DB + CHECK: migrations/V420__identitas_pajak_pelanggan_pemasok.sql.

- IdentitasPajakDasar   : nitku + negara (pelanggan DAN pemasok).
- IdentitasPajakPembeli : + jenis_id + nomor_dokumen (pelanggan; Passport/Other wajib bernomor dokumen).

Pakai sebagai kelas induk skema Create/Update. Untuk PATCH yang hanya mengosongkan nomor_dokumen, handler wajib
mengecek keadaan GABUNGAN badan+DB lewat `tolak_identitas_pajak_tak_lengkap` (validator model hanya melihat badan).
"""
import re
from typing import Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

JENIS_ID_PAJAK = ("TIN", "NIK", "Passport", "Other")
JENIS_ID_BERDOKUMEN = ("Passport", "Other")
PESAN_NITKU = "NITKU harus 22 digit (NPWP 16 digit + 6 digit kode cabang; pusat = 000000)"
PESAN_NEGARA = "Negara = kode ISO 3 huruf (mis. IDN, SGP)"


def kosong_jadi_none(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def normalkan_nitku(v):
    v = kosong_jadi_none(v)
    if v is None:
        return None
    v = re.sub(r"[\s.\-]", "", str(v))
    if not re.fullmatch(r"[0-9]{22}", v):
        raise ValueError(PESAN_NITKU)
    return v


def normalkan_negara(v):
    v = kosong_jadi_none(v)
    if v is None:
        return None
    v = str(v).upper()
    if not re.fullmatch(r"[A-Z]{3}", v):
        raise ValueError(PESAN_NEGARA)
    return v


def tolak_identitas_pajak_tak_lengkap(jenis_id, nomor_dokumen):
    """Passport/Other tanpa nomor dokumen -> 422 bermedan (DB menolaknya juga: customers_nomor_dokumen_wajib)."""
    if jenis_id in JENIS_ID_BERDOKUMEN and not (nomor_dokumen or "").strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "IDENTITAS_PAJAK_TIDAK_LENGKAP", "field": "nomor_dokumen",
                    "message": f"Nomor dokumen wajib diisi untuk jenis ID {jenis_id}"},
        )


class IdentitasPajakDasar(BaseModel):
    nitku: Optional[str] = Field(None, description=PESAN_NITKU)
    negara: Optional[str] = Field(None, description="Kode negara ISO 3166 alpha-3, mis. IDN")

    @field_validator("nitku", mode="before")
    @classmethod
    def _nitku_22_digit(cls, v):
        return normalkan_nitku(v)

    @field_validator("negara", mode="before")
    @classmethod
    def _negara_iso3(cls, v):
        return normalkan_negara(v)


class IdentitasPajakPembeli(IdentitasPajakDasar):
    jenis_id: Optional[Literal["TIN", "NIK", "Passport", "Other"]] = Field(None, description="Jenis ID pembeli (Coretax)")
    nomor_dokumen: Optional[str] = Field(None, max_length=50, description="Nomor paspor/dokumen (wajib bila Passport/Other)")

    @field_validator("jenis_id", "nomor_dokumen", mode="before")
    @classmethod
    def _kosong(cls, v):
        return kosong_jadi_none(v)

    @model_validator(mode="after")
    def _dokumen_wajib(self):
        # Hanya bila jenis_id DIKIRIM di badan ini; PATCH yang hanya mengubah nomor_dokumen dicek handler terhadap DB.
        if "jenis_id" in self.model_fields_set and self.jenis_id in JENIS_ID_BERDOKUMEN and not self.nomor_dokumen:
            raise ValueError(f"Nomor dokumen wajib diisi untuk jenis ID {self.jenis_id}")
        return self
