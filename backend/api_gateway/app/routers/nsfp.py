"""
NSFP Range Management Router — CRUD for efaktur_sequences (NSFP ranges).

Manages Nomor Seri Faktur Pajak ranges allocated by DJP.
current_number tracks last assigned number; generate_efaktur_number() handles assignment.
"""

from fastapi import APIRouter, HTTPException, Request
from typing import Optional
import logging
import asyncpg

from ..schemas.nsfp import (
    NSFPRangeCreate, NSFPRangeUpdate, NSFPRangeResponse,
    NSFPRangeListResponse, NSFPUsageResponse,
)
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
    if not user.get("tenant_id"):
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": user.get("tenant_id"), "user_id": user.get("user_id")}


def enrich_range(row) -> dict:
    """Compute derived fields from an efaktur_sequences row."""
    r = dict(row)
    r["id"] = str(r["id"])
    if r.get("created_by"):
        r["created_by"] = str(r["created_by"])
    total = r["range_end"] - r["range_start"] + 1
    used = max(0, r["current_number"] - r["range_start"] + 1)
    remaining = total - used
    r["total"] = total
    r["remaining"] = remaining
    r["usage_percent"] = round((used / total) * 100, 1) if total > 0 else 0.0
    return r


@router.get("", response_model=NSFPRangeListResponse)
async def list_nsfp_ranges(request: Request):
    """List all NSFP ranges for current tenant."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, prefix, range_start, range_end, current_number,
                      is_active, allocated_date, exhausted_at
               FROM efaktur_sequences
               WHERE tenant_id = $1
               ORDER BY created_at DESC""",
            ctx["tenant_id"]
        )

        data = [enrich_range(r) for r in rows]
        return {"data": data, "total": len(data)}


@router.post("", response_model=NSFPRangeResponse, status_code=201)
async def create_nsfp_range(request: Request, payload: NSFPRangeCreate):
    """Create a new NSFP range."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO efaktur_sequences
                   (tenant_id, prefix, range_start, range_end, current_number,
                    is_active, allocated_date, created_by)
               VALUES ($1, $2, $3, $4, $5, true, $6, $7)
               RETURNING id, prefix, range_start, range_end, current_number,
                         is_active, allocated_date, exhausted_at""",
            ctx["tenant_id"],
            payload.prefix,
            payload.range_start,
            payload.range_end,
            payload.range_start - 1,  # so first assignment = range_start
            payload.allocated_date,
            ctx["user_id"],
        )

        return enrich_range(row)


@router.patch("/{range_id}", response_model=NSFPRangeResponse)
async def update_nsfp_range(request: Request, range_id: str, payload: NSFPRangeUpdate):
    """Toggle active status of an NSFP range. Only is_active can be changed."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE efaktur_sequences
               SET is_active = $1
               WHERE id = $2 AND tenant_id = $3
               RETURNING id, prefix, range_start, range_end, current_number,
                         is_active, allocated_date, exhausted_at""",
            payload.is_active,
            range_id,
            ctx["tenant_id"],
        )

        if row is None:
            raise HTTPException(status_code=404, detail="NSFP range tidak ditemukan")

        return enrich_range(row)


@router.get("/usage", response_model=NSFPUsageResponse)
async def get_nsfp_usage(request: Request):
    """Aggregated NSFP usage summary across all ranges for tenant."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 COALESCE(SUM(range_end - range_start + 1), 0)::bigint AS total_allocated,
                 COALESCE(SUM(GREATEST(0, current_number - range_start + 1)), 0)::bigint AS total_used,
                 COUNT(*) FILTER (WHERE is_active = true) AS active_ranges,
                 COUNT(*) FILTER (WHERE is_active = false AND exhausted_at IS NOT NULL) AS exhausted_ranges
               FROM efaktur_sequences
               WHERE tenant_id = $1""",
            ctx["tenant_id"]
        )

        total_allocated = int(row["total_allocated"])
        total_used = int(row["total_used"])
        total_remaining = total_allocated - total_used
        active_ranges = int(row["active_ranges"])
        exhausted_ranges = int(row["exhausted_ranges"])

        warning = None
        if active_ranges == 0 and total_allocated > 0:
            warning = "Tidak ada range NSFP aktif"
        elif active_ranges == 0 and total_allocated == 0:
            warning = "Belum ada range NSFP. Silakan tambahkan range dari DJP."
        elif total_remaining < 10:
            warning = "NSFP hampir habis — sisa kurang dari 10"

        return {
            "total_allocated": total_allocated,
            "total_used": total_used,
            "total_remaining": total_remaining,
            "active_ranges": active_ranges,
            "exhausted_ranges": exhausted_ranges,
            "warning": warning,
        }
