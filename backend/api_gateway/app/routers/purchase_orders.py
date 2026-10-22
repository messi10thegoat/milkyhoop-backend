"""
Purchase Orders Router - Pesanan Pembelian

Endpoints for managing purchase orders (PO) for procurement.
IMPORTANT: PO does NOT create journal entries - journal is created only when Bill is created.

Flow:
1. Create draft PO
2. Send PO to vendor
3. Receive goods (partial or full)
4. Convert to Bill (creates journal entry via bill posting)
5. Close or Cancel PO

Status Flow:
draft -> sent -> partial_received/received -> partial_billed/billed -> closed
                                                                     ↓
                                                                cancelled

Endpoints:
- GET    /purchase-orders              - List purchase orders
- GET    /purchase-orders/pending      - Pending POs
- GET    /purchase-orders/summary      - Summary statistics
- GET    /purchase-orders/{id}         - Get PO detail
- POST   /purchase-orders              - Create draft PO
- PATCH  /purchase-orders/{id}         - Update draft/sent PO
- DELETE /purchase-orders/{id}         - Delete draft PO
- POST   /purchase-orders/{id}/send    - Mark as sent
- POST   /purchase-orders/{id}/receive - Record goods receipt
- POST   /purchase-orders/{id}/to-bill - Convert to Bill
- POST   /purchase-orders/{id}/cancel  - Cancel PO
- POST   /purchase-orders/{id}/close   - Close completed PO
- GET    /vendors/{id}/purchase-orders - POs for vendor
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from ..utils.tanggal_tenant import tanggal_dokumen
from ..services import po_daftar as PD  # P-U2: keadaan/aksi/position_text/documents SERVER (satu sumber)
from ..services import po_hitung as PH  # P-U2 sisi tulis: uang Decimal 2dp HALF_UP (satu sumber)
from ..services import idem_buat  # idempotensi header X-Idempotency-Key (pola W0)
from ..services.so_riwayat import catat_riwayat  # riwayat beraktor (audit_logs) -- GET /{id}/history
from fastapi import Response

from ..schemas.purchase_orders import (
    CreatePurchaseOrderRequest,
    UpdatePurchaseOrderRequest,
    ReceiveGoodsRequest,
    ConvertToBillRequest,
    CancelPurchaseOrderRequest,
    PurchaseOrderResponse,
    PurchaseOrderDetailResponse,
    PurchaseOrderListResponse,
    PurchaseOrderSummaryResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool


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


def _kunci_idem(request):
    """X-Idempotency-Key (alias Idempotency-Key) atau None; tanpa header = perilaku lama. Objek uji tanpa .headers -> None."""
    if getattr(request, "headers", None) is None:
        return None
    return idem_buat.kunci_dari(request)


async def _kunci_po(conn, tenant_id: str, po_id, kolom: str = "*"):
    """Law 13: kunci PO (advisory, per tenant+id) lalu baca FOR UPDATE; semua pemeriksaan status SESUDAHNYA di dalam lock. None bila tak ada."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"PO:{tenant_id}:{po_id}")
    return await conn.fetchrow(
        f"SELECT {kolom} FROM purchase_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE", po_id, tenant_id)  # nosec B608


