"""
File reference (`file_ref`) -> kunci objek MinIO unggahan chat.

`file_ref` = rujukan OPAK yang tampil di teks chat & konteks LLM
(`[Attached: ..., file_ref=chat_upload:<sha256><ext>]`), menyembunyikan
lokasi penyimpanan dari LLM.

Unit U2 (24 Sep 2026): dulu dipetakan ke path DISK
/tmp/milkyhoop_uploads/<tenant>/chat/<sha><ext> (tembolok sementara U1,
hilang tiap recreate). Kini dipetakan ke kunci MinIO
`<tenant>/uploads/chat/<sha256><ext>` (utils/chat_file_path) dan isinya
dibaca dari MinIO -- TANPA disk. Tenant SELALU dari konteks pemanggil, bukan
dari file_ref, jadi file_ref tak bisa menunjuk objek tenant lain.
"""
import re
from typing import Optional

from .chat_file_path import ambil_objek_unggahan, kunci_sah_milik_tenant

_PREFIX = "chat_upload"
_HASH_EXT = re.compile(r"^[a-f0-9]+\.[a-z0-9]+$")


def kunci_file_ref(file_ref: str, tenant_id: str) -> Optional[str]:
    """`chat_upload:<sha><ext>` -> `<tenant>/uploads/chat/<sha><ext>`, atau
    None bila bentuk salah / tak lolos `kunci_sah_milik_tenant` (hash 64 hex,
    ext allowlist). Murni -- tak menyentuh storage."""
    if not isinstance(file_ref, str) or ":" not in file_ref:
        return None
    prefix, hash_ext = file_ref.split(":", 1)
    if prefix != _PREFIX or not _HASH_EXT.match(hash_ext):
        return None
    kunci = f"{tenant_id}/uploads/chat/{hash_ext}"
    return kunci if kunci_sah_milik_tenant(tenant_id, kunci) else None


async def baca_file_ref(storage, file_ref: str, tenant_id: str) -> Optional[bytes]:
    """Isi berkas untuk file_ref dari MinIO. None = bentuk salah atau objek
    tak ada. Galat storage lain dilempar (lihat ambil_objek_unggahan)."""
    kunci = kunci_file_ref(file_ref, tenant_id)
    if kunci is None:
        return None
    return await ambil_objek_unggahan(storage, tenant_id, kunci)
