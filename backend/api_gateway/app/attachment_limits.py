"""Satu sumber batas & jenis lampiran (Unit B, 16 Sep 2026).

Sebelumnya tiga jalur unggah punya batas berbeda: hub generik 50 MB / 14 tipe,
endpoint faktur 5 MB / 4 tipe, endpoint uang muka 5 MB / 4 tipe. FE punya 3 batas
(5/10/10 MB). Modul ini menyatukan: 10 MB + daftar tipe acuan (14 tipe hub generik).
Pakai `enforce_attachment_limits(len(content), content_type)` di SEMUA jalur unggah.
"""

from fastapi import HTTPException

ATTACHMENT_MAX_MB = 10
ATTACHMENT_MAX_BYTES = ATTACHMENT_MAX_MB * 1024 * 1024

# Daftar acuan = 14 tipe hub generik documents.py (gambar + PDF + dokumen kantor + arsip).
ATTACHMENT_ALLOWED_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/csv",
        "application/zip",
        "application/x-rar-compressed",
    }
)


def enforce_attachment_limits(content_length: int, content_type: str | None) -> None:
    """Tolak 400 bila tipe tak sah atau ukuran > batas. Pesan seragam di semua jalur.

    Urutan: tipe dulu (pesan menyebut daftar), lalu ukuran.
    """
    if content_type not in ATTACHMENT_ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Jenis berkas {content_type} tidak didukung. Gunakan gambar "
                "(JPEG/PNG/GIF/WebP/SVG), PDF, Word, Excel, teks/CSV, atau ZIP/RAR."
            ),
        )
    if content_length > ATTACHMENT_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Ukuran berkas melebihi batas {ATTACHMENT_MAX_MB} MB.",
        )
