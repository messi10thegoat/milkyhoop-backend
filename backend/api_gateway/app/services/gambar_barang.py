"""Unggah GAMBAR barang (28 Nov 2026; GO pemilik via MASTER; tanpa migrasi: kolom products.image_url sudah ada). Flag gelap `gambar_barang_v1`.
Keamanan: JPEG/PNG/WebP saja, ditentukan dari TANDA TANGAN BYTE (bukan nama/Content-Type klien); maks 2 MB; nama acak sha256 isi; disimpan di
MinIO <tenant>/barang/ (bukan disk kontainer, tak pernah dieksekusi/dirender server). image_url tersimpan = "s3:<path>"; keluaran = URL bertanda tangan
berumur 1 jam (url lama non-s3 dikembalikan apa adanya)."""
import hashlib
from typing import Optional

FLAG = "gambar_barang_v1"
MAKS_BYTE = 2 * 1024 * 1024
AWALAN = "s3:"
_TIPE = (("image/jpeg", ".jpg", lambda b: b[:3] == b"\xff\xd8\xff"),
         ("image/png", ".png", lambda b: b[:8] == b"\x89PNG\r\n\x1a\n"),
         ("image/webp", ".webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"))


def tentukan(isi: bytes) -> tuple:
    """Murni. -> (content_type, ext, None) atau (None, None, (status, code, message))."""
    if not isi:
        return None, None, (422, "GAMBAR_KOSONG", "Berkas gambar kosong.")
    if len(isi) > MAKS_BYTE:
        return None, None, (413, "GAMBAR_TERLALU_BESAR", "Ukuran gambar maksimal 2 MB.")
    for ct, ext, cocok in _TIPE:
        if cocok(isi):
            return ct, ext, None
    return None, None, (422, "GAMBAR_TIPE_TIDAK_DIDUKUNG", "Gambar harus berformat JPG, PNG, atau WebP.")


def jalur(tid: str, isi: bytes, ext: str) -> str:
    return f"{tid}/barang/{hashlib.sha256(isi).hexdigest()}{ext}"


async def url_tampil(nilai: Optional[str]) -> Optional[str]:
    if not nilai or not str(nilai).startswith(AWALAN):
        return nilai
    from .storage_service import get_storage_service
    try:
        return await get_storage_service().generate_signed_url(nilai[len(AWALAN):])
    except Exception:
        return None
