"""Resolusi aman kunci berkas unggahan lokal (GET /api/v3/chat/files/...).

Kunci yang sah berbentuk tepat ``<tenant_id>/<subdir>/<sha256><ext>`` --
begitulah semua penulis (_save_chat_attachments, uploads.py,
document_intake, chat_document_bridge) menamai berkasnya. Apa pun di luar
bentuk itu ditolak, lalu path hasil ``realpath`` wajib berada TEPAT di dalam
direktori subdir milik tenant pemanggil (menutup segmen titik, symlink
keluar, dan kunci milik tenant lain).

Pemanggil menjawab 404 untuk semua penolakan, supaya tidak menjadi oracle
keberadaan berkas.
"""

import os
import re
from typing import Optional, Tuple

SUBDIR_SAH = frozenset({"chat", "documents", "forms"})
_NAMA_SAH = re.compile(r"^[0-9a-f]{64}(\.[a-z0-9]{1,10})?$")

# Hanya tipe ini yang boleh tampil inline; selain itu diunduh sebagai lampiran.
TIPE_INLINE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def resolve_berkas_tenant(
    base_dir: str, tenant_id: str, storage_key: str
) -> Optional[str]:
    """Path absolut berkas milik tenant, atau None bila kunci tak sah/tak ada."""
    if not tenant_id or tenant_id in (".", "..") or "/" in tenant_id:
        return None
    bagian = storage_key.split("/")
    if len(bagian) != 3:
        return None
    tenant_bagian, subdir, nama = bagian
    if tenant_bagian != tenant_id or subdir not in SUBDIR_SAH:
        return None
    if not _NAMA_SAH.match(nama):
        return None
    akar = os.path.realpath(os.path.join(base_dir, tenant_id, subdir))
    nyata = os.path.realpath(os.path.join(akar, nama))
    if os.path.dirname(nyata) != akar:
        return None
    if not os.path.isfile(nyata):
        return None
    return nyata


def tipe_sajian(path: str) -> Tuple[str, bool]:
    """(media_type, inline?) -- tipe di luar TIPE_INLINE diunduh, bukan dirender."""
    ext = os.path.splitext(path)[1].lower()
    if ext in TIPE_INLINE:
        return TIPE_INLINE[ext], True
    return "application/octet-stream", False
