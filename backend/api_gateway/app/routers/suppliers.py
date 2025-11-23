"""
Suppliers Router - Autocomplete & Search
Source: TransaksiHarian.vendor_name (suppliers that were used)
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List
import logging
import asyncpg

logger = logging.getLogger(__name__)
router = APIRouter()


class SupplierSuggestion(BaseModel):
    name: str
    usage_count: int


class SupplierSearchResponse(BaseModel):
    suggestions: List[SupplierSuggestion]


@router.get("/all")
async def get_all_suppliers(
    request: Request,
    limit: int = Query(500, ge=1, le=1000)
):
    """
    Fetch ALL suppliers for client-side filtering (instant autocomplete).
    Returns suppliers from transaksi_harian.nama_pihak ordered by usage frequency.

    This endpoint is designed for prefetching - frontend loads all suppliers
    once on mount, then filters locally with Fuse.js for instant results.
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Connect to database
        conn = await asyncpg.connect(
            host="db.ltrqrejrkbusvmknpnwb.supabase.co",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="postgres"
        )

        try:
            # Query ALL distinct supplier names with usage count
            query = """
                SELECT
                    nama_pihak as name,
                    MAX(kontak_pihak) as contact,
                    COUNT(*) as usage_count
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND nama_pihak IS NOT NULL
                  AND nama_pihak != ''
                GROUP BY nama_pihak
                ORDER BY usage_count DESC, nama_pihak ASC
                LIMIT $2
            """

            rows = await conn.fetch(query, tenant_id, limit)

            results = [
                {
                    "name": row['name'],
                    "contact": row['contact'] or None
                }
                for row in rows
            ]

            logger.info(f"Suppliers /all: tenant={tenant_id}, returned={len(results)}")

            return results

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Suppliers /all error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch suppliers")


@router.get("/search", response_model=SupplierSearchResponse)
async def search_suppliers(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Search suppliers by name (autocomplete)

    Source: TransaksiHarian.vendor_name (suppliers that were used before)
    Returns suppliers ordered by usage frequency
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Connect to database
        conn = await asyncpg.connect(
            host="db.ltrqrejrkbusvmknpnwb.supabase.co",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="postgres"
        )

        try:
            # Query distinct vendor/supplier names (nama_pihak) with usage count
            query = """
                SELECT
                    nama_pihak as name,
                    COUNT(*) as usage_count
                FROM public.transaksi_harian
                WHERE tenant_id = $1
                  AND nama_pihak IS NOT NULL
                  AND nama_pihak != ''
                  AND LOWER(nama_pihak) LIKE LOWER($2)
                GROUP BY nama_pihak
                ORDER BY usage_count DESC, nama_pihak ASC
                LIMIT $3
            """

            search_pattern = f"%{q}%"
            rows = await conn.fetch(query, tenant_id, search_pattern, limit)

            suggestions = [
                SupplierSuggestion(
                    name=row['name'],
                    usage_count=int(row['usage_count'])
                )
                for row in rows
            ]

            logger.info(f"Supplier search: q='{q}', tenant={tenant_id}, found={len(suggestions)}")

            return SupplierSearchResponse(suggestions=suggestions)

        finally:
            await conn.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Supplier search error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "suppliers_router"}
