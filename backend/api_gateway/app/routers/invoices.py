"""
Invoices Router - Purchase Invoice (Faktur Pembelian) List & Search
Source: TransaksiHarian where jenis_transaksi = 'pembelian'

Endpoints:
- GET /api/invoices/purchase - List purchase invoices with filters
"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
from typing import List, Optional, Literal
from datetime import datetime, date
import logging
import asyncpg

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool (shared with suppliers router pattern)
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create database connection pool"""
    global _pool
    if _pool is None:
        logger.info("Creating database connection pool for invoices...")
        _pool = await asyncpg.create_pool(
            host="postgres",
            port=5432,
            user="postgres",
            password="Proyek771977",
            database="milkydb",
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("Invoices connection pool created")
    return _pool


# Status types for filtering
StatusType = Literal['all', 'draft', 'unpaid', 'partial', 'overdue', 'paid', 'void']
SortField = Literal['created_at', 'date', 'number', 'supplier', 'amount']
SortOrder = Literal['asc', 'desc']


class PurchaseInvoiceItem(BaseModel):
    """Single purchase invoice in list"""
    id: str
    invoice_number: Optional[str] = None
    supplier_name: str
    total_amount: int
    status: str  # draft, unpaid, partial, overdue, paid, void
    due_date: Optional[str] = None
    transaction_date: str
    created_at: str


class PurchaseInvoiceListResponse(BaseModel):
    """Response for purchase invoice list"""
    items: List[PurchaseInvoiceItem]
    total: int
    has_more: bool


def map_status(row: dict) -> str:
    """
    Map database status_pembayaran to frontend status

    DB values: LUNAS, HUTANG, DRAFT, BATAL
    Frontend values: draft, unpaid, partial, overdue, paid, void
    """
    status_pembayaran = row.get('status_pembayaran', '').upper()
    sisa = row.get('sisa_piutang_hutang') or 0
    total = row.get('total_nominal') or 0
    jatuh_tempo = row.get('jatuh_tempo')  # epoch ms

    if status_pembayaran == 'BATAL':
        return 'void'

    if status_pembayaran == 'DRAFT':
        return 'draft'

    if status_pembayaran == 'LUNAS':
        return 'paid'

    if status_pembayaran == 'HUTANG':
        # Check if overdue
        if jatuh_tempo:
            try:
                # jatuh_tempo is stored as epoch milliseconds
                due_date = datetime.fromtimestamp(jatuh_tempo / 1000).date()
                if due_date < date.today():
                    return 'overdue'
            except:
                pass

        # Check if partial payment
        if sisa > 0 and sisa < total:
            return 'partial'

        return 'unpaid'

    # Default fallback
    return 'unpaid'


def format_date_from_epoch(epoch_ms: Optional[int]) -> Optional[str]:
    """Convert epoch milliseconds to ISO date string"""
    if not epoch_ms:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000).strftime('%Y-%m-%d')
    except:
        return None


