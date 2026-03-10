"""
PKP Settings Router — Tenant PKP configuration management.

GET/PATCH endpoints for managing PKP identity (NPWP, NITKU, etc.)
stored in the tax_info table.
"""

from fastapi import APIRouter, HTTPException, Request
from typing import Optional
import logging
import asyncpg

from ..schemas.pkp_settings import PKPSettingsResponse, PKPSettingsUpdate
from ..config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        db_config = settings.get_db_config()
        _pool = await asyncpg.create_pool(
            **db_config, min_size=2, max_size=10, command_timeout=30
        )
    return _pool


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


# PKP-related columns in tax_info
PKP_COLUMNS = [
    "is_pkp", "npwp_pkp", "npwp_pkp_15", "nitku", "nama_pkp",
    "alamat_pkp", "default_kode_transaksi", "negara", "status_wp", "tahun_terdaftar"
]

PKP_SELECT = ", ".join(PKP_COLUMNS)


@router.get("", response_model=PKPSettingsResponse)
async def get_pkp_settings(request: Request):
    """Get PKP settings for current tenant."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {PKP_SELECT} FROM tax_info WHERE tenant_id = $1 LIMIT 1",
            ctx["tenant_id"]
        )

        if row is None:
            return PKPSettingsResponse()

        data = dict(row)
        # Handle None for fields with defaults
        if data.get("default_kode_transaksi") is None:
            data["default_kode_transaksi"] = "01"
        if data.get("negara") is None:
            data["negara"] = "IDN"

        return PKPSettingsResponse(**data)


@router.patch("", response_model=PKPSettingsResponse)
async def update_pkp_settings(request: Request, payload: PKPSettingsUpdate):
    """Update PKP settings for current tenant. Partial update — only provided fields are changed."""
    ctx = get_user_context(request)
    pool = await get_pool()

    update_fields = payload.dict(exclude_unset=True)
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM tax_info WHERE tenant_id = $1)",
            ctx["tenant_id"]
        )

        if not exists:
            # INSERT new row with provided fields
            columns = ["id", "tenant_id", "periode"] + list(update_fields.keys())
            values = [ctx["tenant_id"], ctx["tenant_id"], "000000"] + list(update_fields.values())
            placeholders = [f"${i+1}" for i in range(len(values))]

            await conn.execute(
                f"INSERT INTO tax_info ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
                *values
            )
            logger.info(f"Created tax_info row for tenant {ctx['tenant_id']}")
        else:
            # UPDATE only provided fields
            set_clauses = []
            values = []
            for i, (k, v) in enumerate(update_fields.items(), 1):
                set_clauses.append(f"{k} = ${i}")
                values.append(v)
            values.append(ctx["tenant_id"])

            await conn.execute(
                f"UPDATE tax_info SET {', '.join(set_clauses)}, updated_at = now() "
                f"WHERE tenant_id = ${len(values)}",
                *values
            )

        # Return updated state
        row = await conn.fetchrow(
            f"SELECT {PKP_SELECT} FROM tax_info WHERE tenant_id = $1",
            ctx["tenant_id"]
        )

        data = dict(row)
        if data.get("default_kode_transaksi") is None:
            data["default_kode_transaksi"] = "01"
        if data.get("negara") is None:
            data["negara"] = "IDN"

        return PKPSettingsResponse(**data)
