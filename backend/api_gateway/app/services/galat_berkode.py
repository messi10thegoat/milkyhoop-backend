"""Galat berkode satu bentuk (VENDOR-DUPLIKAT, 1 Nov 2026; putusan MASTER).

Badan respons: {"detail": <kalimat>, "code": <KODE>, "message": <kalimat>, "field": <medan API>}.
- detail TETAP string kalimat: pembaca lama yang hanya membaca detail-string (CW buatPelanggan cepat,
  useDataForm.ts) menampilkan kalimat, bukan "Gagal membuat kontak (HTTP 409)".
- code + field tingkat atas: petaGalatServer FE memetakan kode -> medan (satu pola pelanggan & vendor).
- message: bacaPesanGalat FE (message didahulukan) dan pembaca errorData.message.
Dirender oleh handler main.py; di dalam kode, e.detail (string) dan e.code tetap terbaca seperti HTTPException biasa.
"""
from typing import Optional

from fastapi import HTTPException


class GalatBerkode(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, field: Optional[str] = None, headers: Optional[dict] = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code, self.field = code, field

    def badan(self) -> dict:
        b = {"detail": self.detail, "code": self.code, "message": self.detail}
        if self.field:
            b["field"] = self.field
        return b