@router.get("/purchase", response_model=PurchaseInvoiceListResponse)
async def get_purchase_invoices(
    request: Request,
    status: StatusType = Query('all', description="Filter by status"),
    sort: SortField = Query('created_at', description="Sort field"),
    order: SortOrder = Query('desc', description="Sort order"),
    search: Optional[str] = Query(None, description="Search keyword"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get list of purchase invoices (Faktur Pembelian).

    Filters:
    - all: All invoices
    - draft: Draft invoices
    - unpaid: Unpaid (full balance remaining)
    - partial: Partially paid
    - overdue: Past due date
    - paid: Fully paid
    - void: Cancelled

    Sort options:
    - created_at: Creation time
    - date: Transaction date (timestamp)
    - number: Invoice number
    - supplier: Supplier name (nama_pihak)
    - amount: Total amount
    """
    try:
        # Get user from auth middleware
        if not hasattr(request.state, 'user') or not request.state.user:
            raise HTTPException(status_code=401, detail="Authentication required")

        tenant_id = request.state.user.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid user context")

        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build WHERE clause
            where_clauses = [
                "tenant_id = $1",
                "jenis_transaksi = 'pembelian'"
            ]
            params = [tenant_id]
            param_index = 2

            # Status filter
            if status != 'all':
                if status == 'draft':
                    where_clauses.append("UPPER(status_pembayaran) = 'DRAFT'")
                elif status == 'paid':
                    where_clauses.append("UPPER(status_pembayaran) = 'LUNAS'")
                elif status == 'void':
                    where_clauses.append("UPPER(status_pembayaran) = 'BATAL'")
                elif status == 'unpaid':
                    where_clauses.append("UPPER(status_pembayaran) = 'HUTANG'")
                    where_clauses.append("(sisa_piutang_hutang IS NULL OR sisa_piutang_hutang = total_nominal)")
                    where_clauses.append(f"(jatuh_tempo IS NULL OR jatuh_tempo >= ${param_index})")
                    params.append(int(datetime.now().timestamp() * 1000))
                    param_index += 1
                elif status == 'partial':
                    where_clauses.append("UPPER(status_pembayaran) = 'HUTANG'")
                    where_clauses.append("sisa_piutang_hutang > 0")
                    where_clauses.append("sisa_piutang_hutang < total_nominal")
                elif status == 'overdue':
                    where_clauses.append("UPPER(status_pembayaran) = 'HUTANG'")
                    where_clauses.append(f"jatuh_tempo < ${param_index}")
                    params.append(int(datetime.now().timestamp() * 1000))
                    param_index += 1

            # Search filter
            if search:
                where_clauses.append(
                    f"(LOWER(nama_pihak) LIKE LOWER(${param_index}) OR "
                    f"LOWER(keterangan) LIKE LOWER(${param_index}))"
                )
                params.append(f"%{search}%")
                param_index += 1

            where_sql = " AND ".join(where_clauses)

            # Sort mapping
            sort_mapping = {
                'created_at': 'created_at',
                'date': 'timestamp',
                'number': 'id',  # Using id as invoice number proxy
                'supplier': 'nama_pihak',
                'amount': 'total_nominal',
            }
            sort_column = sort_mapping.get(sort, 'created_at')
            sort_direction = 'DESC' if order == 'desc' else 'ASC'

            # Count query
            count_query = f"""
                SELECT COUNT(*) as total
                FROM public.transaksi_harian
                WHERE {where_sql}
            """

            count_row = await conn.fetchrow(count_query, *params)
            total = count_row['total'] if count_row else 0

            # Main query with pagination
            query = f"""
                SELECT
                    id,
                    nama_pihak,
                    total_nominal,
                    status_pembayaran,
                    sisa_piutang_hutang,
                    jatuh_tempo,
                    timestamp,
                    created_at,
                    keterangan
                FROM public.transaksi_harian
                WHERE {where_sql}
                ORDER BY {sort_column} {sort_direction}
                LIMIT ${param_index} OFFSET ${param_index + 1}
            """
            params.extend([limit, offset])

            rows = await conn.fetch(query, *params)

            # Map results
            items = []
            for row in rows:
                row_dict = dict(row)
                items.append(PurchaseInvoiceItem(
                    id=row['id'],
                    invoice_number=row['id'][:12] if row['id'] else None,  # Short ID as invoice number
                    supplier_name=row['nama_pihak'] or 'Unknown',
                    total_amount=row['total_nominal'] or 0,
                    status=map_status(row_dict),
                    due_date=format_date_from_epoch(row['jatuh_tempo']),
                    transaction_date=format_date_from_epoch(row['timestamp']) or '',
                    created_at=row['created_at'].isoformat() if row['created_at'] else '',
                ))

            has_more = (offset + len(items)) < total

            logger.info(
                f"Purchase invoices: tenant={tenant_id}, status={status}, "
                f"found={len(items)}, total={total}"
            )

            return PurchaseInvoiceListResponse(
                items=items,
                total=total,
                has_more=has_more,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching purchase invoices: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch invoices")


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "invoices_router"}
