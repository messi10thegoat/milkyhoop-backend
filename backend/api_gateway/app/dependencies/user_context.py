"""
Konteks pengguna per-request untuk router (tenant_id + user_id).

Satu sumber untuk helper `get_user_context` yang sebelumnya disalin di
puluhan router. Nilai `request.state.user` diisi oleh auth middleware;
fungsi di sini hanya membaca dan memvalidasinya.

Dua varian dipertahankan karena perilakunya berbeda dan pemanggil
bergantung pada tipe `user_id`:

- `get_user_context`     : user_id dikembalikan sebagai `uuid.UUID`
                            (None bila kosong). Dipakai router yang
                            meneruskan user_id ke kolom UUID asyncpg.
- `get_user_context_str` : user_id dikembalikan apa adanya (str/None)
                            seperti yang ditaruh middleware.

Teks pesan 401 dan kunci dict dijaga persis sama dengan salinan lama.
Varian router yang memakai pesan/kunci lain tidak diarahkan ke sini
(lihat docs/cloud/LAPORAN-router-boilerplate.md).
"""
from uuid import UUID

from fastapi import HTTPException, Request

PESAN_BELUM_MASUK = "Silakan masuk terlebih dahulu."
PESAN_SESI_TIDAK_VALID = "Sesi tidak valid, silakan masuk lagi."


def _user_dari_request(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail=PESAN_BELUM_MASUK)
    return request.state.user


def get_user_context(request: Request) -> dict:
    """Konteks pengguna dengan user_id bertipe UUID (None bila kosong)."""
    user = _user_dari_request(request)
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail=PESAN_SESI_TIDAK_VALID)

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


def get_user_context_str(request: Request) -> dict:
    """Konteks pengguna dengan user_id apa adanya dari middleware (str/None)."""
    user = _user_dari_request(request)
    tenant_id = user.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail=PESAN_SESI_TIDAK_VALID)

    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}
