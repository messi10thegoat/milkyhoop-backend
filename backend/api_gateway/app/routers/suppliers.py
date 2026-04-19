"""
Suppliers Router - Autocomplete & Search
Source: vendors table (proper master data)

Migrated 2026-02-13: Previously read from transaksi_harian.nama_pihak (legacy POS).
Now reads from the canonical vendors table which is the proper master data source.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List
import logging
import asyncpg


logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


class SupplierSuggestion(BaseModel):
    name: str
    usage_count: int


class SupplierSearchResponse(BaseModel):
    suggestions: List[SupplierSuggestion]


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id}


@router.get("/all")
async def get_all_suppliers(
    request: Request,
    limit: int = Query(500, ge=1, le=1000),
):
    """
    Fetch ALL active vendors/suppliers for client-side filtering.
    Source: vendors table (proper master data).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    v.name,
                    v.contact_person as contact,
                    v.phone,
                    (
                        SELECT COUNT(*)
                        FROM bills b
                        WHERE b.vendor_id = v.id
                          AND b.tenant_id = v.tenant_id
                    ) as usage_count
                FROM vendors v
                WHERE v.tenant_id = $1
                  AND v.is_active = true
                ORDER BY v.name ASC
                LIMIT $2
                """,
                ctx["tenant_id"],
                limit,
            )

            results = [
                {"name": row["name"], "contact": row["contact"] or row["phone"] or None}
                for row in rows
            ]

            logger.info(
                f"Suppliers /all: tenant={ctx[tenant_id]}, returned={len(results)}"
            )
            return results

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Suppliers /all error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch suppliers")


@router.get("/search", response_model=SupplierSearchResponse)
async def search_suppliers(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Search vendors/suppliers by name (autocomplete).
    Source: vendors table (proper master data).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    v.name,
                    (
                        SELECT COUNT(*)
                        FROM bills b
                        WHERE b.vendor_id = v.id
                          AND b.tenant_id = v.tenant_id
                    ) as usage_count
                FROM vendors v
                WHERE v.tenant_id = $1
                  AND v.is_active = true
                  AND LOWER(v.name) LIKE LOWER($2)
                ORDER BY usage_count DESC, v.name ASC
                LIMIT $3
                """,
                ctx["tenant_id"],
                f"%{q}%",
                limit,
            )

            suggestions = [
                SupplierSuggestion(
                    name=row["name"], usage_count=int(row["usage_count"])
                )
                for row in rows
            ]

            logger.info(
                f"Supplier search: q={q}, tenant={ctx[tenant_id]}, found={len(suggestions)}"
            )
            return SupplierSearchResponse(suggestions=suggestions)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Supplier search error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "suppliers_router",
        "source": "vendors_table",
    }
