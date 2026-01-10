"""
Members Router - Customer/Member Management for POS
Source: customers table

Round 22: Added connection pooling for better latency
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncpg

logger = logging.getLogger(__name__)
router = APIRouter()

# Round 22: Global connection pool for better performance
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create database connection pool"""
    global _pool
    if _pool is None:
        logger.info("Creating members database connection pool...")
        _pool = await asyncpg.create_pool(
            host="postgres",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="milkydb",
            min_size=2,   # Keep 2 connections ready
            max_size=10,  # Max 10 concurrent connections
            command_timeout=30,
        )
        logger.info("Members database connection pool created")
    return _pool


# Legacy helper for backward compatibility
async def get_db_connection():
    """Get database connection from pool"""
    pool = await get_pool()
    return await pool.acquire()


# ========================================
# Response Models
# ========================================

class MemberItem(BaseModel):
    id: str
    nama: str
    tipe: str
    telepon: Optional[str] = None
    alamat: Optional[str] = None
    email: Optional[str] = None
    nomor_member: Optional[str] = None
    points: int = 0
    points_per_50k: int = 1
    total_transaksi: int = 0
    total_nilai: int = 0
    saldo_hutang: int = 0
    last_transaction_at: Optional[str] = None
    created_at: Optional[str] = None


class MemberSummary(BaseModel):
    total_pelanggan: int = 0
    total_supplier: int = 0
    total_piutang: int = 0
    total_hutang: int = 0


class MemberListResponse(BaseModel):
    members: List[MemberItem]
    total: int
    has_more: bool
    summary: Optional[MemberSummary] = None


class MemberSearchResponse(BaseModel):
    members: List[MemberItem]
    query: str


class AddPointsRequest(BaseModel):
    member_id: str
    transaction_amount: int  # Total transaction amount


class AddPointsResponse(BaseModel):
    success: bool
    member_id: str
    points_added: int
    new_total_points: int


# ========================================
# API Endpoints
# ========================================

@router.get("/list")
async def list_members(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    tipe: Optional[str] = None  # 'pelanggan' or 'supplier'
):
    """
    List all members (customers) with optional filtering
    Round 22: Uses connection pooling for faster response times.
    """
    try:
        # Get tenant_id from auth context
        tenant_id = getattr(request.state, 'tenant_id', 'evlogia')

        # Round 22: Use connection pool for better performance
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Build query
            base_query = """
                SELECT id, nama, tipe, telepon, alamat, email, nomor_member, points,
                       points_per_50k, total_transaksi, total_nilai, saldo_hutang,
                       last_transaction_at, created_at
                FROM customers
                WHERE tenant_id = $1
            """
            count_query = "SELECT COUNT(*) FROM customers WHERE tenant_id = $1"
            params = [tenant_id]
            param_idx = 2

            # Add type filter
            if tipe:
                base_query += f" AND tipe = ${param_idx}"
                count_query += f" AND tipe = ${param_idx}"
                params.append(tipe)
                param_idx += 1

            # Add search filter
            if search:
                search_pattern = f"%{search}%"
                base_query += f" AND (nama ILIKE ${param_idx} OR telepon ILIKE ${param_idx} OR nomor_member ILIKE ${param_idx})"
                count_query += f" AND (nama ILIKE ${param_idx} OR telepon ILIKE ${param_idx} OR nomor_member ILIKE ${param_idx})"
                params.append(search_pattern)
                param_idx += 1

            # Get total count
            total = await conn.fetchval(count_query, *params[:param_idx-1])

            # Add pagination
            base_query += f" ORDER BY nama ASC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
            params.extend([limit, offset])

            # Execute query
            rows = await conn.fetch(base_query, *params)

            members = []
            for row in rows:
                members.append(MemberItem(
                    id=row['id'],
                    nama=row['nama'],
                    tipe=row['tipe'],
                    telepon=row['telepon'],
                    alamat=row['alamat'],
                    email=row['email'],
                    nomor_member=row['nomor_member'],
                    points=row['points'] or 0,
                    points_per_50k=row['points_per_50k'] or 1,
                    total_transaksi=row['total_transaksi'] or 0,
                    total_nilai=row['total_nilai'] or 0,
                    saldo_hutang=row['saldo_hutang'] or 0,
                    last_transaction_at=str(row['last_transaction_at']) if row['last_transaction_at'] else None,
                    created_at=str(row['created_at']) if row['created_at'] else None
                ))

            # Get summary stats
            summary_row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE tipe = 'pelanggan') as total_pelanggan,
                    COUNT(*) FILTER (WHERE tipe = 'supplier') as total_supplier,
                    COALESCE(SUM(saldo_hutang) FILTER (WHERE tipe = 'pelanggan' AND saldo_hutang > 0), 0) as total_piutang,
                    COALESCE(SUM(saldo_hutang) FILTER (WHERE tipe = 'supplier' AND saldo_hutang > 0), 0) as total_hutang
                FROM customers
                WHERE tenant_id = $1
            """, tenant_id)

            summary = MemberSummary(
                total_pelanggan=summary_row['total_pelanggan'] or 0,
                total_supplier=summary_row['total_supplier'] or 0,
                total_piutang=summary_row['total_piutang'] or 0,
                total_hutang=summary_row['total_hutang'] or 0
            )

            return MemberListResponse(
                members=members,
                total=total or 0,
                has_more=(offset + limit) < (total or 0),
                summary=summary
            )

    except Exception as e:
        logger.error(f"Error listing members: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_members(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query (name, phone, or member number)"),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Search members by name, phone number, or member number
    Optimized for POS autocomplete

    Round 22: Uses connection pooling for faster response times.
    """
    try:
        tenant_id = getattr(request.state, 'tenant_id', 'evlogia')

        # Round 22: Use connection pool for better performance
        pool = await get_pool()
        async with pool.acquire() as conn:
            search_pattern = f"%{q}%"

            # Search only pelanggan (not suppliers) for POS
            rows = await conn.fetch("""
                SELECT id, nama, tipe, telepon, alamat, email, nomor_member, points,
                       points_per_50k, total_transaksi, total_nilai, saldo_hutang,
                       last_transaction_at, created_at
                FROM customers
                WHERE tenant_id = $1
                  AND tipe = 'pelanggan'
                  AND (nama ILIKE $2 OR telepon ILIKE $2 OR nomor_member ILIKE $2)
                ORDER BY
                    CASE WHEN nama ILIKE $3 THEN 0 ELSE 1 END,
                    CASE WHEN nomor_member ILIKE $3 THEN 0 ELSE 1 END,
                    nama ASC
                LIMIT $4
            """, tenant_id, search_pattern, f"{q}%", limit)

            members = []
            for row in rows:
                members.append(MemberItem(
                    id=row['id'],
                    nama=row['nama'],
                    tipe=row['tipe'],
                    telepon=row['telepon'],
                    alamat=row['alamat'],
                    email=row['email'],
                    nomor_member=row['nomor_member'],
                    points=row['points'] or 0,
                    points_per_50k=row['points_per_50k'] or 1,
                    total_transaksi=row['total_transaksi'] or 0,
                    total_nilai=row['total_nilai'] or 0,
                    saldo_hutang=row['saldo_hutang'] or 0,
                    last_transaction_at=str(row['last_transaction_at']) if row['last_transaction_at'] else None,
                    created_at=str(row['created_at']) if row['created_at'] else None
                ))

            return MemberSearchResponse(
                members=members,
                query=q
            )

    except Exception as e:
        logger.error(f"Error searching members: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{member_id}")