async def _vendor_tenant(conn, tenant_id: str, vendor_id):
    """Law 24: vendor_id WAJIB milik tenant (422 bila bukan). None bila vendor_id kosong."""
    if not vendor_id:
        return None
    try:
        vid = UUID(str(vendor_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail={"code": "VENDOR_TIDAK_SAH", "message": "ID vendor tidak sah."})
    v = await conn.fetchrow("SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2", vid, tenant_id)
    if not v:
        raise HTTPException(status_code=422, detail={"code": "VENDOR_TIDAK_DITEMUKAN", "message": "Vendor tidak ditemukan di usaha ini."})
    return v


async def _simpan_baris(conn, po_id, baris: list):
    for idx, item in enumerate(baris, 1):
        await conn.execute(
            """
            INSERT INTO purchase_order_items (
                po_id, item_id, item_code, description,
                quantity, unit, unit_price,
                discount_percent, discount_amount,
                tax_code, tax_rate, tax_amount,
                subtotal, total, line_number
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
            po_id,
            UUID(item["item_id"]) if item.get("item_id") else None,
            item.get("item_code"),
            item["description"],
            Decimal(str(item["quantity"])),
            item.get("unit"),
            item["unit_price"],
            Decimal(str(item.get("discount_percent", 0))),
            item["discount_amount"],
            item.get("tax_code"),
            Decimal(str(item.get("tax_rate", 0))),
            item["tax_amount"],
            item["subtotal"],
            item["total"],
            idx,
        )


async def _hitung_ulang_total(conn, po_id):
    """Total header dari baris DB + diskon/pajak header (trigger DB penimpa total sudah di-DROP, V435). Dipanggil sesudah SETIAP tulis baris/header."""
    po = await conn.fetchrow("SELECT discount_percent, discount_amount, tax_rate FROM purchase_orders WHERE id = $1", po_id)
    baris = await conn.fetch("SELECT subtotal, discount_amount, tax_amount FROM purchase_order_items WHERE po_id = $1", po_id)
    t = PH.hitung_header([dict(b) for b in baris], po["discount_percent"], po["discount_amount"], po["tax_rate"])
    await conn.execute(
        "UPDATE purchase_orders SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5, updated_at = NOW() WHERE id = $1",
        po_id, t["subtotal"], t["discount_amount"], t["tax_amount"], t["total_amount"])
    return t


def calculate_item_totals(item: dict) -> dict:
    """Calculate item totals with discount and tax."""
    quantity = Decimal(str(item.get("quantity", 0)))
    unit_price = Decimal(str(item.get("unit_price", 0)))
    discount_percent = Decimal(str(item.get("discount_percent", 0)))
    discount_amount = Decimal(str(item.get("discount_amount", 0)))
    tax_rate = Decimal(str(item.get("tax_rate", 0)))

    subtotal = quantity * unit_price

    # Apply discount (percent takes precedence)
    if discount_percent > 0:
        discount = subtotal * discount_percent / 100
    else:
        discount = discount_amount

    after_discount = subtotal - discount

    # Apply tax
    tax_amount = after_discount * tax_rate / 100

    total = after_discount + tax_amount

    return {
        **item,
        "subtotal": int(subtotal),
        "discount_amount": int(discount),
        "tax_amount": int(tax_amount),
        "total": int(total),
    }


# =============================================================================
# LIST PURCHASE ORDERS
# =============================================================================


@router.get("", response_model=PurchaseOrderListResponse)
async def list_purchase_orders(
    request: Request,
    status: Optional[
        Literal[
            "all",
            "draft",
            "sent",
            "partial_received",
            "received",
            "partial_billed",
            "billed",
            "closed",
            "cancelled",
        ]
    ] = Query("all"),
    vendor_id: Optional[str] = Query(None),
    search: Optional[str] = Query(
        None, description="Cari no. PO, vendor, no. referensi, catatan (semua kata wajib cocok)"
    ),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    # 22 Okt 2026 (P-U2, aditif): page/per_page/offset di samping skip/limit (prioritas offset > page > skip; per_page = alias limit)
    page: int = Query(1, ge=1),
    per_page: Optional[int] = Query(None, ge=1, le=100),
    offset: Optional[int] = Query(None, ge=0),
    sort_by: Literal["po_date", "po_number", "total_amount", "created_at"] = Query(
        "created_at"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    aksi: Optional[Literal["perlu_dikirim", "telat_terima", "perlu_ditagih"]] = Query(None),
    with_totals: bool = Query(False),
):
    """Daftar Pesanan Pembelian. Aditif (P-U2): terima_*, position_text, aksi (pill PARTISI), amount_*/sisa_ditagih TURUNAN dari baris,
    documents/doc_count, ?aksi=, ?with_totals=true (sum_total/sum_sisa atas SEMUA hasil filter), page/per_page/offset + has_more,
    urutan deterministik (`, id`)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        _i = lambda v, d: v if isinstance(v, int) and not isinstance(v, bool) else d  # noqa: E731  (panggilan langsung = objek Query)
        _limit = _i(per_page, None) or _i(limit, 20)
        _offset = _i(offset, None)
        _page, _skip = _i(page, 1), _i(skip, 0)
        _offset = _offset if _offset is not None and _offset > 0 else ((_page - 1) * _limit if _page > 1 else _skip)

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
            hari_ini = await PD.tanggal_hari_ini(conn, ctx["tenant_id"])

            # $1 = tenant, $2 = tanggal bisnis (predikat telat_terima)
            conditions = ["po.tenant_id = $1", "$2::date IS NOT NULL"]  # $2 SELALU dirujuk (asyncpg menolak parameter tak terpakai)
            params = [ctx["tenant_id"], hari_ini]
            param_idx = 3

            if isinstance(status, str) and status != "all":
                conditions.append(f"po.status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if isinstance(vendor_id, str) and vendor_id:
                conditions.append(f"po.vendor_id = ${param_idx}")
                params.append(UUID(vendor_id))
                param_idx += 1

            cari, par_cari = PD.syarat_cari(search, param_idx)
            if cari:
                conditions.append(cari)
                params.extend(par_cari)
                param_idx += len(par_cari)

            if isinstance(date_from, date):
                conditions.append(f"po.po_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if isinstance(date_to, date):
                conditions.append(f"po.po_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            if isinstance(aksi, str) and aksi in PD.AKSI:  # panggilan langsung tanpa aksi = objek Query -> mati
                conditions.append(PD.sql_aksi(aksi, "$2"))

            where_clause = " AND ".join(conditions)
            dari = f"FROM purchase_orders po {PD.SQL_AGREGAT}"

            valid_sorts = {
                "po_date": "po.po_date",
                "po_number": "po.po_number",
                "total_amount": "po.total_amount",
                "created_at": "po.created_at",
            }
            sort_field = valid_sorts.get(sort_by if isinstance(sort_by, str) else "created_at", "po.created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            total = await conn.fetchval(f"SELECT COUNT(*) {dari} WHERE {where_clause}", *params)

            rows = await conn.fetch(
                f"""
                SELECT po.id, po.po_number, po.vendor_id, po.vendor_name, po.po_date, po.expected_date, po.total_amount,
                       po.status, po.ref_no, po.created_at
                {dari}
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}, po.id
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params, _limit, _offset,
            )

            ids = [row["id"] for row in rows]
            fakta = await PD.fakta_po(conn, ctx["tenant_id"], ids, hari_ini)
            kel = await PD.dokumen_po(conn, ctx["tenant_id"], ids)

            items = []
            for row in rows:
                f = fakta[row["id"]]
                dok = PD.documents_baris(kel.get(row["id"], []))
                items.append({
                    "id": str(row["id"]),
                    "po_number": row["po_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "po_date": row["po_date"].isoformat(),
                    "expected_date": row["expected_date"].isoformat() if row["expected_date"] else None,
                    "total_amount": float(row["total_amount"]),
                    "amount_received": float(f["amount_received"]),
                    "amount_billed": float(f["amount_billed"]),
                    "sisa_ditagih": float(f["sisa_ditagih"]),
                    "status": row["status"],
                    "ref_no": row["ref_no"],
                    "created_at": row["created_at"].isoformat(),
                    "terima_terbuka": f["terima_terbuka"], "terima_hari": f["terima_hari"],
                    "terima_keadaan": f["terima_keadaan"], "terima_teks": f["terima_teks"],
                    "position_text": f["position_text"], "aksi": f["aksi"],
                    "documents": dok, "doc_count": len(dok),
                })

            hasil = {"items": items, "total": total, "has_more": (_offset + _limit) < total}
            if with_totals is True:  # panggilan langsung (uji) = objek Query -> mati; agregat atas WHERE yang SAMA dengan COUNT (bukan halaman)
                t = await conn.fetchrow(
                    f"""SELECT COALESCE(SUM(po.total_amount) FILTER (WHERE po.status <> 'cancelled'), 0) AS sum_total,
                               COALESCE(SUM({PD.sql_sisa_ditagih()}), 0) AS sum_sisa
                        {dari} WHERE {where_clause}""", *params)
                hasil["sum_total"] = float(PD.r2(t["sum_total"]))
                hasil["sum_sisa"] = float(PD.r2(t["sum_sisa"]))
            return hasil

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing purchase orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat daftar pesanan pembelian.")


# =============================================================================
# PENDING PURCHASE ORDERS
# =============================================================================


@router.get("/pending", response_model=PurchaseOrderListResponse)
async def list_pending_purchase_orders(
    request: Request,
    vendor_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List pending purchase orders (sent but not fully received/billed)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            conditions = [
                "tenant_id = $1",
                "status IN ('sent', 'partial_received', 'received', 'partial_billed')",
            ]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if vendor_id:
                conditions.append(f"vendor_id = ${param_idx}")
                params.append(UUID(vendor_id))
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM purchase_orders WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, po_number, vendor_id, vendor_name,
                       po_date, expected_date, total_amount,
                       amount_received, amount_billed, status, ref_no, created_at
                FROM purchase_orders
                WHERE {where_clause}
                ORDER BY expected_date ASC NULLS LAST, po_date ASC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "po_number": row["po_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "po_date": row["po_date"].isoformat(),
                    "expected_date": row["expected_date"].isoformat()
                    if row["expected_date"]
                    else None,
                    "total_amount": row["total_amount"],
                    "amount_received": row["amount_received"] or 0,
                    "amount_billed": row["amount_billed"] or 0,
                    "status": row["status"],
                    "ref_no": row["ref_no"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing pending purchase orders: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Gagal memuat daftar pesanan pembelian yang menunggu."
        )


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary", response_model=PurchaseOrderSummaryResponse)
async def get_purchase_orders_summary(request: Request, search: Optional[str] = Query(None)):
    """Ringkasan Pesanan Pembelian. P-U2: jumlah uang TURUNAN dari baris PO (bukan kolom cache), ?search= (predikat daftar) membatasi SEMUA
    hitungan/nilai, + <aksi>_count/<aksi>_value (predikat SAMA dgn ?aksi= daftar)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
            hari_ini = await PD.tanggal_hari_ini(conn, ctx["tenant_id"])
            cari, par_cari = PD.syarat_cari(search, 3)
            w = f" AND {cari}" if cari else ""
            pill = ", ".join(
                f"COUNT(*) FILTER (WHERE {PD.sql_aksi(k, '$2')}) AS {k}_count, "
                f"COALESCE(SUM(po.total_amount) FILTER (WHERE {PD.sql_aksi(k, '$2')}), 0) AS {k}_value" for k in PD.AKSI)
            query = f"""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE po.status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE po.status = 'sent') as sent_count,
                    COUNT(*) FILTER (WHERE po.status IN ('partial_received', 'received')) as received_count,
                    COUNT(*) FILTER (WHERE po.status IN ('partial_billed', 'billed')) as billed_count,
                    COUNT(*) FILTER (WHERE po.status = 'closed') as closed_count,
                    COUNT(*) FILTER (WHERE po.status = 'cancelled') as cancelled_count,
                    COALESCE(SUM(po.total_amount) FILTER (WHERE po.status <> 'cancelled'), 0) as total_value,
                    COALESCE(SUM(ag.amount_received) FILTER (WHERE po.status <> 'cancelled'), 0) as total_received,
                    COALESCE(SUM(ag.amount_billed) FILTER (WHERE po.status <> 'cancelled'), 0) as total_billed,
                    COALESCE(SUM(GREATEST(po.total_amount - ag.amount_billed, 0))
                        FILTER (WHERE po.status IN ('sent', 'partial_received', 'received', 'partial_billed')), 0) as pending_amount,
                    {pill}
                FROM purchase_orders po {PD.SQL_AGREGAT}
                WHERE po.tenant_id = $1{w}
            """
            row = await conn.fetchrow(query, ctx["tenant_id"], hari_ini, *par_cari)

            data = {
                "total": row["total"] or 0,
                "draft_count": row["draft_count"] or 0,
                "sent_count": row["sent_count"] or 0,
                "received_count": row["received_count"] or 0,
                "billed_count": row["billed_count"] or 0,
                "closed_count": row["closed_count"] or 0,
                "cancelled_count": row["cancelled_count"] or 0,
                "total_value": float(PD.r2(row["total_value"])),
                "total_received": float(PD.r2(row["total_received"])),
                "total_billed": float(PD.r2(row["total_billed"])),
                "pending_amount": float(PD.r2(row["pending_amount"])),
            }
            for k in PD.AKSI:
                data[f"{k}_count"] = row[f"{k}_count"] or 0
                data[f"{k}_value"] = float(PD.r2(row[f"{k}_value"]))
            return {"success": True, "data": data}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting purchase orders summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat ringkasan pesanan pembelian.")


# =============================================================================
# GET PURCHASE ORDER DETAIL
# =============================================================================


@router.get("/{po_id}", response_model=PurchaseOrderDetailResponse)
async def get_purchase_order(request: Request, po_id: UUID):
    """Get detailed information for a purchase order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            # Get PO
            po = await conn.fetchrow(
                """
                SELECT * FROM purchase_orders
                WHERE id = $1 AND tenant_id = $2
            """,
                po_id,
                ctx["tenant_id"],
            )

            if not po:
                raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")

            # Get items
            items = await conn.fetch(
                """
                SELECT * FROM purchase_order_items
                WHERE po_id = $1
                ORDER BY line_number
            """,
                po_id,
            )

            # Get linked bills
            # P-U2: kolom `bills.bill_date` TIDAK ADA (kolomnya issue_date) -> detail PO dulu selalu 500; kunci JSON `bill_date` dipertahankan
            bills = await conn.fetch(
                """
                SELECT id, invoice_number, issue_date AS bill_date, grand_total, COALESCE(status_v2, status) as status
                FROM bills
                WHERE purchase_order_id = $1 AND tenant_id = $2
                ORDER BY issue_date, invoice_number
            """,
                po_id,
                ctx["tenant_id"],
            )

            # paritas detail == baris: fungsi SAMA dengan daftar (services/po_daftar)
            hari_ini = await PD.tanggal_hari_ini(conn, ctx["tenant_id"])
            f = (await PD.fakta_po(conn, ctx["tenant_id"], [po_id], hari_ini))[po_id]
            dok = PD.documents_baris((await PD.dokumen_po(conn, ctx["tenant_id"], [po_id])).get(po_id, []))

            return {
                "success": True,
                "data": {
                    "id": str(po["id"]),
                    "po_number": po["po_number"],
                    "vendor_id": str(po["vendor_id"]) if po["vendor_id"] else None,
                    "vendor_name": po["vendor_name"],
                    "subtotal": float(po["subtotal"] or 0),
                    "discount_percent": float(po["discount_percent"] or 0),
                    "discount_amount": float(po["discount_amount"] or 0),
                    "tax_rate": float(po["tax_rate"] or 0),
                    "tax_amount": float(po["tax_amount"] or 0),
                    "total_amount": float(po["total_amount"]),
                    "amount_received": float(f["amount_received"]),
                    "amount_billed": float(f["amount_billed"]),
                    "sisa_ditagih": float(f["sisa_ditagih"]),
                    "terima_terbuka": f["terima_terbuka"], "terima_hari": f["terima_hari"],
                    "terima_keadaan": f["terima_keadaan"], "terima_teks": f["terima_teks"],
                    "position_text": f["position_text"], "aksi": f["aksi"],
                    "documents": dok, "doc_count": len(dok),
                    "status": po["status"],
                    "po_date": po["po_date"].isoformat(),
                    "expected_date": po["expected_date"].isoformat()
                    if po["expected_date"]
                    else None,
                    "ship_to_address": po["ship_to_address"],
                    "ref_no": po["ref_no"],
                    "notes": po["notes"],
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "quantity_received": float(item["quantity_received"] or 0),
                            "quantity_billed": float(item["quantity_billed"] or 0),
                            "unit": item["unit"],
                            "unit_price": float(item["unit_price"] or 0),
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": float(item["discount_amount"] or 0),
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": float(item["tax_amount"] or 0),
                            "subtotal": float(item["subtotal"] or 0),
                            "total": float(item["total"] or 0),
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "bills": [
                        {
                            "id": str(bill["id"]),
                            "invoice_number": bill["invoice_number"],
                            "bill_date": bill["bill_date"].isoformat()
                            if bill["bill_date"]
                            else None,
                            "grand_total": float(bill["grand_total"] or 0),
                            "status": bill["status"],
                        }
                        for bill in bills
                    ],
                    "sent_at": po["sent_at"].isoformat() if po["sent_at"] else None,
                    "sent_by": str(po["sent_by"]) if po["sent_by"] else None,
                    "cancelled_at": po["cancelled_at"].isoformat()
                    if po["cancelled_at"]
                    else None,
                    "cancelled_by": str(po["cancelled_by"])
                    if po["cancelled_by"]
                    else None,
                    "cancelled_reason": po["cancelled_reason"],
                    "closed_at": po["closed_at"].isoformat()
                    if po["closed_at"]
                    else None,
                    "closed_by": str(po["closed_by"]) if po["closed_by"] else None,
                    "created_at": po["created_at"].isoformat(),
                    "updated_at": po["updated_at"].isoformat(),
                    "created_by": str(po["created_by"]) if po["created_by"] else None,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat pesanan pembelian.")


@router.get("/{po_id}/history")
async def get_purchase_order_history(request: Request, po_id: UUID, limit: int = Query(200, ge=1, le=500)):
    """Riwayat Pesanan Pembelian, bentuk SAMA dengan GET /sales-orders/{id}/history (services/po_riwayat): siklus PO beraktor + audit
    (diubah, barang diterima) + tagihan bertaut (izin baca purchase_invoice; tanpa izin -> `omitted`). Terbaru dulu."""
    from ..services.dashboard_izin import boleh_baca
    from ..services.po_riwayat import riwayat_po
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_po(conn, ctx["tenant_id"], po_id, lambda m: boleh_baca(request, m), limit if isinstance(limit, int) else 200)
    if data is None:
        raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")
    return {"success": True, "data": data}


@router.get("/{po_id}/documents")
async def get_purchase_order_documents(request: Request, po_id: UUID):
    """Bundel panel berjangkar PESANAN PEMBELIAN (P-U2): skema SAMA dgn bundel SO/faktur {anchor, default, groups[{key,label,docs[]}]};
    pembentuk SATU tempat (services/po_daftar.dokumen_po) = sumber kolom documents[] daftar. paid/balance jangkar = null (PO bukan dokumen uang)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
            po = await conn.fetchrow(
                "SELECT id, po_number, vendor_name, vendor_id, total_amount, status FROM purchase_orders WHERE id = $1 AND tenant_id = $2",
                po_id, ctx["tenant_id"])
            if not po:
                raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")
            kel = (await PD.dokumen_po(conn, ctx["tenant_id"], [po_id])).get(po_id, [])
            kontak = await conn.fetchrow(
                "SELECT phone, email FROM vendors WHERE id = $1 AND tenant_id = $2", po["vendor_id"], ctx["tenant_id"]) if po["vendor_id"] else None
            return {
                "anchor": {"kind": "purchase_order", "id": str(po["id"]), "number": po["po_number"], "vendor": po["vendor_name"] or "",
                           "position_text": PD.position_text(po["status"]), "total": f"{PD.r2(po['total_amount']):.2f}", "paid": None, "balance": None,
                           "vendor_phone": (kontak["phone"] if kontak else None) or None,
                           "vendor_email": (kontak["email"] if kontak else None) or None},
                "default": {"group": "po", "index": 0},
                "groups": kel,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting purchase order documents {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat dokumen pesanan pembelian.")


# =============================================================================
# CREATE PURCHASE ORDER (DRAFT)
# =============================================================================


@router.post("", response_model=PurchaseOrderResponse, status_code=201)
async def create_purchase_order(request: Request, body: CreatePurchaseOrderRequest, response: Response = None):
    """
    Buat Pesanan Pembelian (draf). PO TIDAK membuat jurnal -- jurnal lahir saat Bill diposting.

    P-U2 (22 Okt 2026): idempotensi X-Idempotency-Key (kunci sama + isi sama = respons asli; isi beda = 409), vendor_id wajib milik tenant,
    uang Decimal 2dp HALF_UP (services/po_hitung), diskon & pajak header ikut tersimpan.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")
        kunci = _kunci_idem(request)

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                kp, sidik, lama = await idem_buat.mulai(conn, ctx, kunci, "PO_CREATE", body, "purchase_orders", "po", response)
                if lama is not None:
                    return lama

                vendor = await _vendor_tenant(conn, ctx["tenant_id"], body.vendor_id)

                po_number = await conn.fetchval(
                    "SELECT generate_purchase_order_number($1, 'PO')", ctx["tenant_id"]
                )

                baris = [PH.hitung_baris(item.model_dump()) for item in body.items]
                tot = PH.hitung_header(baris, body.discount_percent, body.discount_amount, body.tax_rate)

                po_id = await conn.fetchval(
                    """
                    INSERT INTO purchase_orders (
                        tenant_id, po_number, vendor_id, vendor_name,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, po_date, expected_date,
                        ship_to_address, ref_no, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                              'draft', $11, $12, $13, $14, $15, $16)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    po_number,
                    vendor["id"] if vendor else None,
                    body.vendor_name,
                    tot["subtotal"],
                    Decimal(str(body.discount_percent)),
                    tot["discount_amount"],
                    Decimal(str(body.tax_rate)),
                    tot["tax_amount"],
                    tot["total_amount"],
                    body.po_date,
                    body.expected_date,
                    body.ship_to_address,
                    body.ref_no,
                    body.notes,
                    ctx["user_id"],
                )

                await _simpan_baris(conn, po_id, baris)
                t = await _hitung_ulang_total(conn, po_id)  # sumber angka tersimpan = baris DB (satu jalur dgn patch)

                logger.info(f"Purchase order created: {po_id}, number={po_number}")

                resp = {
                    "success": True,
                    "message": "Pesanan pembelian dibuat.",
                    "data": {
                        "id": str(po_id),
                        "po_number": po_number,
                        "total_amount": t["total_amount"],
                        "status": "draft",
                    },
                }
                return await idem_buat.simpan(conn, ctx, kp, sidik, "PO_CREATE", resp, po_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating purchase order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membuat pesanan pembelian.")


# =============================================================================
# UPDATE PURCHASE ORDER (DRAFT/SENT ONLY)
# =============================================================================


@router.patch("/{po_id}", response_model=PurchaseOrderResponse)
async def update_purchase_order(
    request: Request, po_id: UUID, body: UpdatePurchaseOrderRequest
):
    """
    Ubah pesanan pembelian (hanya Draf atau Terkirim). Kunci PO + baca ulang di dalam lock; total dihitung ulang dari baris + diskon/pajak
    header sesudah SETIAP perubahan (baris ATAU diskon/pajak header).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                po = await _kunci_po(conn, ctx["tenant_id"], po_id, "id, status, po_number")
                if not po:
                    raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")

                if po["status"] not in ("draft", "sent"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pesanan pembelian berstatus '{po['status']}' tidak bisa diubah. Hanya Draf atau Terkirim.",
                    )

                update_data = body.model_dump(exclude_unset=True)
                if not update_data:
                    return {"success": True, "message": "Tidak ada perubahan.", "data": {"id": str(po_id)}}

                ubah_uang = False
                if update_data.get("vendor_id"):
                    await _vendor_tenant(conn, ctx["tenant_id"], update_data["vendor_id"])

                if "items" in update_data and update_data["items"]:
                    await conn.execute("DELETE FROM purchase_order_items WHERE po_id = $1", po_id)
                    baris = [PH.hitung_baris(item.model_dump()) for item in body.items]
                    await _simpan_baris(conn, po_id, baris)
                    ubah_uang = True
                update_data.pop("items", None)

                if update_data:
                    updates, params = [], []
                    for i, (field, value) in enumerate(update_data.items(), 1):
                        updates.append(f"{field} = ${i}")
                        if field == "vendor_id":
                            params.append(UUID(value) if value else None)
                        elif field in ("discount_percent", "discount_amount", "tax_rate"):
                            ubah_uang = True
                            params.append(Decimal(str(value if value is not None else 0)))
                        else:
                            params.append(value)
                    params.extend([po_id, ctx["tenant_id"]])
                    await conn.execute(
                        f"UPDATE purchase_orders SET {', '.join(updates)}, updated_at = NOW() "  # nosec B608 -- nama kolom dari skema, bukan masukan
                        f"WHERE id = ${len(updates) + 1} AND tenant_id = ${len(updates) + 2}",
                        *params,
                    )

                if ubah_uang:
                    await _hitung_ulang_total(conn, po_id)

                await catat_riwayat(conn, ctx["tenant_id"], "purchase_order", po_id, po["po_number"], "PO_UPDATED", ctx["user_id"],
                                    f"Pesanan pembelian {po['po_number']} diubah", meta={"fields": sorted(body.model_dump(exclude_unset=True))},
                                    source="api:purchase_orders")

                logger.info(f"Purchase order updated: {po_id}")

                return {
                    "success": True,
                    "message": "Pesanan pembelian diperbarui.",
                    "data": {"id": str(po_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memperbarui pesanan pembelian.")


# =============================================================================
# DELETE PURCHASE ORDER (DRAFT ONLY)
# =============================================================================


@router.delete("/{po_id}", response_model=PurchaseOrderResponse)
async def delete_purchase_order(request: Request, po_id: UUID):
    """Hapus pesanan pembelian berstatus Draf. Yang sudah terkirim dibatalkan (cancel), bukan dihapus."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # V230: trigger trg_log_deletion membaca app.user_id yang HANYA hidup di dalam transaksi -> SET dan DELETE satu transaksi.
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
                await conn.execute("SELECT set_config('app.user_id', $1, true)", str(ctx["user_id"] or ""))
                po = await _kunci_po(conn, ctx["tenant_id"], po_id, "id, status, po_number")
                if not po:
                    raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")
                if po["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail="Hanya pesanan pembelian berstatus Draf yang bisa dihapus. Yang sudah terkirim dibatalkan.",
                    )
                await conn.execute("DELETE FROM purchase_orders WHERE id = $1 AND tenant_id = $2", po_id, ctx["tenant_id"])

            logger.info(f"Purchase order deleted: {po_id}")

            return {
                "success": True,
                "message": "Pesanan pembelian dihapus.",
                "data": {"id": str(po_id), "po_number": po["po_number"]},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menghapus pesanan pembelian.")


# =============================================================================
# SEND PURCHASE ORDER
# =============================================================================


@router.post("/{po_id}/send", response_model=PurchaseOrderResponse)
async def send_purchase_order(request: Request, po_id: UUID):
    """Tandai pesanan pembelian terkirim ke vendor (Draf -> Terkirim). Kunci PO; status dibaca di dalam lock."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
                po = await _kunci_po(conn, ctx["tenant_id"], po_id, "id, status, po_number")
                if not po:
                    raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")
                if po["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dikirim. Hanya Draf.",
                    )
                await conn.execute(
                    """
                    UPDATE purchase_orders
                    SET status = 'sent', sent_at = NOW(), sent_by = $2, updated_at = NOW()
                    WHERE id = $1
                """,
                    po_id,
                    ctx["user_id"],
                )

            logger.info(f"Purchase order sent: {po_id}")

            return {
                "success": True,
                "message": "Pesanan pembelian ditandai terkirim.",
                "data": {"id": str(po_id), "po_number": po["po_number"], "status": "sent"},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal mengirim pesanan pembelian.")


# =============================================================================
# RECEIVE GOODS
# =============================================================================


@router.post("/{po_id}/receive", response_model=PurchaseOrderResponse)
async def receive_goods(request: Request, po_id: UUID, body: ReceiveGoodsRequest, response: Response = None):
    """
    Record goods receipt for a purchase order.

    Updates quantity_received for specified items.
    NOTE: This does NOT create journal entries.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 14 (P-U2): X-Idempotency-Key -> kirim ulang (retry/klik ganda) = respons asli, BUKAN penerimaan kedua
                kp, sidik, lama = await idem_buat.mulai_aksi(
                    conn, ctx, _kunci_idem(request), "PO_RECEIVE", po_id, lambda: body.model_dump(mode="json"), response)
                if lama is not None:
                    return lama

                # Law 13 / P-U1 G1: PO dikunci (FOR UPDATE) sebelum baca sisa -> dua penerimaan bersamaan tak bisa melebihi qty
                po = await conn.fetchrow(
                    """
                    SELECT * FROM purchase_orders
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                """,
                    po_id,
                    ctx["tenant_id"],
                )

                if not po:
                    raise HTTPException(
                        status_code=404, detail="Pesanan pembelian tidak ditemukan."
                    )

                if po["status"] not in ("sent", "partial_received"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Barang tidak bisa diterima untuk pesanan pembelian berstatus '{po['status']}'.",
                    )

                # receive_date dulu DIABAIKAN: kini divalidasi (tanggal bisnis tenant; tidak sebelum tanggal PO). Penyimpanan
                # tanggal per penerimaan menunggu dokumen GRN (P-U4); sampai itu penghitung kumulatif tak punya kolom tanggal.
                _hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
                if body.receive_date > _hari_ini:
                    raise HTTPException(status_code=400, detail="Tanggal terima tidak boleh di masa depan.")
                if body.receive_date < po["po_date"]:
                    raise HTTPException(status_code=400, detail="Tanggal terima tidak boleh sebelum tanggal pesanan.")

                items_received = []

                for recv_item in body.items:
                    # Get PO item
                    po_item = await conn.fetchrow(
                        """
                        SELECT * FROM purchase_order_items
                        WHERE id = $1 AND po_id = $2
                        FOR UPDATE
                    """,
                        UUID(recv_item.po_item_id),
                        po_id,
                    )

                    if not po_item:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Baris pesanan {recv_item.po_item_id} tidak ditemukan.",
                        )

                    # Check quantity (Decimal murni -- Law 25)
                    _qty_terima = Decimal(str(recv_item.quantity_received))
                    remaining = Decimal(str(po_item["quantity"])) - Decimal(
                        str(po_item["quantity_received"] or 0)
                    )
                    if _qty_terima > remaining:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Jumlah diterima ({_qty_terima}) melebihi sisa ({remaining}).",
                        )

                    # Update quantity_received
                    new_qty_received = (
                        Decimal(str(po_item["quantity_received"] or 0))
                        + _qty_terima
                    )

                    await conn.execute(
                        """
                        UPDATE purchase_order_items
                        SET quantity_received = $2
                        WHERE id = $1
                    """,
                        UUID(recv_item.po_item_id),
                        new_qty_received,
                    )

                    items_received.append(
                        {
                            "po_item_id": recv_item.po_item_id,
                            "quantity_received": _qty_terima,
                            "total_received": new_qty_received,
                        }
                    )

                # Nilai diterima = TURUNAN baris (qty_diterima x total / qty, 2dp HALF_UP per baris); dulu ::BIGINT memotong sen
                all_items = await conn.fetch(
                    """
                    SELECT quantity, quantity_received, quantity_billed, total FROM purchase_order_items
                    WHERE po_id = $1
                """,
                    po_id,
                )
                total_received = PD.jumlah_turunan([dict(i) for i in all_items])["amount_received"]

                all_received = all(
                    Decimal(str(item["quantity_received"] or 0)) >= Decimal(str(item["quantity"]))
                    for item in all_items
                )

                new_status = "received" if all_received else "partial_received"

                await conn.execute(
                    """
                    UPDATE purchase_orders
                    SET amount_received = $2, status = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    po_id,
                    total_received,
                    new_status,
                )

                await catat_riwayat(conn, ctx["tenant_id"], "purchase_order", po_id, po["po_number"], "PO_RECEIVED", ctx["user_id"],
                                    f"Barang diterima untuk {po['po_number']}: {len(items_received)} baris (tanggal terima {body.receive_date.isoformat()})",
                                    meta={"items": [{"po_item_id": i["po_item_id"], "qty": str(i["quantity_received"])} for i in items_received]},
                                    source="api:purchase_orders")

                logger.info(
                    f"Goods received for PO: {po_id}, items={len(items_received)}"
                )

                resp = {
                    "success": True,
                    "message": f"Barang diterima: {len(items_received)} baris.",
                    "data": {
                        "id": str(po_id),
                        "items_received": items_received,
                        "total_received": total_received,
                        "status": new_status,
                    },
                }
                return await idem_buat.simpan(conn, ctx, kp, sidik, "PO_RECEIVE", resp, po_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error receiving goods for PO {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menerima barang.")


# =============================================================================
# CONVERT TO BILL
# =============================================================================


@router.post("/{po_id}/to-bill", response_model=PurchaseOrderResponse)
async def convert_to_bill(request: Request, po_id: UUID, body: ConvertToBillRequest, response: Response = None):
    """
    Convert purchase order to a Bill.

    Creates a Bill with items from the PO.
    NOTE: Journal entry is created when Bill is posted, not here.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

                # Law 14 (P-U2): kirim ulang dengan X-Idempotency-Key sama = respons asli, BUKAN tagihan kedua
                kp, sidik, lama = await idem_buat.mulai_aksi(
                    conn, ctx, _kunci_idem(request), "PO_TOBILL", po_id, lambda: body.model_dump(mode="json"), response)
                if lama is not None:
                    return lama

                # Law 13 (P-U2): PO dikunci (advisory + FOR UPDATE) sebelum baca sisa tertagih -> dua konversi bersamaan tak bisa menagih ganda
                po = await _kunci_po(conn, ctx["tenant_id"], po_id)

                if not po:
                    raise HTTPException(
                        status_code=404, detail="Pesanan pembelian tidak ditemukan."
                    )

                billable_statuses = (
                    "sent",
                    "partial_received",
                    "received",
                    "partial_billed",
                )
                if po["status"] not in billable_statuses:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dibuatkan tagihan.",
                    )

                # Get items to bill
                if body.items:
                    # Specific items
                    items_to_bill = []
                    for bill_item in body.items:
                        po_item = await conn.fetchrow(
                            """
                            SELECT * FROM purchase_order_items
                            WHERE id = $1 AND po_id = $2
                            FOR UPDATE
                        """,
                            UUID(bill_item.po_item_id),
                            po_id,
                        )

                        if not po_item:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Baris pesanan {bill_item.po_item_id} tidak ditemukan.",
                            )

                        unbilled = Decimal(po_item["quantity"]) - Decimal(
                            po_item["quantity_billed"] or 0
                        )
                        qty_to_bill = Decimal(str(bill_item.quantity_to_bill)) if bill_item.quantity_to_bill else unbilled

                        if qty_to_bill > unbilled:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Jumlah ditagih ({qty_to_bill}) melebihi sisa belum tertagih ({unbilled}).",
                            )

                        items_to_bill.append(
                            {"po_item": po_item, "quantity": qty_to_bill}
                        )
                else:
                    # All unbilled items
                    po_items = await conn.fetch(
                        """
                        SELECT * FROM purchase_order_items
                        WHERE po_id = $1 AND quantity > COALESCE(quantity_billed, 0)
                        ORDER BY line_number
                        FOR UPDATE
                    """,
                        po_id,
                    )

                    items_to_bill = [
                        {
                            "po_item": item,
                            "quantity": Decimal(item["quantity"])
                            - Decimal(item["quantity_billed"] or 0),
                        }
                        for item in po_items
                    ]

                if not items_to_bill:
                    raise HTTPException(
                        status_code=400, detail="Tidak ada baris yang belum ditagih."
                    )

                # Generate bill number
                bill_number = await conn.fetchval(
                    "SELECT generate_bill_number($1, 'BILL')", ctx["tenant_id"]
                )

                if not bill_number:
                    # Fallback if function doesn't exist
                    bill_number = f"BILL-{po['po_number']}"

                # D: satu rumus dgn bill manual (create_bill_v2). BillCalculator
                # per-baris HALF_UP + pajak per-item 2dp HALF_UP (simetris penjualan).
                # Nol int() -> sen harga terjaga; PO == bill manual by construction.
                from ..services.bills_service import BillCalculator

                def _po_disc_pct(po_item, qty, unit_price):
                    if po_item["discount_percent"] and po_item["discount_percent"] > 0:
                        return Decimal(str(po_item["discount_percent"]))
                    amt = po_item["discount_amount"] or 0
                    gross = qty * unit_price
                    if amt and gross and po_item["quantity"]:
                        prorated = (
                            Decimal(str(amt)) * qty / Decimal(str(po_item["quantity"]))
                        )
                        return prorated / gross * Decimal("100")
                    return Decimal("0")

                _po_lines = []
                for item in items_to_bill:
                    po_item = item["po_item"]
                    qty = Decimal(str(item["quantity"]))
                    unit_price = Decimal(str(po_item["unit_price"]))
                    disc_pct = _po_disc_pct(po_item, qty, unit_price)
                    ic = BillCalculator.calculate_item_total(qty, unit_price, disc_pct)
                    tax_rate = Decimal(str(po_item["tax_rate"] or 0))
                    # DPP = subtotal (cermin create_bill_v2), pajak 2dp HALF_UP
                    item_tax = (
                        float(
                            (
                                Decimal(str(ic["subtotal"])) * tax_rate / Decimal("100")
                            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        )
                        if tax_rate > 0
                        else 0
                    )
                    _po_lines.append(
                        {
                            "po_item": po_item,
                            "qty": qty,
                            "unit_price": unit_price,
                            "disc_pct": disc_pct,
                            "ic": ic,
                            "tax_rate": float(po_item["tax_rate"] or 0),
                            "tax": item_tax,
                        }
                    )

                _calc = BillCalculator.calculate(
                    items=[
                        {
                            "qty": l["qty"],
                            "price": l["unit_price"],
                            "discount_percent": l["disc_pct"],
                        }
                        for l in _po_lines
                    ],
                    invoice_discount_percent=Decimal("0"),
                    invoice_discount_amount=0.0,
                    cash_discount_percent=Decimal("0"),
                    cash_discount_amount=0.0,
                    tax_rate=0,
                    # tarif header 0: pajak PO dijumlah per baris di bawah -> tanpa DPP-NL.
                    dpp_factor=(1, 1),
                )
                bill_subtotal = _calc["subtotal"]
                bill_tax = float(sum(Decimal(str(l["tax"])) for l in _po_lines))
                bill_total = float(
                    (
                        Decimal(str(_calc["grand_total"])) + Decimal(str(bill_tax))
                    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )

                # Create Bill
                bill_id = await conn.fetchval(
                    """
                    INSERT INTO bills (
                        tenant_id, invoice_number, vendor_id, vendor_name,
                        issue_date, due_date, amount, grand_total,
                        subtotal, tax_amount,
                        purchase_order_id, notes, status_v2, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'draft', $13)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    bill_number,
                    po["vendor_id"],
                    po["vendor_name"],
                    body.bill_date,
                    body.due_date or body.bill_date,
                    bill_total,
                    bill_total,
                    bill_subtotal,
                    bill_tax,
                    po_id,
                    body.notes or f"Dari pesanan pembelian {po['po_number']}",
                    ctx["user_id"],
                )

                # Create bill items (D: pakai _po_lines pra-hitung; nilai == bill manual)
                for idx, l in enumerate(_po_lines, 1):
                    po_item = l["po_item"]
                    ic = l["ic"]

                    await conn.execute(
                        """
                        INSERT INTO bill_items (
                            bill_id, product_id, product_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_rate, tax_amount,
                            subtotal, total, line_number, purchase_order_item_id
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        bill_id,
                        po_item["item_id"],
                        po_item["item_code"],
                        po_item["description"],
                        l["qty"],
                        po_item["unit"],
                        l["unit_price"],
                        float(l["disc_pct"]),
                        ic["discount_amount"],
                        l["tax_rate"],
                        l["tax"],
                        ic["subtotal"],
                        ic["total"],
                        idx,
                        po_item["id"],  # V436: tautan baris PO -> qty tertagih TURUNAN (PD.sinkron_tagih), pulih saat bill dihapus/void
                    )

                # qty/jumlah tertagih + status PO TURUNAN dari bill_items bertaut (bill tak-void; draf ikut = pola NetSuite 'Partially Billed' sejak
                # bill dibuat dari PO). Satu fungsi dgn pemulihan saat bill dihapus/void (dipanggil BACKEND dari bills.py).
                hasil_tagih = await PD.sinkron_tagih(conn, ctx["tenant_id"], po_id)
                new_status = hasil_tagih["status"]

                logger.info(f"Bill created from PO: {po_id}, bill={bill_id}")

                resp = {
                    "success": True,
                    "message": "Tagihan dibuat dari pesanan pembelian.",
                    "data": {
                        "id": str(po_id),
                        "bill_id": str(bill_id),
                        "bill_number": bill_number,
                        "bill_total": bill_total,
                        "po_status": new_status,
                    },
                }
                return await idem_buat.simpan(conn, ctx, kp, sidik, "PO_TOBILL", resp, po_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting PO to bill {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membuat tagihan dari pesanan pembelian.")


# =============================================================================
# CANCEL PURCHASE ORDER
# =============================================================================


@router.post("/{po_id}/cancel", response_model=PurchaseOrderResponse)
async def cancel_purchase_order(
    request: Request, po_id: UUID, body: CancelPurchaseOrderRequest
):
    """Batalkan pesanan pembelian. Hanya bila belum ada barang diterima dan belum ada tagihan (diukur dari BARIS, bukan kolom cache)."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
                po = await _kunci_po(conn, ctx["tenant_id"], po_id, "id, status")
                if not po:
                    raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")
                if po["status"] in ("cancelled", "closed"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pesanan pembelian berstatus '{po['status']}' tidak bisa dibatalkan.",
                    )
                q = await conn.fetchrow(
                    "SELECT COALESCE(SUM(quantity_received), 0) AS diterima, COALESCE(SUM(quantity_billed), 0) AS ditagih "
                    "FROM purchase_order_items WHERE po_id = $1", po_id)
                if Decimal(str(q["diterima"])) > 0:
                    raise HTTPException(status_code=400, detail="Pesanan pembelian yang barangnya sudah diterima tidak bisa dibatalkan.")
                if Decimal(str(q["ditagih"])) > 0:
                    raise HTTPException(status_code=400, detail="Pesanan pembelian yang sudah ditagih tidak bisa dibatalkan.")

                await conn.execute(
                    """
                    UPDATE purchase_orders
                    SET status = 'cancelled', cancelled_at = NOW(),
                        cancelled_by = $2, cancelled_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    po_id,
                    ctx["user_id"],
                    body.reason,
                )

            logger.info(f"Purchase order cancelled: {po_id}")

            return {
                "success": True,
                "message": "Pesanan pembelian dibatalkan.",
                "data": {"id": str(po_id), "status": "cancelled"},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membatalkan pesanan pembelian.")


# =============================================================================
# CLOSE PURCHASE ORDER
# =============================================================================


@router.post("/{po_id}/close", response_model=PurchaseOrderResponse)
async def close_purchase_order(request: Request, po_id: UUID):
    """Tutup pesanan pembelian (selesai manual, mis. sisa barang tak akan datang). Kunci PO; status dibaca di dalam lock."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="ID pengguna wajib ada.")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", ctx["tenant_id"])
                po = await _kunci_po(conn, ctx["tenant_id"], po_id, "id, status")
                if not po:
                    raise HTTPException(status_code=404, detail="Pesanan pembelian tidak ditemukan.")
                if po["status"] in ("draft", "cancelled", "closed"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Pesanan pembelian berstatus '{po['status']}' tidak bisa ditutup.",
                    )
                await conn.execute(
                    """
                    UPDATE purchase_orders
                    SET status = 'closed', closed_at = NOW(),
                        closed_by = $2, updated_at = NOW()
                    WHERE id = $1
                """,
                    po_id,
                    ctx["user_id"],
                )

            logger.info(f"Purchase order closed: {po_id}")

            return {
                "success": True,
                "message": "Pesanan pembelian ditutup.",
                "data": {"id": str(po_id), "status": "closed"},
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing purchase order {po_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal menutup pesanan pembelian.")


# =============================================================================
# LIST PURCHASE ORDERS FOR VENDOR
# =============================================================================


@router.get("/vendor/{vendor_id}", response_model=PurchaseOrderListResponse)
async def list_purchase_orders_by_vendor(
    request: Request,
    vendor_id: UUID,
    status: Optional[str] = Query("all"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List purchase orders for a specific vendor."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

            conditions = ["tenant_id = $1", "vendor_id = $2"]
            params = [ctx["tenant_id"], vendor_id]
            param_idx = 3

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM purchase_orders WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            query = f"""
                SELECT id, po_number, vendor_id, vendor_name,
                       po_date, expected_date, total_amount,
                       amount_received, amount_billed, status, ref_no, created_at
                FROM purchase_orders
                WHERE {where_clause}
                ORDER BY po_date DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "po_number": row["po_number"],
                    "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                    "vendor_name": row["vendor_name"],
                    "po_date": row["po_date"].isoformat(),
                    "expected_date": row["expected_date"].isoformat()
                    if row["expected_date"]
                    else None,
                    "total_amount": row["total_amount"],
                    "amount_received": row["amount_received"] or 0,
                    "amount_billed": row["amount_billed"] or 0,
                    "status": row["status"],
                    "ref_no": row["ref_no"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing POs for vendor {vendor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat daftar pesanan pembelian vendor.")
