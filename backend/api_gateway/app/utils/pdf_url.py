"""PDF dokumen `?format=url` = PATH RELATIF GATEWAY (Unit 2, 25 Sep 2026).

KENAPA ADA: `format=url` dulu mengunggah PDF ke MinIO lalu mengembalikan URL
presign (`storage.upload_bytes` -> `generate_signed_url`). Host presign =
`MINIO_PUBLIC_ENDPOINT`, dan sejak port publik ditutup (23 Sep) MinIO hanya
mendengar di 127.0.0.1:9000 -> setiap URL itu MATI dari luar, sementara
setiap panggilan meninggalkan salinan PDF di bucket (`<t>/invoices/<id>.pdf`,
`<t>/quotes/<id>.pdf`) yang tak dibaca siapa pun.

ARAH (pola Unit 1a lampiran): `url` = path relatif ke rute PDF yang sama
dengan `format=inline`. PDF dirender saat diunduh, lewat gateway: izin modul
(permission_middleware) + pagar tenant rute berlaku pada tiap unduhan, bukan
tanda tangan yang bisa diteruskan. Tak ada tulisan ke MinIO, tak ada
kedaluwarsa (`expires_at` = None). Unduh butuh header Bearer (fetchWithAuth),
sama seperti lampiran.
"""
from typing import Optional
from urllib.parse import urlencode

# Penanda unik (dipakai window_item.sh untuk memastikan kode ini terpasang).
PENANDA_PDF_URL = "unit2-pdf-url-lewat-gateway"


def url_pdf_dokumen(modul: str, dokumen_id, template: Optional[str] = None) -> str:
    """Path relatif gateway yang mengembalikan PDF dokumen (format=inline).

    `template` hanya diteruskan bila pemanggil MENGIRIMnya (faktur penjualan):
    tanpa itu, rute memakai setelan tenant saat diunduh.
    """
    q = {"format": "inline"}
    if template:
        q["template"] = template
    return f"/api/{modul}/{dokumen_id}/pdf?{urlencode(q)}"


def respons_pdf_url(modul: str, dokumen_id, filename: str,
                    template: Optional[str] = None) -> dict:
    """Badan respons `format=url` — bentuk lama dipertahankan (url, expires_at,
    filename), `expires_at` kini None karena path gateway tak kedaluwarsa."""
    return {
        "success": True,
        "data": {
            "status": "ok",
            "url": url_pdf_dokumen(modul, dokumen_id, template),
            "expires_at": None,
            "filename": filename,
        },
    }
