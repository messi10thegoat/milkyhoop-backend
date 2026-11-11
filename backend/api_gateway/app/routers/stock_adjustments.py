"""
Stock Adjustments Router - Inventory Adjustments with Journal

Endpoints for managing stock adjustments (penyesuaian persediaan).
Adjustments create journal entries to track inventory value changes.

Flow:
1. Create draft stock adjustment
2. Post to accounting (creates journal + updates inventory)
3. Void if needed (creates reversal journal + reverses inventory)

Endpoints:
- GET    /stock-adjustments              - List adjustments
- GET    /stock-adjustments/summary      - Summary statistics
- GET    /stock-adjustments/{id}         - Get adjustment detail
- POST   /stock-adjustments              - Create draft
- PATCH  /stock-adjustments/{id}         - Update draft
- DELETE /stock-adjustments/{id}         - Delete draft
- POST   /stock-adjustments/{id}/post    - Post to accounting
- POST   /stock-adjustments/{id}/void    - Void with reversal
"""

from fastapi import APIRouter, HTTPException, Request, Query, Response
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal
import uuid as uuid_module

from ..utils.tanggal_tenant import tanggal_dokumen
from ..services import idem_buat
from ..services import nomor_jurnal as NJ
from ..services import stok_sesuai as SS
from ..services.bayar_keluar_rencana import Blok
from ..schemas.stock_adjustments import (
    CreateStockAdjustmentRequest,
    UpdateStockAdjustmentRequest,
    VoidStockAdjustmentRequest,
    StockAdjustmentResponse,
    StockAdjustmentDetailResponse,
    StockAdjustmentListResponse,
    StockAdjustmentSummaryResponse,
    PostStockAdjustmentRequest,
)
from ..services.resolve_account import resolve_account_id  # noqa: F401  (kept for back-compat)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
)
from ..services.role_precondition import assert_required_roles_for_path

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool

# Fase D3.3: role-based CoA resolution for stock adjustment posting.
# Replaces hardcoded literals 1-10600 (INVENTORY_MERCHANDISE) and
# 5-50100 (INVENTORY_ADJUSTMENT_EXPENSE). Void/reverse path reads
# account_id from journal_lines (Law 2/26) and is NOT touched.
_STOCK_ADJ_REQUIRED_ROLES = [
    AccountRole.INVENTORY_MERCHANDISE,
    AccountRole.INVENTORY_ADJUSTMENT_EXPENSE,
]
_stock_adj_precondition_checked_tenants: set = set()


async def _ensure_stock_adj_role_preconditions(pool, tenant_id=None):
    """Run role-mapping precondition once per tenant for stock_adjustments.

    Fails loud (PreconditionFailedError) if any tenant lacks any required
    role mapping. After first successful check the audit is skipped.
    """
    if tenant_id is None:
        # legacy/global fallback (unchanged behavior)
        await assert_required_roles_for_path(
            pool, "stock_adjustments", _STOCK_ADJ_REQUIRED_ROLES
        )
        return
    if tenant_id in _stock_adj_precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "stock_adjustments", _STOCK_ADJ_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _stock_adj_precondition_checked_tenants.add(tenant_id)


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu.")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Sesi tidak valid, silakan masuk lagi.")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


async def get_product_info(conn, tenant_id: str, product_id: UUID) -> Optional[dict]:
    """Get product info with current stock and weighted average cost.

    Law 16: Stock is derived from inventory_ledger (SUM(quantity_in) - SUM(quantity_out)),
    NOT from the legacy 'persediaan' table.
    """
    # Try products table first
    product = await conn.fetchrow(
        """
        SELECT p.id, p.nama_produk as name, COALESCE(NULLIF(p.base_unit, ''), p.satuan) as unit, p.barcode as code,
               COALESCE(p.purchase_price, 0) as purchase_price
        FROM products p
        WHERE p.id = $1 AND p.tenant_id = $2
    """,
        product_id,
        tenant_id,
    )

    if not product:
        return None

    # Get current stock from inventory_ledger (Law 16 compliant)
    current_stock = (
        await conn.fetchval(
            """
        SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0)
        FROM inventory_ledger
        WHERE product_id = $1 AND tenant_id = $2
    """,
            product_id,
            tenant_id,
        )
        or 0
    )

    # Get weighted average cost from inventory_ledger
    avg_cost = await conn.fetchval(
        """
        SELECT average_cost FROM inventory_ledger
        WHERE tenant_id = $1 AND product_id = $2
        ORDER BY created_at DESC LIMIT 1
    """,
        tenant_id,
        product_id,
    )

    # Fall back to purchase_price if no ledger history
    if avg_cost is None or avg_cost == 0:
        avg_cost = product["purchase_price"] or 0

    return {
        "id": product["id"],
        "name": product["name"],
        "code": product["code"],
        "unit": product["unit"],
        "current_stock": int(current_stock),  # Law 25: read path
        "unit_cost": int(avg_cost) if avg_cost else 0,  # Law 25: read path
    }


# =============================================================================
# LIST STOCK ADJUSTMENTS
# =============================================================================


