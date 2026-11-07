"""SATU bentuk aksi_tersedia untuk semua modul standar SO (putusan MASTER 7 Nov 2026; Barang, Jurnal, Biaya t2):
{kunci_aksi: {"tersedia": bool, "alasan": str | None}} -- kunci = nama aksi, urutan JSON = urutan tampil; alasan = kalimat
server bila tak tersedia (None bila tersedia). Label tombol milik resep FE, bukan server. Penjaga AST:
tests/unit/test_aksi_tersedia_satu_bentuk.py (setiap medan skema bernama aksi_tersedia wajib bertipe AksiTersediaPeta)."""
from typing import Dict, Optional

from pydantic import BaseModel


class AksiTersedia(BaseModel):
    tersedia: bool
    alasan: Optional[str] = None


AksiTersediaPeta = Dict[str, AksiTersedia]


def aksi(tersedia: bool, alasan_bila_tidak: Optional[str]) -> dict:
    """Satu entri: alasan None bila tersedia (alasan hanya untuk aksi yang tak bisa dijalankan)."""
    return {"tersedia": bool(tersedia), "alasan": None if tersedia else alasan_bila_tidak}
