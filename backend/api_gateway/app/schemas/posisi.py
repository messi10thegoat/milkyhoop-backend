"""position_text SERAGAM (standar SO v3, celah S peta kesiapan 7 Nov 2026): satu medan teks posisi di baris daftar + detail semua modul.
Mixin DIDEKLARASIKAN di response_model (pola BUG-097: medan tak dideklarasikan dibuang diam-diam).
- PosisiDariStatus: modul yang penentunya sudah status_text (NK, ND, Pelanggan, Vendor) -> position_text = status_text (alias aditif,
  nilai SAMA, satu penentu; status_text tetap untuk FE lama).
- PosisiTerima: Pembayaran Masuk -> penerimaan_daftar.posisi_terima(status, alokasi_teks) (cermin posisi_bayar BP)."""
from typing import Optional

from pydantic import BaseModel, model_validator


class PosisiDariStatus(BaseModel):
    position_text: Optional[str] = None

    @model_validator(mode="after")
    def _posisi_dari_status(self):
        if self.position_text is None:
            self.position_text = getattr(self, "status_text", None)
        return self


class PosisiTerima(BaseModel):
    position_text: Optional[str] = None

    @model_validator(mode="after")
    def _posisi_terima(self):
        if self.position_text is None:
            from ..services.penerimaan_daftar import posisi_terima
            self.position_text = posisi_terima(getattr(self, "status", None), getattr(self, "alokasi_teks", None))
        return self
