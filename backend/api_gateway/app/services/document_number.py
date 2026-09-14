"""Validasi nomor dokumen manual opsional (faktur/penawaran/pesanan).

Kontrak (putusan MASTER): default auto; boleh diisi manual saat membuat; unik per tenant
(dijamin index -> 409 di pemanggil). Format bebas: trim, 1-50 karakter, hanya huruf/angka/-/./,
tanpa spasi (di awal/akhir maupun di tengah).
"""
import re
from typing import Optional

from fastapi import HTTPException

_DOCNUM_RE = re.compile(r"^[A-Za-z0-9./\-]{1,50}$")


def bersihkan_nomor_dokumen_opsional(raw: Optional[str]) -> Optional[str]:
    """None / '' / hanya-spasi -> None (pakai auto-generate). Selain itu: trim lalu validasi;
    invalid -> HTTPException 400. Kembalikan nomor bersih."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) > 50 or not _DOCNUM_RE.match(s):
        raise HTTPException(
            status_code=400,
            detail="Nomor dokumen tidak valid: 1–50 karakter, hanya huruf/angka/-/./ tanpa spasi.",
        )
    return s
