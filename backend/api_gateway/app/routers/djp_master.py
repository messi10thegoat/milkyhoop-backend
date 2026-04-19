"""
DJP Master Data Router — Read-only endpoints for DJP reference data.

Serves shared (non-RLS) tables: djp_kode_barang_jasa, djp_satuan_ukur, djp_kode_transaksi.
Auth required for consistency but data is not tenant-scoped.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
import logging
import asyncpg


logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    if not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": user.get("tenant_id"), "user_id": user.get("user_id")}


@router.get("/kode-barang-jasa")
async def list_kode_barang_jasa(
    request: Request,
    search: Optional[str] = Query(None, description="Search by nama"),
    jenis: Optional[str] = Query(None, description="Filter: A=barang, B=jasa"),
):
    """List DJP kode barang/jasa. Shared reference data."""
    get_user_context(request)  # auth check
    pool = await get_pool()

    async with pool.acquire() as conn:
        conditions = []
        params = []
        idx = 1

        if search:
            conditions.append(f"nama ILIKE ${idx}")
            params.append(f"%{search}%")
            idx += 1

        if jenis and jenis in ("A", "B"):
            conditions.append(f"jenis = ${idx}")
            params.append(jenis)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = await conn.fetch(
            f"SELECT id, kode, nama, jenis FROM djp_kode_barang_jasa {where} ORDER BY kode",
            *params,
        )

        return {
            "data": [
                {
                    "id": str(r["id"]),
                    "kode": r["kode"],
                    "nama": r["nama"],
                    "jenis": r["jenis"],
                }
                for r in rows
            ],
            "total": len(rows),
        }


@router.get("/satuan-ukur")
async def list_satuan_ukur(
    request: Request,
    search: Optional[str] = Query(None, description="Search by nama"),
):
    """List DJP satuan ukur. Shared reference data."""
    get_user_context(request)  # auth check
    pool = await get_pool()

    async with pool.acquire() as conn:
        if search:
            rows = await conn.fetch(
                "SELECT id, kode, nama FROM djp_satuan_ukur WHERE nama ILIKE $1 ORDER BY kode",
                f"%{search}%",
            )
        else:
            rows = await conn.fetch(
                "SELECT id, kode, nama FROM djp_satuan_ukur ORDER BY kode"
            )

        return {
            "data": [
                {"id": str(r["id"]), "kode": r["kode"], "nama": r["nama"]} for r in rows
            ],
            "total": len(rows),
        }


@router.get("/kode-transaksi")
async def list_kode_transaksi(
    request: Request,
    active_only: bool = Query(True, description="Only show active codes"),
):
    """List DJP kode transaksi with validation rules."""
    get_user_context(request)  # auth check
    pool = await get_pool()

    async with pool.acquire() as conn:
        if active_only:
            rows = await conn.fetch(
                """SELECT id, kode, nama, deskripsi, requires_cap_fasilitas,
                          requires_keterangan, uses_dpp_nilai_lain
                   FROM djp_kode_transaksi WHERE is_active = true ORDER BY kode"""
            )
        else:
            rows = await conn.fetch(
                """SELECT id, kode, nama, deskripsi, requires_cap_fasilitas,
                          requires_keterangan, uses_dpp_nilai_lain
                   FROM djp_kode_transaksi ORDER BY kode"""
            )

        return {
            "data": [
                {
                    "id": str(r["id"]),
                    "kode": r["kode"],
                    "nama": r["nama"],
                    "deskripsi": r["deskripsi"],
                    "requires_cap_fasilitas": r["requires_cap_fasilitas"],
                    "requires_keterangan": r["requires_keterangan"],
                    "uses_dpp_nilai_lain": r["uses_dpp_nilai_lain"],
                }
                for r in rows
            ],
            "total": len(rows),
        }
