"""
Suppliers Router - Autocomplete & Search
Source: TransaksiHarian.vendor_name (suppliers that were used)

Round 21: Added connection pooling for better latency
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncpg

logger = logging.getLogger(__name__)
router = APIRouter()

# Round 21: Global connection pool for better performance
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create database connection pool"""
    global _pool
    if _pool is None:
        logger.info("Creating database connection pool...")
        _pool = await asyncpg.create_pool(
            host="postgres",  # Docker service name
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="milkydb",
            min_size=2,   # Keep 2 connections ready
            max_size=10,  # Max 10 concurrent connections
            command_timeout=30,  # Query timeout
        )
        logger.info("Database connection pool created")
    return _pool


# Legacy helper for backward compatibility
async def get_db_connection():
    """Get database connection from pool"""
    pool = await get_pool()
    return await pool.acquire()


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

    Round 21: Uses connection pooling for faster response times.
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Round 21: Use connection pool for better performance
        pool = await get_pool()

        async with pool.acquire() as conn:
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

    Round 21: Uses connection pooling for faster response times.
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        # Round 21: Use connection pool for better performance
        pool = await get_pool()

        async with pool.acquire() as conn:
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Supplier search error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "suppliers_router"}
