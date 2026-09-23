"""
Status PKP tenant -- "Tenant".is_pkp. SATU-SATUNYA jalur tulis bendera ini di kode.

Status PKP (boleh memungut PPN) adalah STATUS PAJAK tenant, bukan modul e-Faktur. Dulu satu-satunya
tempat menyetelnya = Settings > PKP (pkp_settings.py), yang (a) menulis tax_info.is_pkp -- tabel yang
tak ada di produksi, BUKAN "Tenant".is_pkp yang dibaca penjaga PPN; dan (b) kini 409 oleh penjaga
modul e-Faktur (V293). Akibatnya tak ada jalan sah mengoreksi tenant yang salah bendera (grapgrap:
non-PKP menurut pemilik, tercatat PKP karena DEFAULT true V154).

Tulis = PEMILIK saja, gagal-TERTUTUP (tanpa business_role_code -> 403). Sebelum/sesudah dicatat.
NPWP/NITKU/identitas e-Faktur tetap di pkp_settings (tetap dijaga penjaga modul).
"""
import logging

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


class PkpStatusUpdate(BaseModel):
    is_pkp: bool


async def get_pool() -> asyncpg.Pool:
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


@router.get("")
async def get_pkp_status(request: Request):
    user = _user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        is_pkp = await conn.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', user["tenant_id"])
    if is_pkp is None:
        raise HTTPException(status_code=404, detail="Tenant tidak ditemukan")
    return {"success": True, "data": {"is_pkp": is_pkp}}


@router.patch("")
async def update_pkp_status(request: Request, body: PkpStatusUpdate):
    user = _user(request)
    # Gagal-TERTUTUP: peran tak diketahui = ditolak (pola "if role and role not in ..." gagal-terbuka).
    if user.get("business_role_code") != "OWNER":
        raise HTTPException(status_code=403, detail="Hanya pemilik usaha yang dapat mengubah status PKP.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            sebelum = await conn.fetchval(
                'SELECT is_pkp FROM "Tenant" WHERE id = $1 FOR UPDATE', user["tenant_id"]
            )
            if sebelum is None:
                raise HTTPException(status_code=404, detail="Tenant tidak ditemukan")
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = $2, updated_at = NOW() WHERE id = $1',
                user["tenant_id"],
                body.is_pkp,
            )
            sesudah = await conn.fetchval('SELECT is_pkp FROM "Tenant" WHERE id = $1', user["tenant_id"])
    logger.warning(
        "Status PKP diubah: tenant=%s user=%s is_pkp %s -> %s",
        user["tenant_id"], user.get("user_id"), sebelum, sesudah,
    )
    return {"success": True, "data": {"is_pkp": sesudah, "sebelum": sebelum}}