@router.get("", response_model=StockAdjustmentListResponse)
async def list_stock_adjustments(
    request: Request,
    status: Optional[Literal["all", "draft", "posted", "void"]] = Query("all"),
    adjustment_type: Optional[
        Literal["increase", "decrease", "recount", "damaged", "expired"]
    ] = Query(None),
    search: Optional[str] = Query(None, description="Search by number"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal[
        "adjustment_date", "adjustment_number", "total_value", "created_at"
    ] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    aksi: Optional[Literal["draf", "bulan_ini"]] = Query(None, description="Kunci standar SO (services/stok_sesuai)"),
    with_totals: bool = Query(False, description="sum_total = Σ total_value baris terfilter tanpa void"),
):
    """List stock adjustments with filters and pagination (+ ?aksi=, with_totals, position_text/aksi/documents per baris)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if adjustment_type:
                conditions.append(f"adjustment_type = ${param_idx}")
                params.append(adjustment_type)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(f"adjustment_number ILIKE ${param_idx}")
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(f"adjustment_number ILIKE ${param_idx}")
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")
                # 26 Sep 2026: TANPA param_idx += 1 tambahan di sini — tiap cabang di atas sudah menaikkan (dulu ganda -> placeholder melompat -> 500 saat ?search)

            if date_from:
                conditions.append(f"adjustment_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"adjustment_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            if aksi in SS.SEMUA_KUNCI:
                predikat = SS.sql_aksi(aksi, f"${param_idx}")
                conditions.append(predikat)
                if f"${param_idx}" in predikat:  # hanya kunci bertanggal memakai tanggal bisnis (pelajaran ?aksi=draf Kas&Bank)
                    params.append(await tanggal_dokumen(conn, ctx["tenant_id"]))
                    param_idx += 1

            where_clause = " AND ".join(conditions)

            valid_sorts = {
                "adjustment_date": "adjustment_date",
                "adjustment_number": "adjustment_number",
                "total_value": "total_value",
                "created_at": "created_at",
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            count_query = f"SELECT COUNT(*) FROM stock_adjustments WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)
            jumlah = {}
            if with_totals is True:  # Query() default = objek truthy saat dipanggil langsung -> `is True`
                jumlah = {"sum_total": SS.rp(await conn.fetchval(
                    f"SELECT COALESCE(SUM(total_value), 0) FROM stock_adjustments WHERE {where_clause} AND status <> 'void'", *params))}

            query = f"""
                SELECT id, adjustment_number, adjustment_date, adjustment_type,
                       storage_location_name, total_value, item_count,
                       status, reference_no, created_at, journal_id
                FROM stock_adjustments
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "adjustment_number": row["adjustment_number"],
                    "adjustment_date": row["adjustment_date"].isoformat(),
                    "adjustment_type": row["adjustment_type"],
                    "storage_location_name": row["storage_location_name"],
                    "total_value": row["total_value"] or 0,
                    "item_count": row["item_count"] or 0,
                    "status": row["status"],
                    "reference_no": row["reference_no"],
                    "created_at": row["created_at"].isoformat(),
                    "jenis_text": SS.jenis_text(row["adjustment_type"]),
                    "position_text": SS.position_text(row["status"], row["adjustment_type"]),
                    "aksi": (SS.aksi_semua(row["status"]) or [None])[0],
                    "aksi_semua": SS.aksi_semua(row["status"]),
                    "documents": SS.documents_baris(row["status"], row["journal_id"]),
                    "doc_count": len(SS.documents_baris(row["status"], row["journal_id"])),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total, **jumlah}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing stock adjustments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list stock adjustments")


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary", response_model=StockAdjustmentSummaryResponse)
async def get_stock_adjustments_summary(request: Request, search: Optional[str] = Query(None)):
    """Get summary statistics for stock adjustments. + {k}_count/_value standar SO = predikat SAMA dgn ?aksi= daftar (services/stok_sesuai)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE status = 'void') as void_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'increase') as increase_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'decrease') as decrease_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'recount') as recount_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'damaged') as damaged_count,
                    COUNT(*) FILTER (WHERE adjustment_type = 'expired') as expired_count,
                    COALESCE(SUM(total_value) FILTER (WHERE status = 'posted'), 0) as total_value
                FROM stock_adjustments
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])
            cari, par_cari = "", []
            if isinstance(search, str) and search.strip():
                for n, kata in enumerate(search.strip().split(), 3):  # predikat cari SAMA dgn daftar (nomor penyesuaian)
                    cari += f" AND adjustment_number ILIKE ${n}"
                    par_cari.append(f"%{kata}%")
            k_aksi = await conn.fetchrow(
                "SELECT " + ", ".join(
                    f"COUNT(*) FILTER (WHERE {SS.sql_aksi(k, '$2')}) AS {k}_count, "
                    f"COALESCE(SUM(total_value) FILTER (WHERE {SS.sql_aksi(k, '$2')}), 0) AS {k}_value" for k in SS.SEMUA_KUNCI)
                + f" FROM stock_adjustments WHERE stock_adjustments.tenant_id = $1{cari}",
                ctx["tenant_id"], await tanggal_dokumen(conn, ctx["tenant_id"]), *par_cari)

            return {
                "success": True,
                "data": {
                    **{f"{k}_count": int(k_aksi[f"{k}_count"]) for k in SS.SEMUA_KUNCI},
                    **{f"{k}_value": SS.rp(k_aksi[f"{k}_value"]) for k in SS.SEMUA_KUNCI},
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "void_count": row["void_count"] or 0,
                    "by_type": {
                        "increase": row["increase_count"] or 0,
                        "decrease": row["decrease_count"] or 0,
                        "recount": row["recount_count"] or 0,
                        "damaged": row["damaged_count"] or 0,
                        "expired": row["expired_count"] or 0,
                    },
                    "total_value": int(row["total_value"] or 0),  # Law 25: read path
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stock adjustments summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET STOCK ADJUSTMENT DETAIL
# =============================================================================


@router.get("/counter-accounts")
async def daftar_akun_lawan_penyesuaian(request: Request):
    """Dropdown akun lawan jurnal penyesuaian (putusan pemilik 10 Nov #7a): HANYA akun sah (bukan persediaan/induk/nonaktif/piutang-utang/kas-bank),
    predikat SAMA dengan validasi tulis. bawaan_id = akun peran penyesuaian persediaan."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        return {"success": True, "data": await SS.daftar_akun_lawan(conn, ctx["tenant_id"])}


@router.get("/{adjustment_id}", response_model=StockAdjustmentDetailResponse)
async def get_stock_adjustment(request: Request, adjustment_id: UUID):
    """Get detailed information for a stock adjustment."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            sa = await conn.fetchrow(
                """
                SELECT sa.*, je.journal_number, w.name AS warehouse_name
                FROM stock_adjustments sa
                LEFT JOIN journal_entries je ON sa.journal_id = je.id AND je.tenant_id = sa.tenant_id
                LEFT JOIN warehouses w ON w.id = sa.warehouse_id AND w.tenant_id = sa.tenant_id
                WHERE sa.id = $1 AND sa.tenant_id = $2
            """,
                adjustment_id,
                ctx["tenant_id"],
            )

            if not sa:
                raise HTTPException(
                    status_code=404, detail="Stock adjustment not found"
                )

            items = await conn.fetch(
                """
                SELECT * FROM stock_adjustment_items
                WHERE stock_adjustment_id = $1
                ORDER BY line_number
            """,
                adjustment_id,
            )

            return {
                "success": True,
                "data": {
                    "id": str(sa["id"]),
                    "adjustment_number": sa["adjustment_number"],
                    "adjustment_date": sa["adjustment_date"].isoformat(),
                    "adjustment_type": sa["adjustment_type"],
                    "storage_location_id": str(sa["storage_location_id"])
                    if sa["storage_location_id"]
                    else None,
                    "storage_location_name": sa["storage_location_name"],
                    "reference_no": sa["reference_no"],
                    "notes": sa["notes"],
                    "total_value": sa["total_value"] or 0,
                    "item_count": sa["item_count"] or 0,
                    "status": sa["status"],
                    "journal_id": str(sa["journal_id"]) if sa["journal_id"] else None,
                    "journal_number": sa["journal_number"],
                    "items": [
                        {
                            "id": str(item["id"]),
                            "product_id": str(item["product_id"]),
                            "product_code": item["product_code"],
                            "product_name": item["product_name"],
                            "quantity_before": float(item["quantity_before"]),  # Law 25: read path
                            "quantity_adjustment": float(item["quantity_adjustment"]),  # Law 25: read path
                            "quantity_after": float(item["quantity_after"]),  # Law 25: read path
                            "unit": item["unit"],
                            "unit_cost": item["unit_cost"],
                            "total_value": item["total_value"],
                            "reason_detail": item["reason_detail"],
                            "system_quantity": float(item["system_quantity"])
                            if item["system_quantity"]
                            else None,  # Law 25: read path
                            "physical_quantity": float(item["physical_quantity"])
                            if item["physical_quantity"]
                            else None,  # Law 25: read path
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "posted_at": sa["posted_at"].isoformat()
                    if sa["posted_at"]
                    else None,
                    "posted_by": str(sa["posted_by"]) if sa["posted_by"] else None,
                    "voided_at": sa["voided_at"].isoformat()
                    if sa["voided_at"]
                    else None,
                    "voided_reason": sa["voided_reason"],
                    "created_at": sa["created_at"].isoformat(),
                    "updated_at": sa["updated_at"].isoformat(),
                    "created_by": str(sa["created_by"]) if sa["created_by"] else None,
                    "warehouse_id": str(sa["warehouse_id"]) if sa["warehouse_id"] else None,
                    "warehouse_name": sa["warehouse_name"],
                    "jenis_text": SS.jenis_text(sa["adjustment_type"]),
                    "position_text": SS.position_text(sa["status"], sa["adjustment_type"]),
                    "aksi_tersedia": await SS.aksi_tersedia(conn, ctx["tenant_id"], sa),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting stock adjustment {adjustment_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get stock adjustment")


# =============================================================================
# CREATE STOCK ADJUSTMENT
# =============================================================================


async def _tulis_baris_draf(conn, sa_id, baris) -> None:
    """Baris draf dari penentu SS.susun_baris (== pratinjau). Total induk diisi trigger trg_update_sa_totals (diukur pg_trigger)."""
    for b in baris:
        await conn.execute(
            """
            INSERT INTO stock_adjustment_items (
                id, stock_adjustment_id, product_id, product_code, product_name,
                quantity_before, quantity_adjustment, quantity_after, unit,
                unit_cost, total_value, reason_detail,
                system_quantity, physical_quantity, line_number
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
            uuid_module.uuid4(), sa_id, b["product_id"], b["product_code"], b["product_name"],
            b["quantity_before"], b["quantity_adjustment"], b["quantity_after"], b["unit"],
            b["unit_cost"], b["total_value"], b["reason_detail"], b["system_quantity"], b["physical_quantity"], b["line_number"],
        )


async def _lokasi(conn, tid: str, storage_location_id) -> Optional[str]:
    if not storage_location_id:
        return None
    loc = await conn.fetchrow("SELECT name FROM storage_locations WHERE id = $1 AND tenant_id = $2", UUID(storage_location_id), tid)
    return loc["name"] if loc else None


@router.post("/preview")
async def preview_create_stock_adjustment(request: Request, body: CreateStockAdjustmentRequest):
    """Standar SO: pratinjau buat -- penentu SAMA dengan POST (gudang, stok sebelum PER gudang, selisih, nilai), READ ONLY."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    return await SS.pratinjau(await get_pool(), tid, lambda c: SS.rencana_buat(c, tid, body, Blok(True)), body.model_dump(mode="json"))


@router.post("", response_model=StockAdjustmentResponse, status_code=201)
async def create_stock_adjustment(request: Request, body: CreateStockAdjustmentRequest, response: Response = None):
    """Create a new stock adjustment in draft status. Penentu SS.rencana_buat (== /preview); X-Idempotency-Key opsional."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                _kp, _sd, _lama = await idem_buat.mulai(conn, ctx, idem_buat.kunci_dari(request), "STOCK_ADJ_CREATE", body,
                                                        "stock_adjustments", "adjustment", response)
                if _lama is not None:
                    return _lama
                r = await SS.rencana_buat(conn, ctx["tenant_id"], body, Blok(False))
                sa_number = await conn.fetchval(
                    "SELECT generate_stock_adjustment_number($1, 'SA')",
                    ctx["tenant_id"],
                )
                sa_id = uuid_module.uuid4()

                await conn.execute(
                    """
                    INSERT INTO stock_adjustments (
                        id, tenant_id, adjustment_number, adjustment_date, adjustment_type,
                        storage_location_id, storage_location_name,
                        reference_no, notes, status, created_by, warehouse_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'draft', $10, $11)
                """,
                    sa_id,
                    ctx["tenant_id"],
                    sa_number,
                    body.adjustment_date,
                    body.adjustment_type,
                    UUID(body.storage_location_id) if body.storage_location_id else None,
                    await _lokasi(conn, ctx["tenant_id"], body.storage_location_id),
                    body.reference_no,
                    body.notes,
                    ctx["user_id"],
                    r["gudang"]["id"],
                )
                await _tulis_baris_draf(conn, sa_id, r["baris_draf"])
                total_value = sum((b["total_value"] for b in r["baris_draf"]), Decimal(0))

                logger.info(f"Stock adjustment created: {sa_id}, number={sa_number}")

                return await idem_buat.simpan(conn, ctx, _kp, _sd, "STOCK_ADJ_CREATE", {
                    "success": True,
                    "message": "Stock adjustment created successfully",
                    "data": {
                        "id": str(sa_id),
                        "adjustment_number": sa_number,
                        "total_value": total_value,
                        "status": "draft",
                        "warehouse_id": str(r["gudang"]["id"]),
                    },
                }, sa_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating stock adjustment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create stock adjustment")


# =============================================================================
# UPDATE STOCK ADJUSTMENT
# =============================================================================


@router.post("/{adjustment_id}/edit/preview")
async def preview_update_stock_adjustment(request: Request, adjustment_id: UUID, body: UpdateStockAdjustmentRequest):
    """Standar SO: pratinjau ubah draf -- penentu SAMA dengan PATCH (SS.rencana_ubah), READ ONLY."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    return await SS.pratinjau(await get_pool(), tid, lambda c: SS.rencana_ubah(c, tid, adjustment_id, body, Blok(True)),
                              body.model_dump(mode="json"))


@router.patch("/{adjustment_id}", response_model=StockAdjustmentResponse)
async def update_stock_adjustment(
    request: Request, adjustment_id: UUID, body: UpdateStockAdjustmentRequest
):
    """Update a draft stock adjustment. Penentu SS.rencana_ubah (== /edit/preview)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        # Optimistic concurrency (opt-in If-Match): reject a stale write.
        from ..services.optimistic_concurrency import assert_if_match_row
        await assert_if_match_row(request, "stock_adjustments", adjustment_id)

        async with pool.acquire() as conn:
            async with conn.transaction():
                r = await SS.rencana_ubah(conn, ctx["tenant_id"], adjustment_id, body, Blok(False))

                updates = []
                params = []
                param_idx = 1
                for kolom, nilai in (("adjustment_date", body.adjustment_date), ("adjustment_type", body.adjustment_type),
                                     ("reference_no", body.reference_no), ("notes", body.notes)):
                    if nilai is not None:
                        updates.append(f"{kolom} = ${param_idx}")
                        params.append(nilai)
                        param_idx += 1

                if body.storage_location_id is not None:
                    updates.append(f"storage_location_id = ${param_idx}")
                    params.append(UUID(body.storage_location_id) if body.storage_location_id else None)
                    param_idx += 1
                    updates.append(f"storage_location_name = ${param_idx}")
                    params.append(await _lokasi(conn, ctx["tenant_id"], body.storage_location_id))
                    param_idx += 1

                if r["gudang"] and r["gudang"]["id"] != r["sa"]["warehouse_id"]:
                    updates.append(f"warehouse_id = ${param_idx}")
                    params.append(r["gudang"]["id"])
                    param_idx += 1

                if updates:
                    updates.append("updated_at = NOW()")
                    params.extend([adjustment_id, ctx["tenant_id"]])

                    query = f"""
                        UPDATE stock_adjustments
                        SET {", ".join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """
                    await conn.execute(query, *params)

                # Update items if provided (baris dari penentu; total induk lewat trigger trg_update_sa_totals)
                if r["baris_draf"] is not None:
                    await conn.execute(
                        "DELETE FROM stock_adjustment_items WHERE stock_adjustment_id = $1",
                        adjustment_id,
                    )
                    await _tulis_baris_draf(conn, adjustment_id, r["baris_draf"])

                logger.info(f"Stock adjustment updated: {adjustment_id}")

                return {
                    "success": True,
                    "message": "Stock adjustment updated successfully",
                    "data": {"id": str(adjustment_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error updating stock adjustment {adjustment_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to update stock adjustment")


# =============================================================================
# DELETE STOCK ADJUSTMENT
# =============================================================================


@router.post("/{adjustment_id}/delete/preview")
async def preview_delete_stock_adjustment(request: Request, adjustment_id: UUID):
    """Standar SO: pratinjau hapus draf -- penentu SAMA dengan DELETE (SS.rencana_hapus), READ ONLY."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    return await SS.pratinjau(await get_pool(), tid, lambda c: SS.rencana_hapus(c, tid, adjustment_id, Blok(True)),
                              {"adjustment_id": str(adjustment_id)})


@router.delete("/{adjustment_id}", response_model=StockAdjustmentResponse)
async def delete_stock_adjustment(request: Request, adjustment_id: UUID):
    """Delete a draft stock adjustment. Penentu SS.rencana_hapus (== /delete/preview)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            r = await SS.rencana_hapus(conn, ctx["tenant_id"], adjustment_id, Blok(False))
            sa = r["sa"]

            await conn.execute(
                "DELETE FROM stock_adjustments WHERE id = $1 AND tenant_id = $2", adjustment_id, ctx["tenant_id"]
            )

            logger.info(f"Stock adjustment deleted: {adjustment_id}")

            return {
                "success": True,
                "message": "Stock adjustment deleted",
                "data": {
                    "id": str(adjustment_id),
                    "adjustment_number": sa["adjustment_number"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error deleting stock adjustment {adjustment_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to delete stock adjustment")


# =============================================================================
# POST STOCK ADJUSTMENT
# =============================================================================


@router.post("/{adjustment_id}/post/preview")
async def preview_post_stock_adjustment(request: Request, adjustment_id: UUID, body: Optional[PostStockAdjustmentRequest] = None):
    """Standar SO: pratinjau posting -- jurnal + gerak stok per gudang + isyarat (stok minus, harga pokok berubah sejak draf), penentu SAMA
    dengan /post (SS.rencana_posting), READ ONLY. Body opsional counter_account_id = akun lawan pilihan (penyesuaian-2)."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    lawan = body.counter_account_id if body else None
    return await SS.pratinjau(await get_pool(), tid, lambda c: SS.rencana_posting(c, tid, adjustment_id, Blok(True), counter_account_id=lawan),
                              {"adjustment_id": str(adjustment_id), "counter_account_id": lawan})


@router.post("/{adjustment_id}/post", response_model=StockAdjustmentResponse)
async def post_stock_adjustment(request: Request, adjustment_id: UUID, response: Response = None,
                                body: Optional[PostStockAdjustmentRequest] = None):
    """
    Post stock adjustment to accounting.

    For INCREASE: Dr. Persediaan, Cr. Penyesuaian Persediaan
    For DECREASE: Dr. Penyesuaian Persediaan, Cr. Persediaan
    Penentu SS.rencana_posting (== /post/preview): baris jurnal + gerak stok di gudang penyesuaian (putusan pemilik 9 Nov: gudang dipilih,
    bawaan UTAMA). Nomor jurnal = nomor dokumen (nomor_jurnal_dokumen, putusan MASTER; data baru saja). X-Idempotency-Key opsional.
    Body opsional counter_account_id = akun lawan jurnal pilihan pengguna (putusan pemilik 10 Nov #7a), divalidasi SS.akun_lawan.
    """
    lawan = body.counter_account_id if body else None
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()
        await _ensure_stock_adj_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock to prevent concurrent posting
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"STOCK_ADJ:{adjustment_id}",
                )
                _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "STOCK_ADJ_POST", adjustment_id,
                                                             {"counter_account_id": lawan} if lawan else {}, response)
                if _lama is not None:
                    return _lama
                r = await SS.rencana_posting(conn, ctx["tenant_id"], adjustment_id, Blok(False), kunci=True, counter_account_id=lawan)
                sa = r["sa"]

                # Law 20: Create journal entry as DRAFT first
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = await NJ.nomor_jurnal_dokumen(conn, ctx["tenant_id"], sa["adjustment_number"], "SAJ")

                # Step 1: INSERT as DRAFT (Law 20)
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'STOCK_ADJUSTMENT', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    sa["adjustment_date"],
                    f"Stock Adjustment {sa['adjustment_number']} - {sa['adjustment_type'].title()}",
                    adjustment_id,
                    str(trace_id),
                    r["total"],  # Law 25: pass Decimal directly
                    ctx["user_id"],
                )

                # Step 2: INSERT journal_lines dari penentu (== pratinjau)
                for line_number, (_akun, _debit, _kredit, _memo) in enumerate(r["baris"], 1):
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        uuid_module.uuid4(), journal_id, line_number, _akun, _debit, _kredit, _memo,
                    )

                # Step 3: UPDATE to POSTED (Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # inventory_ledger per gerak penentu (barang goods + track_inventory), di gudang penyesuaian
                adj_warehouse_id = r["gudang"]["id"]
                for g in r["gerak"]:
                    unit_cost = g["unit_cost"]
                    if g["masuk"] > 0:
                        # INBOUND: WAC recompute + ledger insert lewat helper KANONIK (milkyhoop-inventory Rule 3, L09)
                        from ..services.inventory_helpers import (
                            record_inventory_inbound,
                        )

                        await record_inventory_inbound(
                            conn,
                            tenant_id=ctx["tenant_id"],
                            product_id=g["product_id"],
                            product_code=g["product_code"],
                            product_name=g["product_name"],
                            warehouse_id=adj_warehouse_id,
                            quantity=g["masuk"],
                            unit_cost=unit_cost,
                            source_type="STOCK_ADJUSTMENT",
                            source_id=adjustment_id,
                            source_number=sa["adjustment_number"],
                            user_id=ctx["user_id"],
                            notes=f"Stock Adjustment {sa['adjustment_number']} - {sa['adjustment_type'].title()}",
                            movement_date=sa["adjustment_date"],
                            journal_id=journal_id,
                            movement_type="STOCK_ADJUSTMENT",
                            storage_location_id=sa["storage_location_id"],
                        )
                    else:
                        # OUTBOUND: WAC does NOT change (Rule 3). Snapshot current net-on-hand WAC, insert outbound row.
                        qty_out = g["keluar"]
                        total_cost = unit_cost * qty_out
                        balance_row = await conn.fetchrow(
                            """
                            SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as balance
                            FROM inventory_ledger
                            WHERE tenant_id = $1 AND product_id = $2
                        """,
                            ctx["tenant_id"],
                            g["product_id"],
                        )
                        current_balance = Decimal(str(balance_row["balance"])) if balance_row else Decimal("0")
                        new_balance = current_balance - qty_out

                        avg_cost_row = await conn.fetchrow(
                            """
                            SELECT
                                COALESCE(SUM(quantity_in * unit_cost)
                                         - SUM(quantity_out * unit_cost), 0) as net_value,
                                COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as total_qty
                            FROM inventory_ledger
                            WHERE tenant_id = $1 AND product_id = $2
                        """,
                            ctx["tenant_id"],
                            g["product_id"],
                        )
                        if avg_cost_row and Decimal(str(avg_cost_row["total_qty"])) > 0:
                            new_avg_cost = Decimal(str(avg_cost_row["net_value"])) / Decimal(str(avg_cost_row["total_qty"]))
                        else:
                            new_avg_cost = unit_cost

                        await conn.execute(
                            """
                            INSERT INTO inventory_ledger (
                                tenant_id, product_id, product_code, product_name,
                                movement_type, movement_date, source_type, source_id, source_number,
                                quantity_in, quantity_out, quantity_balance,
                                unit_cost, total_cost, average_cost,
                                warehouse_id, storage_location_id, journal_id, created_by, notes
                            ) VALUES (
                                $1, $2, $3, $4,
                                'STOCK_ADJUSTMENT', $5, 'STOCK_ADJUSTMENT', $6, $7,
                                0, $8, $9,
                                $10, $11, $12,
                                $13, $14, $15, $16, $17
                            )
                        """,
                            ctx["tenant_id"],
                            g["product_id"],
                            g["product_code"],
                            g["product_name"],
                            sa["adjustment_date"],
                            adjustment_id,
                            sa["adjustment_number"],
                            qty_out,
                            new_balance,
                            unit_cost,
                            total_cost,
                            new_avg_cost,
                            adj_warehouse_id,
                            sa["storage_location_id"],
                            journal_id,
                            ctx["user_id"],
                            f"Stock Adjustment {sa['adjustment_number']} - {sa['adjustment_type'].title()}",
                        )

                # Update stock adjustment status (+ gudang yang benar-benar dipakai, untuk draf lama tanpa warehouse_id)
                await conn.execute(
                    """
                    UPDATE stock_adjustments
                    SET status = 'posted', journal_id = $2, warehouse_id = $4,
                        posted_at = NOW(), posted_by = $3, updated_at = NOW()
                    WHERE id = $1 AND tenant_id = $5
                """,
                    adjustment_id,
                    journal_id,
                    ctx["user_id"],
                    adj_warehouse_id,
                    ctx["tenant_id"],
                )

                logger.info(
                    f"Stock adjustment posted: {adjustment_id}, journal={journal_id}"
                )

                return await idem_buat.simpan(conn, ctx, _kp, _sd, "STOCK_ADJ_POST", {
                    "success": True,
                    "message": "Stock adjustment posted to accounting",
                    "data": {
                        "id": str(adjustment_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "status": "posted",
                    },
                }, adjustment_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error posting stock adjustment {adjustment_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to post stock adjustment")


# =============================================================================
# VOID STOCK ADJUSTMENT
# =============================================================================


@router.post("/{adjustment_id}/void/preview")
async def preview_void_stock_adjustment(request: Request, adjustment_id: UUID, body: VoidStockAdjustmentRequest):
    """Standar SO: pratinjau batal -- jurnal pembalik + cermin gerak stok + isyarat stok minus, penentu SAMA dengan /void, READ ONLY."""
    ctx = get_user_context(request)
    tid = ctx["tenant_id"]
    return await SS.pratinjau(await get_pool(), tid, lambda c: SS.rencana_batal(c, tid, adjustment_id, body.reason, Blok(True)),
                              body.model_dump(mode="json"))


@router.post("/{adjustment_id}/void", response_model=StockAdjustmentResponse)
async def void_stock_adjustment(
    request: Request, adjustment_id: UUID, body: VoidStockAdjustmentRequest, response: Response = None
):
    """Void a stock adjustment with reversal. Penentu SS.rencana_batal (== /void/preview); putusan pemilik 9 Nov: periode TANGGAL
    penyesuaian juga harus terbuka. X-Idempotency-Key opsional."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock to prevent concurrent voiding
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"STOCK_ADJ_VOID:{adjustment_id}",
                )
                _kp, _sd, _lama = await idem_buat.mulai_aksi(conn, ctx, idem_buat.kunci_dari(request), "STOCK_ADJ_VOID", adjustment_id,
                                                             {"reason": body.reason}, response)
                if _lama is not None:
                    return _lama
                r = await SS.rencana_batal(conn, ctx["tenant_id"], adjustment_id, body.reason, Blok(False), kunci=True)
                sa = r["sa"]

                if r["hapus_draf"]:
                    await conn.execute(
                        "DELETE FROM stock_adjustments WHERE id = $1 AND tenant_id = $2", adjustment_id, ctx["tenant_id"]
                    )
                    return await idem_buat.simpan(conn, ctx, _kp, _sd, "STOCK_ADJ_VOID", {
                        "success": True,
                        "message": "Draft stock adjustment deleted",
                        "data": {"id": str(adjustment_id)},
                    }, adjustment_id)

                hari_ini = r["hari_ini"]  # t10-tanggal-bisnis

                # Create reversal journal
                reversal_journal_id = None
                if sa["journal_id"]:
                    reversal_journal_id = uuid_module.uuid4()

                    # Canonical reversal number via self-healing generator
                    # (V176): GREATEST(counter+1, anchored-actual-max+1). Mirrors
                    # sales_invoices.void_invoice -- avoids hardcoded RV-{num}
                    # collisions and keeps the JV counter in sync. chain_sequence
                    # is left NULL so the AFTER trigger (assign_hash_and_sequence)
                    # assigns max+1 atomically (Law 22 -- no (tenant,seq) collision).
                    reversal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "JV-REV",
                        hari_ini,  # t10-tanggal-bisnis
                    )
                    reversal_trace = str(uuid_module.uuid4())

                    # Step 1: INSERT reversal as DRAFT (Law 20)
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            trace_id, status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $10, $4, 'STOCK_ADJUSTMENT', $5, $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        reversal_number,
                        f"Void {sa['adjustment_number']} - {body.reason}",
                        adjustment_id,
                        sa["journal_id"],
                        reversal_trace,
                        sa["total_value"],  # Law 25: pass Decimal directly
                        ctx["user_id"],
                        hari_ini,  # t10-tanggal-bisnis
                    )

                    # Step 2: INSERT reversal journal_lines dari penentu (== pratinjau): debit/kredit ditukar
                    for idx, (_akun, _debit, _kredit, _memo) in enumerate(r["baris"], 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(), reversal_journal_id, idx, _akun, _debit, _kredit, _memo,
                        )

                    # Step 3: UPDATE reversal to POSTED (Law 20)
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        reversal_journal_id,
                    )

                    # Law 26 + Law 2: Link reversal -- set reversed_by_id (+
                    # reversed_at) on the ORIGINAL journal and leave it
                    # status='POSTED' (immutability). is_effective_journal()
                    # drops it via reversed_by_id; the reversal carries
                    # reversal_of_id. Mirrors sales_invoices.void_invoice exactly.
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()
                        WHERE id = $1
                    """,
                        sa["journal_id"],
                        reversal_journal_id,
                    )

                # Reverse inventory changes via the canonical shared helper (milkyhoop-inventory Rule 9)
                if reversal_journal_id:
                    from ..services.inventory_helpers import (
                        record_inventory_reversal,
                    )

                    await record_inventory_reversal(
                        conn,
                        ctx["tenant_id"],
                        source_type="STOCK_ADJUSTMENT",
                        source_id=adjustment_id,
                        reversal_journal_id=reversal_journal_id,
                        created_by=ctx["user_id"],
                        reversal_date=hari_ini,  # t10-tanggal-bisnis
                        notes_prefix=f"VOID {sa['adjustment_number']}",
                    )

                # Update status
                await conn.execute(
                    """
                    UPDATE stock_adjustments
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1 AND tenant_id = $4
                """,
                    adjustment_id,
                    ctx["user_id"],
                    body.reason,
                    ctx["tenant_id"],
                )

                logger.info(f"Stock adjustment voided: {adjustment_id}")

                return await idem_buat.simpan(conn, ctx, _kp, _sd, "STOCK_ADJ_VOID", {
                    "success": True,
                    "message": "Stock adjustment voided successfully",
                    "data": {
                        "id": str(adjustment_id),
                        "status": "void",
                        "reversal_journal_id": str(reversal_journal_id)
                        if reversal_journal_id
                        else None,
                    },
                }, adjustment_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error voiding stock adjustment {adjustment_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to void stock adjustment")


@router.get("/{adjustment_id}/history")
async def get_stock_adjustment_history(request: Request, adjustment_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """Standar SO: riwayat satu penyesuaian, bentuk SAMA dgn /sales-orders/{id}/history (services/stok_sesuai)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await SS.riwayat(conn, ctx["tenant_id"], adjustment_id, limit)
    if data is None:
        raise HTTPException(status_code=404, detail="Penyesuaian stok tidak ditemukan.")
    return {"success": True, "data": data}


@router.get("/{adjustment_id}/documents")
async def get_stock_adjustment_documents(request: Request, adjustment_id: UUID):
    """Standar SO: bundel dokumen {anchor, default, groups} -- penyesuaian · jurnal (+pembalik) · gerak stok."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await SS.dokumen(conn, ctx["tenant_id"], adjustment_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Penyesuaian stok tidak ditemukan.")
    return {"success": True, "data": data}


@router.get("/{adjustment_id}/journal-entries")
async def get_stock_adjustment_journal_entries(request: Request, adjustment_id: str):
    """
    Tab: Journal Entries - Get journal entries linked to this stock adjustment.
    Includes the original posting journal and any reversal journal.
    """
    try:
        try:
            uuid_module.UUID(adjustment_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid adjustment_id format")

        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            sa = await conn.fetchrow(
                """
                SELECT id, adjustment_number, journal_id, status
                FROM stock_adjustments
                WHERE id = $1::uuid AND tenant_id = $2
                """,
                adjustment_id,
                ctx["tenant_id"],
            )

            if not sa:
                raise HTTPException(
                    status_code=404, detail="Stock adjustment not found"
                )

            journal_ids = []
            if sa["journal_id"]:
                journal_ids.append(sa["journal_id"])
                reversal = await conn.fetchrow(
                    """
                    SELECT reversed_by_id FROM journal_entries
                    WHERE id = $1 AND reversed_by_id IS NOT NULL
                    """,
                    sa["journal_id"],
                )
                if reversal and reversal["reversed_by_id"]:
                    journal_ids.append(reversal["reversed_by_id"])

            if not journal_ids:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "summary": {
                        "total_debit": 0,
                        "total_credit": 0,
                        "is_balanced": True,
                    },
                }

            journals = await conn.fetch(
                """
                SELECT je.id, je.journal_number, je.journal_date, je.memo as description,
                       je.source_type, je.status, je.total_debit, je.total_credit
                FROM journal_entries je
                WHERE je.id = ANY($1::uuid[])
                ORDER BY je.journal_date, je.created_at
                """,
                journal_ids,
            )

            journal_data = []
            total_debit = 0
            total_credit = 0

            for journal in journals:
                lines = await conn.fetch(
                    """
                    SELECT jl.id, jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo,
                           coa.account_code, coa.name as account_name
                    FROM journal_lines jl
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    journal["id"],
                )

                line_data = [
                    {
                        "id": str(line["id"]),
                        "line_number": line["line_number"],
                        "account_id": str(line["account_id"]),
                        "account_code": line["account_code"],
                        "account_name": line["account_name"],
                        "debit": int(line["debit"] or 0),  # Law 25: read path
                        "credit": int(line["credit"] or 0),  # Law 25: read path
                        "memo": line["memo"] or "",
                    }
                    for line in lines
                ]

                journal_debit = int(journal["total_debit"] or 0)  # Law 25: read path
                journal_credit = int(journal["total_credit"] or 0)  # Law 25: read path
                total_debit += journal_debit
                total_credit += journal_credit

                journal_data.append(
                    {
                        "id": str(journal["id"]),
                        "journal_number": journal["journal_number"],
                        "journal_date": journal["journal_date"].isoformat()
                        if journal["journal_date"]
                        else None,
                        "description": journal["description"],
                        "source_type": journal["source_type"],
                        "status": journal["status"],
                        "total_debit": journal_debit,
                        "total_credit": journal_credit,
                        "is_balanced": abs(journal_debit - journal_credit) < 0.01,
                        "lines": line_data,
                    }
                )

            return {
                "success": True,
                "data": journal_data,
                "total": len(journal_data),
                "summary": {
                    "total_debit": total_debit,
                    "total_credit": total_credit,
                    "is_balanced": abs(total_debit - total_credit) < 0.01,
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting stock adjustment journal entries: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get journal entries")