async def get_member(request: Request, member_id: str):
    """
    Get member detail by ID
    Round 22: Uses connection pooling for faster response times.
    """
    try:
        tenant_id = getattr(request.state, 'tenant_id', 'evlogia')

        # Round 22: Use connection pool for better performance
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, nama, tipe, telepon, alamat, email, nomor_member, points,
                       points_per_50k, total_transaksi, total_nilai, saldo_hutang,
                       last_transaction_at, created_at
                FROM customers
                WHERE tenant_id = $1 AND id = $2
            """, tenant_id, member_id)

            if not row:
                raise HTTPException(status_code=404, detail="Member not found")

            return MemberItem(
                id=row['id'],
                nama=row['nama'],
                tipe=row['tipe'],
                telepon=row['telepon'],
                alamat=row['alamat'],
                email=row['email'],
                nomor_member=row['nomor_member'],
                points=row['points'] or 0,
                points_per_50k=row['points_per_50k'] or 1,
                total_transaksi=row['total_transaksi'] or 0,
                total_nilai=row['total_nilai'] or 0,
                saldo_hutang=row['saldo_hutang'] or 0,
                last_transaction_at=str(row['last_transaction_at']) if row['last_transaction_at'] else None,
                created_at=str(row['created_at']) if row['created_at'] else None
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting member: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add-points")
async def add_points(request: Request, data: AddPointsRequest):
    """
    Add points to member based on transaction amount
    Points = floor(transaction_amount / 50000) * points_per_50k
    Round 22: Uses connection pooling for faster response times.
    """
    try:
        tenant_id = getattr(request.state, 'tenant_id', 'evlogia')

        # Round 22: Use connection pool for better performance
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Get current member data
            row = await conn.fetchrow("""
                SELECT id, points, points_per_50k, total_transaksi, total_nilai
                FROM customers
                WHERE tenant_id = $1 AND id = $2
            """, tenant_id, data.member_id)

            if not row:
                raise HTTPException(status_code=404, detail="Member not found")

            # Calculate points to add
            points_per_50k = row['points_per_50k'] or 1
            points_to_add = (data.transaction_amount // 50000) * points_per_50k
            new_total_points = (row['points'] or 0) + points_to_add
            new_total_transaksi = (row['total_transaksi'] or 0) + 1
            new_total_nilai = (row['total_nilai'] or 0) + data.transaction_amount

            # Update member
            await conn.execute("""
                UPDATE customers
                SET points = $1,
                    total_transaksi = $2,
                    total_nilai = $3,
                    last_transaction_at = NOW(),
                    updated_at = NOW()
                WHERE tenant_id = $4 AND id = $5
            """, new_total_points, new_total_transaksi, new_total_nilai, tenant_id, data.member_id)

            return AddPointsResponse(
                success=True,
                member_id=data.member_id,
                points_added=points_to_add,
                new_total_points=new_total_points
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding points: {e}")
        raise HTTPException(status_code=500, detail=str(e))
