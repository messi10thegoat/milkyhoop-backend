"""
Sales Orders Router
Order management with shipment tracking.
NO journal entries - accounting impact happens on Invoice creation.
"""
from fastapi import APIRouter, HTTPException, Request, Query, Response
from typing import Optional, Literal
from datetime import date
from decimal import Decimal
import asyncpg
import logging
import uuid as uuid_module
from ..services import faktur_cetak as _fc_snap
from ..services.so_faktur_draf import penanda_faktur_so
from ..services.so_posisi import fakta_daftar  # P1 SO-dokumen: Posisi/Kirim/Dok. (1 Okt 2026)
from ..utils.tanggal_tenant import tanggal_dokumen
from ..services.pihak_helpers import pelanggan_kanonik_tenant  # pelanggan WAJIB satu tenant (30 Sep)
from ..utils.idempotency import (
    ambil_replay_klien,
    hash_payload,
    kunci_idempotensi_klien,
    simpan_replay_klien,
)
from ..services.sales_doc_calc import (
    compute_document, plan_so_invoice, DocumentDiscountError, d as _dd,
)
from ..services.tax_factor import (
    attach_dpp_factors, resolve_shipping_tax, effective_shipping_code, turunkan_tarif_baris,
)
from ..services.pkp_guard import tolak_ppn_bila_non_pkp
from ..services import so_agregat
from ..services import so_kirim
from ..services.so_riwayat import catat_riwayat, riwayat_so
from ..services.default_dokumen import default_pesanan, hitung_dp
from ..services.termin_bayar import tentukan_jatuh_tempo, termin_hari
from ..services.dashboard_izin import boleh_baca

from ..schemas.sales_orders import (
    CreateSalesOrderRequest,
    UpdateSalesOrderRequest,
    CreateShipmentRequest,
    ConvertToInvoiceRequest,
    CancelSalesOrderRequest,
    CloseSalesOrderRequest,
    SalesOrderListResponse,
    SalesOrderDetailResponse,
    SalesOrderResponse,
    SalesOrderSummaryResponse,
    PendingOrdersResponse,
    SalesOrderListItem,
    SalesOrderDetail,
    SalesOrderDepositSummary,
    SalesOrderItemResponse,
    ShipmentDetail,
    ShipmentItemResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id") or user.get("id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {
        "tenant_id": tenant_id,
        "user_id": uuid_module.UUID(user_id) if user_id else None,
    }


def _so_doc(items: list, discount_amount, shipping_amount, shipping_tax=None) -> dict:
    """SO memakai kalkulator bersama (services/sales_doc_calc.py) -- menggantikan
    salinan lokal yang memotong pajak dengan int() (Law 9) dan mengurangkan diskon
    dokumen SESUDAH PPN. Arti kolom SO dipertahankan: subtotal header = SIGMA neto baris
    (sesudah diskon baris, sebelum diskon dokumen); line_total = neto baris + PPN baris.
    """
    try:
        doc = compute_document(
            items, doc_discount_amount=discount_amount or 0,
            shipping_amount=shipping_amount or 0, shipping_tax=shipping_tax,
        )
    except DocumentDiscountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for ln in doc["items"]:
        ln["line_total"] = ln["total"]
    return doc


# ============================================================================
# LIST & DETAIL ENDPOINTS
# ============================================================================


@router.get("", response_model=SalesOrderListResponse)
async def list_sales_orders(
    request: Request,
    status: Optional[
        Literal[
            "all",
            "draft",
            "confirmed",
            "partial_shipped",
            "shipped",
            "partial_invoiced",
            "invoiced",
            "completed",
            "cancelled",
        ]
    ] = Query("all"),
    customer_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """List sales orders with filters."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}")
                params.append(uuid_module.UUID(customer_id))
                param_idx += 1

            if start_date:
                conditions.append(f"order_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"order_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(order_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = sales_orders.tenant_id AND c.search_text ILIKE ${param_idx}))"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(order_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = sales_orders.tenant_id AND c.search_text ILIKE ${param_idx}))"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            where_clause = " AND ".join(conditions)

            count_query = f"SELECT COUNT(*) FROM sales_orders WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            list_query = f"""
                SELECT id, order_number, order_date, expected_ship_date, customer_id, customer_name,
                       subtotal, discount_amount, tax_amount, shipping_amount, total_amount,
                       status, shipped_qty, invoiced_qty, created_at, quote_id, order_code, order_title
                FROM sales_orders
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(list_query, *params)
            # Q-016 (a): penanda faktur DRAF per SO (status tetap) — satu kueri, sumber = quantity_invoiced
            penanda = await penanda_faktur_so(conn, ctx["tenant_id"], [row["id"] for row in rows])
            # P1 SO-dokumen: Posisi/Kirim/jumlah dokumen dari SATU fungsi (services/so_posisi), tanggal usaha zona tenant
            posisi = await fakta_daftar(conn, ctx["tenant_id"], rows, await tanggal_dokumen(conn, ctx["tenant_id"]))

            items = [
                SalesOrderListItem(
                    id=str(row["id"]),
                    order_number=row["order_number"],
                    order_code=row["order_code"],
                    order_title=row["order_title"],
                    order_date=row["order_date"].isoformat(),
                    expected_ship_date=row["expected_ship_date"].isoformat()
                    if row["expected_ship_date"]
                    else None,
                    customer_id=str(row["customer_id"]),
                    customer_name=row["customer_name"],
                    subtotal=row["subtotal"],
                    discount_amount=row["discount_amount"],
                    tax_amount=row["tax_amount"],
                    shipping_amount=row["shipping_amount"],
                    total_amount=row["total_amount"],
                    status=row["status"],
                    shipped_qty=float(row["shipped_qty"] or 0),
                    invoiced_qty=float(row["invoiced_qty"] or 0),
                    **penanda[str(row["id"])],
                    **posisi[str(row["id"])],
                    created_at=row["created_at"].isoformat(),
                )
                for row in rows
            ]

            return SalesOrderListResponse(
                items=items, total=total, has_more=(skip + limit) < total
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing sales orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list sales orders")


@router.get("/pending", response_model=PendingOrdersResponse)
async def get_pending_orders(
    request: Request, action: Literal["shipment", "invoice", "all"] = Query("all")
):
    """Get orders pending shipment or invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            if action == "shipment":
                statuses = ["confirmed", "partial_shipped"]
            elif action == "invoice":
                statuses = ["shipped", "partial_shipped", "partial_invoiced"]
            else:
                statuses = [
                    "confirmed",
                    "partial_shipped",
                    "shipped",
                    "partial_invoiced",
                ]

            query = """
                SELECT so.id, so.order_number, so.customer_name, so.order_date, so.total_amount, so.status,
                       COALESCE(SUM(soi.quantity - soi.quantity_shipped), 0) as pending_ship,
                       COALESCE(SUM(soi.quantity - soi.quantity_invoiced), 0) as pending_invoice
                FROM sales_orders so
                LEFT JOIN sales_order_items soi ON so.id = soi.sales_order_id
                WHERE so.tenant_id = $1 AND so.status = ANY($2)
                GROUP BY so.id
                ORDER BY so.order_date ASC
            """
            rows = await conn.fetch(query, ctx["tenant_id"], statuses)

            items = []
            for row in rows:
                pending_qty = (
                    float(row["pending_ship"])
                    if row["status"] in ["confirmed", "partial_shipped"]
                    else float(row["pending_invoice"])
                )
                pending_action = (
                    "shipment"
                    if row["status"] in ["confirmed", "partial_shipped"]
                    else "invoice"
                )

                items.append(
                    {
                        "id": str(row["id"]),
                        "order_number": row["order_number"],
                        "customer_name": row["customer_name"],
                        "order_date": row["order_date"].isoformat(),
                        "total_amount": row["total_amount"],
                        "status": row["status"],
                        "pending_qty": pending_qty,
                        "pending_action": pending_action,
                    }
                )

            return PendingOrdersResponse(success=True, data=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting pending orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get pending orders")


@router.get("/summary", response_model=SalesOrderSummaryResponse)
async def get_sales_order_summary(request: Request):
    """Get sales order statistics summary.
    pending_shipment_value/_count = MENUNGGU KIRIM NYATA (27 Sep): baris PERLU DIKIRIM dgn sisa qty > 0
    (so_kirim.ringkasan_menunggu_kirim), BUKAN status SO; pending_invoice_value = Σ total SO per STATUS (lama);
    uninvoiced_value = belum ditagih NYATA (turunan jurnal, Q-012) — pakai ini untuk "belum ditagih";
    uninvoiced_count = jumlah SO dengan sisa belum ditagih > 0 (sumber sama, aggregate?q=uninvoiced.count)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total_orders,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'confirmed') as confirmed_count,
                    COUNT(*) FILTER (WHERE status = 'partial_shipped') as partial_shipped_count,
                    COUNT(*) FILTER (WHERE status = 'shipped') as shipped_count,
                    COUNT(*) FILTER (WHERE status = 'partial_invoiced') as partial_invoiced_count,
                    COUNT(*) FILTER (WHERE status = 'invoiced') as invoiced_count,
                    COUNT(*) FILTER (WHERE status = 'completed') as completed_count,
                    COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled_count,
                    COALESCE(SUM(total_amount), 0) as total_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status IN ('shipped', 'partial_invoiced')), 0) as pending_invoice_value
                FROM sales_orders
                WHERE tenant_id = $1
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])
            belum = await so_agregat.uninvoiced(conn, ctx["tenant_id"])
            kirim = await so_kirim.ringkasan_belum_dikirim(conn, ctx["tenant_id"], so_agregat.AKTIF_TIDAK)
            menunggu = await so_kirim.ringkasan_menunggu_kirim(conn, ctx["tenant_id"], so_agregat.AKTIF_TIDAK)

            return SalesOrderSummaryResponse(
                success=True,
                data={
                    "total_orders": row["total_orders"],
                    "draft_count": row["draft_count"],
                    "confirmed_count": row["confirmed_count"],
                    "partial_shipped_count": row["partial_shipped_count"],
                    "shipped_count": row["shipped_count"],
                    "partial_invoiced_count": row["partial_invoiced_count"],
                    "invoiced_count": row["invoiced_count"],
                    "completed_count": row["completed_count"],
                    "cancelled_count": row["cancelled_count"],
                    "total_value": row["total_value"],
                    "pending_shipment_value": float(menunggu["total"]),
                    "pending_shipment_count": menunggu["count"],
                    "pending_invoice_value": row["pending_invoice_value"],
                    "uninvoiced_value": belum["total"],
                    "uninvoiced_count": belum["count"],   # jumlah SO bersisa (SEMUA, bukan baris terpotong 50)
                    "unshipped_value": float(kirim["total"]),
                    "unshipped_count": kirim["count"],
                    "fulfillment_count": await so_kirim.jumlah_surat_jalan(conn, ctx["tenant_id"]),
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sales order summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get sales order summary")


@router.get("/aggregate")
async def get_sales_order_aggregate(
    request: Request,
    q: Optional[str] = Query(None),
    dari: Optional[str] = Query(None, alias="from"),
    sampai: Optional[str] = Query(None, alias="to"),
    customer_id: Optional[str] = Query(None),
    limit: Optional[str] = Query(None),
):
    """Q-012: agregat SERVER untuk jawaban Workspace (FE tak menjumlah daftar). Parameter salah -> 400.
    Definisi di services/so_agregat.py; uang "belum ditagih" = turunan jurnal (sama dengan payment_summary)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await so_agregat.agregat(conn, ctx["tenant_id"], q, dari, sampai, customer_id, limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sales order aggregate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get sales order aggregate")


@router.get("/defaults")
async def get_sales_order_defaults(request: Request, customer_id: Optional[str] = None):
    """Default form Pesanan Penjualan (default bisa-ditimpa tahap 1, 30 Sep 2026): persen uang muka perusahaan +
    rekening penerimaan UTAMA (bendera eksplisit). null = tak ada default (FE membiarkan isian kosong). Server tak
    mengisi dokumen sendiri; FE mengirim nilainya (snapshot). customer_id diterima untuk tahap 2 (per pelanggan)."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await default_pesanan(conn, ctx["tenant_id"], customer_id)
    return {"success": True, "data": data}


@router.get("/{order_id}", response_model=SalesOrderDetailResponse)
async def get_sales_order_detail(request: Request, order_id: str):
    """Get sales order detail with items and shipments."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get order header (G4: join quotes for human-readable quote_number)
            order = await conn.fetchrow(
                """
                SELECT so.*, q.quote_number
                FROM sales_orders so
                LEFT JOIN quotes q ON q.id = so.quote_id
                WHERE so.id = $1 AND so.tenant_id = $2
            """,
                _so_uuid(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            # Get items
            items = await conn.fetch(
                """
                SELECT soi.*, p.nama_produk AS product_name, p.item_type AS product_item_type,
                       COALESCE(soi.perlu_kirim, p.track_inventory, false) AS requires_fulfillment
                FROM sales_order_items soi
                LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = $2
                WHERE soi.sales_order_id = $1 ORDER BY soi.sort_order, soi.id
            """,
                _so_uuid(order_id),
                ctx["tenant_id"],
            )

            # Get shipments
            shipments = await conn.fetch(
                """
                SELECT * FROM sales_order_shipments WHERE sales_order_id = $1 ORDER BY shipment_date DESC
            """,
                _so_uuid(order_id),
            )

            shipment_details = []
            for shp in shipments:
                shp_items = await conn.fetch(
                    """
                    SELECT si.*, soi.description
                    FROM sales_order_shipment_items si
                    JOIN sales_order_items soi ON si.sales_order_item_id = soi.id
                    WHERE si.shipment_id = $1
                """,
                    shp["id"],
                )

                shipment_details.append(
                    ShipmentDetail(
                        id=str(shp["id"]),
                        shipment_number=shp["shipment_number"],
                        shipment_date=shp["shipment_date"].isoformat(),
                        carrier=shp["carrier"],
                        tracking_number=shp["tracking_number"],
                        status=shp["status"],
                        items=[
                            ShipmentItemResponse(
                                id=str(si["id"]),
                                sales_order_item_id=str(si["sales_order_item_id"]),
                                description=si["description"],
                                quantity_shipped=float(si["quantity_shipped"]),
                            )
                            for si in shp_items
                        ],
                        created_at=shp["created_at"].isoformat(),
                        shipped_at=shp["shipped_at"].isoformat()
                        if shp["shipped_at"]
                        else None,
                        delivered_at=shp["delivered_at"].isoformat()
                        if shp["delivered_at"]
                        else None,
                    )
                )

            # Get related invoices
            invoices = await conn.fetch(
                """
                SELECT id, invoice_number, invoice_date, total_amount, status
                FROM sales_invoices WHERE sales_order_id = $1
            """,
                _so_uuid(order_id),
            )

            # G2: linked customer deposits (exclude void) + atribusi proforma (28 Sep 2026)
            from ..services.proforma_atribusi import uang_muka_so
            deposits = await uang_muka_so(conn, ctx["tenant_id"], order["id"])

            # Q-011: angka "Dibayar" dari SERVER, journal-derived (bukan Σ deposits[]/invoices[] di FE).
            ringkas = await ringkasan_pesanan(conn, ctx["tenant_id"], [order["id"]])
            payment_summary = ringkasan_pembayaran_so(order["total_amount"], ringkas[order["id"]])
            penanda = (await penanda_faktur_so(conn, ctx["tenant_id"], [order["id"]]))[str(order["id"])]
            terkirim, tanpa_tautan = await so_kirim.terkirim_per_baris(conn, ctx["tenant_id"], [order["id"]])

            termin_n, termin_sumber = await termin_hari(
                conn, ctx["tenant_id"], order.get("payment_terms"), order.get("customer_id"))
            from ..services.kode_order import boleh_ganti_kode
            # Boleh isi/ganti kode TAK bergantung pada kode sudah ada: tenant pemicu manual_only (dan SO yang belum
            # terbit kodenya) mengisi lewat jalur yang sama (pemilik: "nomor job order bisa diinput manual").
            _boleh_kode = await boleh_ganti_kode(conn, ctx["tenant_id"], ctx.get("user_id"))

            return SalesOrderDetailResponse(
                success=True,
                data=SalesOrderDetail(
                    order_code=order.get("order_code"),
                    order_title=order.get("order_title"),
                    order_code_source=order.get("order_code_source"),
                    order_code_can_override=_boleh_kode,
                    completed_source=order.get("completed_source"),  # V315 (.get: kode aman sebelum migrasi)
                    payment_terms_days=termin_n,
                    payment_terms_source=termin_sumber,
                    id=str(order["id"]),
                    order_number=order["order_number"],
                    order_date=order["order_date"].isoformat(),
                    expected_ship_date=order["expected_ship_date"].isoformat()
                    if order["expected_ship_date"]
                    else None,
                    customer_id=str(order["customer_id"]),
                    customer_name=order["customer_name"],
                    quote_id=str(order["quote_id"]) if order["quote_id"] else None,
                    quote_number=order["quote_number"],
                    reference=order["reference"],
                    shipping_address=order["shipping_address"],
                    shipping_method=order["shipping_method"],
                    subtotal=order["subtotal"],
                    discount_amount=order["discount_amount"],
                    tax_amount=order["tax_amount"],
                    # 3e: sama dengan SO /calculate. subtotal SO = NETO (setelah diskon baris).
                    line_tax_amount=float((order["tax_amount"] or 0) - (order.get("shipping_tax_amount") or 0)),
                    shipping_amount=order["shipping_amount"],
                    shipping_tax_code_id=str(order["shipping_tax_code_id"])
                    if order.get("shipping_tax_code_id")
                    else None,
                    shipping_tax_rate=float(order.get("shipping_tax_rate") or 0),
                    shipping_dpp=float(order.get("shipping_dpp") or 0),
                    shipping_tax_amount=float(order.get("shipping_tax_amount") or 0),
                    shipping_tax_code_id_effective=effective_shipping_code(
                        order.get("shipping_tax_code_id"), [dict(i) for i in items],
                        "tax_id", order.get("shipping_amount") or 0,
                    ),
                    total_amount=order["total_amount"],
                    status=order["status"],
                    shipped_qty=float(order["shipped_qty"] or 0),
                    fulfilled_qty_unlinked=float(tanpa_tautan.get(order["id"], 0)),
                    invoiced_qty=float(order["invoiced_qty"] or 0),
                    **penanda,
                    notes=order["notes"],
                    internal_notes=order["internal_notes"],
                    # T199: syarat DP yang dibawa dari Penawaran (V224).
                    dp_percent=order["dp_percent"],
                    dp_amount=order["dp_amount"],
                    dp_amount_source=order.get("dp_amount_source"),
                    payment_terms=order["payment_terms"],
                    payment_bank_name=order["payment_bank_name"],
                    payment_account_number=order["payment_account_number"],
                    payment_account_holder=order["payment_account_holder"],
                    items=[
                        SalesOrderItemResponse(
                            id=str(item["id"]),
                            item_id=str(item["item_id"]) if item["item_id"] else None,
                            description=item["description"],
                            product_name=item["product_name"],
                            quantity=float(item["quantity"]),
                            quantity_shipped=float(item["quantity_shipped"]),
                            quantity_invoiced=float(item["quantity_invoiced"]),
                            quantity_remaining=float(
                                item["quantity"] - item["quantity_shipped"]
                            ),
                            unit=item["unit"],
                            unit_price=item["unit_price"],
                            discount_percent=float(item["discount_percent"]),
                            tax_id=str(item["tax_id"]) if item["tax_id"] else None,
                            tax_rate=float(item["tax_rate"]),
                            tax_amount=item["tax_amount"],
                            line_total=item["line_total"],
                            line_net=item["line_total"] - (item["tax_amount"] or 0),
                            warehouse_id=str(item["warehouse_id"])
                            if item["warehouse_id"]
                            else None,
                            sort_order=item["sort_order"],
                            item_type=item["product_item_type"],
                            requires_fulfillment=bool(item["requires_fulfillment"]),
                            fulfilled_qty=float(terkirim.get(item["id"], 0)),
                            unfulfilled_qty=float(so_kirim.belum_dikirim(item["quantity"], terkirim.get(item["id"]))),
                            unfulfilled_value=float(so_kirim.nilai_belum_dikirim(  # neto = line_total - PPN baris
                                item["quantity"], item["line_total"] - (item["tax_amount"] or 0), terkirim.get(item["id"]))),
                        )
                        for item in items
                    ],
                    shipments=shipment_details,
                    payment_summary=payment_summary,
                    invoices=[
                        {
                            "id": str(inv["id"]),
                            "invoice_number": inv["invoice_number"],
                            "invoice_date": inv["invoice_date"].isoformat(),
                            "total_amount": inv["total_amount"],
                            "status": inv["status"],
                        }
                        for inv in invoices
                    ],
                    deposits=[SalesOrderDepositSummary(**dep) for dep in deposits],
                    created_at=order["created_at"].isoformat(),
                    updated_at=order["updated_at"].isoformat(),
                    created_by=str(order["created_by"])
                    if order["created_by"]
                    else None,
                    confirmed_at=order["confirmed_at"].isoformat()
                    if order["confirmed_at"]
                    else None,
                    confirmed_by=str(order["confirmed_by"])
                    if order["confirmed_by"]
                    else None,
                ),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sales order detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get sales order detail")


# ============================================================================
# CREATE, UPDATE, DELETE ENDPOINTS
# ============================================================================


@router.post("", response_model=SalesOrderResponse)
async def create_sales_order(request: Request, body: CreateSalesOrderRequest, response: Response):
    """Create a new sales order (draft status).

    W0 (Conversational Workspace): header X-Idempotency-Key (alias Idempotency-Key)
    -> kunci sama + isi sama dalam 24 jam = respons ASLI diulang (header
    X-Idempotent-Replay: true), tanpa SO kedua; kunci sama + isi beda = 409.
    Ruang kunci = (tenant, pengguna, kunci). Hanya respons SUKSES yang dicatat, di
    transaksi yang sama dengan SO-nya. Badan replay = badan ASLI saat dibuat (bukan
    status SO terkini). Tanpa header = perilaku lama (SO kembar yang sah tetap boleh).
    """
    try:
        ctx = get_user_context(request)
        _cek_uuid_badan_so(body)
        try:
            _kunci_klien = kunci_idempotensi_klien(request)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # W0: idempotency DULUAN — sebelum cek nomor manual / penomoran, supaya
                # pengulangan dengan order_number manual tak ditolak 409 "nomor dipakai".
                _kunci_penuh = _sidik = None
                if _kunci_klien:
                    _kunci_penuh = f"SO_CREATE:{ctx['user_id']}:{_kunci_klien}"
                    _sidik = hash_payload(body.model_dump(mode="json"))
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"IDEM:{ctx['tenant_id']}:{_kunci_penuh}",
                    )
                    try:
                        _lama = await ambil_replay_klien(
                            conn, ctx["tenant_id"], _kunci_penuh, _sidik
                        )
                    except LookupError as _e:
                        # Kode TETAP supaya FE membedakan dari 409 "nomor dipakai" tanpa
                        # mencocokkan teks; bentuk {code, message} = konvensi rute lain.
                        # Q-006 (Anton): + order_id/order_number SO yang tersimpan untuk kunci ini
                        # (FE: "Buka pesanan yang tersimpan"). Dibaca ULANG dari sales_orders milik
                        # tenant pemanggil: nomor terkini; SO sudah dihapus -> keduanya null.
                        _asli = (getattr(_e, "respons", None) or {}).get("data") or {}
                        _so = None
                        if _asli.get("id"):
                            _so = await conn.fetchrow(
                                "SELECT id, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                                uuid_module.UUID(str(_asli["id"])), ctx["tenant_id"],
                            )
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "IDEMPOTENCY_KEY_REUSED",
                                "message": "Idempotency-Key sudah dipakai untuk pesanan lain",
                                "order_id": str(_so["id"]) if _so else None,
                                "order_number": _so["order_number"] if _so else None,
                            },
                        )
                    if _lama is not None:
                        response.headers["X-Idempotent-Replay"] = "true"
                        return SalesOrderResponse(**_lama)

                from ..services.document_number import bersihkan_nomor_dokumen_opsional
                _nomor_manual = bersihkan_nomor_dokumen_opsional(getattr(body, "order_number", None))
                if _nomor_manual:
                    if await conn.fetchval(
                        "SELECT 1 FROM sales_orders WHERE tenant_id=$1 AND order_number=$2",
                        ctx["tenant_id"], _nomor_manual):
                        raise HTTPException(status_code=409, detail="Nomor sudah dipakai di tenant ini.")
                    order_number = _nomor_manual
                else:
                    order_number = await conn.fetchval(
                        "SELECT generate_sales_order_number($1, 'SO')", ctx["tenant_id"]
                    )

                _items = [item.model_dump() for item in body.items]
                await turunkan_tarif_baris(conn, ctx["tenant_id"], _items, "tax_id")  # #34
                await attach_dpp_factors(conn, ctx["tenant_id"], _items, "tax_id")
                _ship = await resolve_shipping_tax(
                    conn, ctx["tenant_id"], body.shipping_tax_code_id, _items,
                    body.shipping_amount, "tax_id",
                )
                _doc = _so_doc(_items, body.discount_amount, body.shipping_amount, _ship)
                await tolak_ppn_bila_non_pkp(conn, ctx["tenant_id"], _doc["tax_amount"])  # #34
                calculated_items = _doc["items"]
                totals = {
                    "subtotal": _doc["net_subtotal"],
                    "tax_amount": _doc["tax_amount"],
                    "total_amount": _doc["total_amount"],
                }
                # Uang muka: nominal diketik = timpa; hanya persen = nominal DIHITUNG SERVER dari total (default_dokumen)
                _dp = hitung_dp(_doc["total_amount"], body.dp_percent, body.dp_amount)

                # Pelanggan WAJIB milik tenant ini (30 Sep 2026; dulu hanya format UUID -> id tenant lain tersimpan).
                body.customer_id = await pelanggan_kanonik_tenant(conn, ctx["tenant_id"], body.customer_id)
                # Auto-resolve customer_name if not provided
                if not body.customer_name and body.customer_id:
                    cust = await conn.fetchrow(
                        "SELECT nama FROM customers WHERE id = $1 AND tenant_id = $2",
                        uuid_module.UUID(body.customer_id),
                        ctx["tenant_id"],
                    )
                    if cust:
                        body.customer_name = cust["nama"]

                from ..services.kode_order import KodeOrderGalat, normal_judul
                try:
                    _judul_order = normal_judul(body.order_title)
                except KodeOrderGalat as e:
                    raise HTTPException(status_code=422, detail=str(e))
                order_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO sales_orders (
                        id, tenant_id, order_number, order_date, expected_ship_date,
                        customer_id, customer_name, quote_id, reference,
                        shipping_address, shipping_method,
                        subtotal, discount_amount, tax_amount, shipping_amount, total_amount,
                        status, notes, internal_notes, created_by,
                        dp_percent, dp_amount, payment_terms,
                        payment_bank_name, payment_account_number,
                        payment_account_holder,
                        shipping_tax_code_id, shipping_tax_rate, shipping_tax_amount, shipping_dpp,
                        dp_amount_source, order_title
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, 'draft', $17, $18, $19,
                        $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31
                    )
                """,
                    order_id,
                    ctx["tenant_id"],
                    order_number,
                    body.order_date,
                    body.expected_ship_date,
                    uuid_module.UUID(body.customer_id),
                    body.customer_name,
                    uuid_module.UUID(body.quote_id) if body.quote_id else None,
                    body.reference,
                    body.shipping_address,
                    body.shipping_method,
                    totals["subtotal"],
                    body.discount_amount,
                    totals["tax_amount"],
                    body.shipping_amount,
                    totals["total_amount"],
                    body.notes,
                    body.internal_notes,
                    ctx["user_id"],
                    # DP ikut ditulis SEJAK PEMBUATAN ($20-$22) -- lewat hitung_dp (nominal dari persen = server).
                    _dp["dp_percent"],
                    _dp["dp_amount"],
                    body.payment_terms,
                    # Rekening tujuan cetak ($23-$25).
                    body.payment_bank_name,
                    body.payment_account_number,
                    await _fc_snap.pemilik_cetak(conn, ctx["tenant_id"], body.payment_bank_name, body.payment_account_number, body.payment_account_holder),  # 28 Sep: snapshot pemilik rekening, bukan nama akun
                    # V290: hanya pilihan eksplisit (NULL = ikut kode pajak barang).
                    uuid_module.UUID(body.shipping_tax_code_id) if body.shipping_tax_code_id else None,
                    _doc["shipping_tax_rate"],
                    _doc["shipping_tax_amount"],
                    _doc["shipping_dpp"],
                    _dp["dp_amount_source"],
                    _judul_order,
                )

                for idx, item in enumerate(calculated_items):
                    await conn.execute(
                        """
                        INSERT INTO sales_order_items (
                            id, sales_order_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total,
                            warehouse_id, sort_order, dpp
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    """,
                        uuid_module.uuid4(),
                        order_id,
                        uuid_module.UUID(item["item_id"])
                        if item.get("item_id")
                        else None,
                        item["description"],
                        item["quantity"],
                        item.get("unit"),
                        item["unit_price"],
                        item.get("discount_percent", 0),
                        uuid_module.UUID(item["tax_id"])
                        if item.get("tax_id")
                        else None,
                        item.get("tax_rate", 0),
                        item["tax_amount"],
                        item["line_total"],
                        uuid_module.UUID(item["warehouse_id"])
                        if item.get("warehouse_id")
                        else None,
                        item.get("sort_order", idx),
                        item["dpp"],
                    )

                _hasil = SalesOrderResponse(
                    success=True,
                    message="Sales order created successfully",
                    data={"id": str(order_id), "order_number": order_number},
                )
                if _kunci_penuh:
                    await simpan_replay_klien(
                        conn, ctx["tenant_id"], _kunci_penuh, "SALES_ORDER_CREATE",
                        _sidik, _hasil.model_dump(mode="json"), result_id=order_id,
                    )
                return _hasil

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create sales order")


@router.post("/calculate")
async def calculate_sales_order(request: Request, body: CreateSalesOrderRequest):
    """Unit 3d -- pratinjau SO TANPA menyimpan: kalkulator bersama yang SAMA dengan create
    (faktor DPP per kode, pajak ongkir). KONTRAK: mengembalikan PERSIS yang akan disimpan
    create. shipping_tax_code_id = pilihan eksplisit dari permintaan (null = ikut barang);
    shipping_tax_code_id_effective = kode yang benar-benar dipakai."""
    ctx = get_user_context(request)
    pool = await get_pool()
    _items = [item.model_dump() for item in body.items]
    async with pool.acquire() as conn:
        await turunkan_tarif_baris(conn, ctx["tenant_id"], _items, "tax_id")  # #34
        await attach_dpp_factors(conn, ctx["tenant_id"], _items, "tax_id")
        _ship = await resolve_shipping_tax(
            conn, ctx["tenant_id"], body.shipping_tax_code_id, _items,
            body.shipping_amount, "tax_id",
        )
        doc = _so_doc(_items, body.discount_amount, body.shipping_amount, _ship)
        await tolak_ppn_bila_non_pkp(conn, ctx["tenant_id"], doc["tax_amount"])  # #34
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    _dp = hitung_dp(doc["total_amount"], body.dp_percent, body.dp_amount)  # SAMA dengan create
    return {
        "success": True,
        "data": {
            "dp_percent": f(_dp["dp_percent"]),
            "dp_amount": f(_dp["dp_amount"]),
            "dp_amount_source": _dp["dp_amount_source"],
            "subtotal": f(doc["net_subtotal"]),
            "discount_amount": f(doc["doc_discount"]),
            "line_tax_amount": f(doc["line_tax_amount"]),
            "tax_amount": f(doc["tax_amount"]),
            "shipping_amount": f(doc["shipping_amount"]),
            "shipping_tax_code_id": body.shipping_tax_code_id or None,
            "shipping_tax_code_id_effective": doc["shipping_tax_code_id_effective"],
            "shipping_tax_rate": f(doc["shipping_tax_rate"]),
            "shipping_dpp": f(doc["shipping_dpp"]),
            "shipping_tax_amount": f(doc["shipping_tax_amount"]),
            "total_amount": f(doc["total_amount"]),
            "items": [
                {"line_number": i + 1, "description": ln.get("description"),
                 "quantity": f(ln.get("quantity")), "unit_price": f(ln.get("unit_price")),
                 "discount_percent": f(ln.get("discount_percent") or 0), "tax_rate": f(ln.get("tax_rate") or 0),
                 "tax_amount": f(ln["tax_amount"]), "line_total": f(ln["line_total"]), "dpp": f(ln["dpp"])}
                for i, ln in enumerate(doc["items"])
            ],
        },
    }


@router.patch("/{order_id}", response_model=SalesOrderResponse)
async def update_sales_order(
    request: Request, order_id: str, body: UpdateSalesOrderRequest
):
    """Update a sales order (draft only)."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()
        # Optimistic concurrency (opt-in If-Match): reject a stale write.
        from ..services.optimistic_concurrency import assert_if_match_row
        await assert_if_match_row(request, "sales_orders", order_id)

        async with pool.acquire() as conn:
            async with conn.transaction():
                order = await conn.fetchrow(
                    """
                    SELECT id, status FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE
                """,
                    _so_uuid(order_id),
                    ctx["tenant_id"],
                )

                if not order:
                    raise HTTPException(status_code=404, detail="Sales order not found")

                # V359 judul order: label, bukan uang -> boleh di SEMUA status. Diterapkan lewat jalur sendiri
                # (services/kode_order.ubah_judul: normalisasi + riwayat), lalu dibuang dari body supaya penjaga
                # status draf di bawah hanya berlaku untuk field lain.
                if "order_title" in body.model_fields_set:
                    from ..services.kode_order import KodeOrderGalat, ubah_judul
                    try:
                        await ubah_judul(conn, ctx["tenant_id"], order["id"], body.order_title, ctx["user_id"])
                    except KodeOrderGalat as e:
                        raise HTTPException(status_code=422, detail=str(e))
                    body.__pydantic_fields_set__.discard("order_title")
                    if not body.model_fields_set:
                        return SalesOrderResponse(
                            success=True, message="Sales order updated", data={"id": order_id}
                        )

                from ..services.document_number import bersihkan_nomor_dokumen_opsional
                _new_num = bersihkan_nomor_dokumen_opsional(getattr(body, "order_number", None))
                if _new_num is None:
                    body.__pydantic_fields_set__.discard("order_number")
                else:
                    if order["status"] != "draft":
                        raise HTTPException(status_code=400, detail="Nomor dokumen yang sudah terbit tidak dapat diubah")
                    if await conn.fetchval(
                        "SELECT 1 FROM sales_orders WHERE tenant_id=$1 AND order_number=$2 AND id <> $3",
                        ctx["tenant_id"], _new_num, _so_uuid(order_id)):
                        raise HTTPException(status_code=409, detail="Nomor sudah dipakai di tenant ini.")
                    body.order_number = _new_num

                if order["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft orders can be updated"
                    )

                updates = []
                params = []
                param_idx = 1

                # SEMANTIK PATCH: kunci ABSEN = jangan ubah; kunci TERKIRIM
                # (termasuk null / "") = terapkan, sehingga field BISA
                # DIKOSONGKAN.
                #
                # Bentuk lama membangun dict SELURUH field lalu melewati yang
                # bernilai None. Akibatnya "tidak dikirim" dan "dikirim sebagai
                # null" tak bisa dibedakan, dan MENGOSONGKAN `reference`
                # (juga dp_percent/dp_amount/payment_terms/payment_bank_*)
                # menjadi MUSTAHIL lewat API — satu-satunya jalan adalah SQL
                # langsung. Pengguna yang menghapus isi kolom lalu menyimpan
                # mendapat 200 dan nilai lamanya tetap berdiri.
                #
                # `exclude_unset=True` adalah pembedanya: Pydantic mencatat
                # field mana yang BENAR-BENAR dikirim (`model_fields_set`),
                # jadi null eksplisit ikut terbawa sementara yang absen tidak.
                # Ini pola yang sama dengan PATCH faktur penjualan.
                # `items` dikecualikan karena ditangani blok tersendiri di bawah
                # (hapus + sisip ulang baris), bukan kolom `sales_orders`.
                update_data = body.model_dump(exclude_unset=True, exclude={"items"})

                for field, value in update_data.items():
                    if field in ("dp_percent", "dp_amount"):
                        continue  # ditulis blok uang muka di bawah (sesudah total baru diketahui)
                    if field == "customer_id":
                        # customer_id tetap butuh cast uuid; null tetap boleh
                        # lewat (mengosongkan relasi) tanpa memanggil UUID(None).
                        if value is not None:
                            _c = await pelanggan_kanonik_tenant(conn, ctx["tenant_id"], value)  # satu tenant
                            value = uuid_module.UUID(_c) if _c else None
                        updates.append(f"{field} = ${param_idx}")
                    else:
                        updates.append(f"{field} = ${param_idx}")
                    params.append(value)
                    param_idx += 1

                # Hitung ulang bila BARIS, DISKON, atau ONGKIR berubah. Diskon dokumen kini
                # memengaruhi PPN tiap baris, jadi suntingan diskon/ongkir-saja WAJIB
                # menghitung ulang dari baris tersimpan -- dulu total_amount dibiarkan basi.
                _fs = body.model_fields_set
                _recalc = body.items is not None or bool(
                    {"discount_amount", "shipping_amount", "shipping_tax_code_id"} & _fs
                )
                if _recalc:
                    current = await conn.fetchrow(
                        "SELECT discount_amount, shipping_amount, shipping_tax_code_id FROM sales_orders WHERE id = $1",
                        _so_uuid(order_id),
                    )
                    discount_amt = (
                        body.discount_amount
                        if body.discount_amount is not None
                        else current["discount_amount"]
                    )
                    shipping_amt = (
                        body.shipping_amount
                        if body.shipping_amount is not None
                        else current["shipping_amount"]
                    )
                    if body.items is not None:
                        _src = [item.model_dump() for item in body.items]
                        await turunkan_tarif_baris(conn, ctx["tenant_id"], _src, "tax_id")  # #34
                    else:
                        _src = [
                            dict(r)
                            for r in await conn.fetch(
                                """SELECT id, quantity, unit_price, discount_percent, tax_rate, tax_id
                                   FROM sales_order_items WHERE sales_order_id = $1
                                   ORDER BY sort_order, id""",
                                _so_uuid(order_id),
                            )
                        ]
                    await attach_dpp_factors(conn, ctx["tenant_id"], _src, "tax_id")
                    _ship_code = (
                        (body.shipping_tax_code_id or None)
                        if "shipping_tax_code_id" in _fs
                        else (str(current["shipping_tax_code_id"]) if current["shipping_tax_code_id"] else None)
                    )
                    _ship = await resolve_shipping_tax(
                        conn, ctx["tenant_id"], _ship_code, _src, shipping_amt, "tax_id"
                    )
                    _doc = _so_doc(_src, discount_amt, shipping_amt, _ship)
                    await tolak_ppn_bila_non_pkp(conn, ctx["tenant_id"], _doc["tax_amount"])  # #34
                    calculated_items = _doc["items"]

                    for fld, val in [
                        ("subtotal", _doc["net_subtotal"]),
                        ("tax_amount", _doc["tax_amount"]),
                        ("total_amount", _doc["total_amount"]),
                        ("shipping_tax_rate", _doc["shipping_tax_rate"]),
                        ("shipping_tax_amount", _doc["shipping_tax_amount"]),
                        ("shipping_dpp", _doc["shipping_dpp"]),
                    ]:
                        updates.append(f"{fld} = ${param_idx}")
                        params.append(val)
                        param_idx += 1

                    if body.items is None:
                        # SO draft: compute_so_status mengembalikan 'draft' apa adanya,
                        # jadi UPDATE baris tak mengubah status (trg_update_so_status).
                        for _ln in calculated_items:
                            await conn.execute(
                                """UPDATE sales_order_items
                                   SET tax_amount = $2, line_total = $3, dpp = $4
                                   WHERE id = $1""",
                                _ln["id"], _ln["tax_amount"], _ln["line_total"], _ln["dpp"],
                            )

                # Uang muka (default bisa-ditimpa, 30 Sep): nominal diketik = 'manual' (terkunci); persen saja /
                # nominal dikosongkan dengan persen ada = 'percent' (dihitung server dari total); sumber 'percent'
                # dihitung ulang bila total berubah. Sumber NULL (SO lama) tak disentuh kecuali medan DP dikirim.
                _dp_kirim = {"dp_percent", "dp_amount"} & _fs
                _dp = None
                _kini = await conn.fetchrow(
                    "SELECT dp_percent, dp_amount, dp_amount_source, total_amount FROM sales_orders WHERE id = $1 AND tenant_id = $2",
                    _so_uuid(order_id), ctx["tenant_id"],
                ) if (_dp_kirim or _recalc) else None
                _total_baru = (_doc["total_amount"] if _recalc else _kini["total_amount"]) if _kini else None
                if _dp_kirim:
                    _pct = body.dp_percent if "dp_percent" in _fs else _kini["dp_percent"]
                    if "dp_amount" in _fs:
                        _amt = body.dp_amount
                    elif _kini["dp_amount_source"] == "manual":
                        _amt = _kini["dp_amount"]  # persen berubah, nominal diketik tetap terkunci
                    else:
                        _amt = None
                    _dp = hitung_dp(_total_baru, _pct, _amt)
                elif _recalc and _kini["dp_amount_source"] == "percent" and _kini["dp_percent"] is not None:
                    _dp = hitung_dp(_total_baru, _kini["dp_percent"], None)
                if _dp is not None:
                    for fld in ("dp_percent", "dp_amount", "dp_amount_source"):
                        updates.append(f"{fld} = ${param_idx}")
                        params.append(_dp[fld])
                        param_idx += 1

                if body.items is not None:
                    await conn.execute(
                        "DELETE FROM sales_order_items WHERE sales_order_id = $1",
                        _so_uuid(order_id),
                    )

                    for idx, item in enumerate(calculated_items):
                        await conn.execute(
                            """
                            INSERT INTO sales_order_items (
                                id, sales_order_id, item_id, description,
                                quantity, unit, unit_price, discount_percent,
                                tax_id, tax_rate, tax_amount, line_total,
                                warehouse_id, sort_order, dpp
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        """,
                            uuid_module.uuid4(),
                            _so_uuid(order_id),
                            uuid_module.UUID(item["item_id"])
                            if item.get("item_id")
                            else None,
                            item["description"],
                            item["quantity"],
                            item.get("unit"),
                            item["unit_price"],
                            item.get("discount_percent") or 0,
                            uuid_module.UUID(item["tax_id"])
                            if item.get("tax_id")
                            else None,
                            item.get("tax_rate") or 0,
                            item["tax_amount"],
                            item["line_total"],
                            uuid_module.UUID(item["warehouse_id"])
                            if item.get("warehouse_id")
                            else None,
                            item.get("sort_order") if item.get("sort_order") is not None else idx,
                            item["dpp"],
                        )

                if updates:
                    params.append(_so_uuid(order_id))
                    params.append(ctx["tenant_id"])
                    await conn.execute(
                        f"""
                        UPDATE sales_orders SET {', '.join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """,
                        *params,
                    )

                _ubah = sorted(set(update_data) | ({"items"} if body.items is not None else set()))
                if _ubah:
                    # Riwayat SO: 'diubah' tak punya kolom aktor -> audit_logs, tx yang sama (Law 12)
                    await catat_riwayat(
                        conn, ctx["tenant_id"], "sales_orders", order["id"], None, "SALES_ORDER_UPDATED",
                        ctx["user_id"], "Pesanan diubah (" + ", ".join(_ubah) + ")", {"fields": _ubah},
                        source="api:sales_orders.update",
                    )
                return SalesOrderResponse(
                    success=True, message="Sales order updated", data={"id": order_id}
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update sales order")


# ═════════════════════════════════════════════════════════════════════════
# GUARD UANG MUKA — dipakai OLEH DUA JALUR TERMINAL di modul ini.
#
# DP kanonik ditambatkan ke SALES ORDER, jadi ini jalur yang benar-benar
# dipakai tenant (terukur: 11 baris customer_deposits menunjuk sales_orders).
# Dua kebocoran terukur 2026-09-03, keduanya HTTP 200:
#   - `cancel` : SO jadi 'cancelled', DP tetap 'posted' menunjuk SO mati.
#   - `delete` : baris SO LENYAP dan FK `ON DELETE SET NULL` MENGHAPUS
#                tautannya -- DP bertahan dengan jurnal utuh tapi tanpa
#                penambat sama sekali, jadi asal-usulnya tak bisa dilacak
#                lagi. Ini lebih buruk daripada yatim biasa.
#
# Guard ini dipasang SESUDAH guard yang sudah ada (status, shipped_qty,
# invoiced_qty) supaya pesannya tidak bertabrakan: SO yang sudah dikirim
# atau difaktur tetap ditolak dengan alasan aslinya, dan pesan uang muka
# hanya muncul untuk SO yang sebetulnya masih boleh dibatalkan.
# ═════════════════════════════════════════════════════════════════════════

from ..services.dp_guard import tolak_bila_ada_uang_muka_aktif_pesanan
from ..services.proforma_terbayar import ringkasan_pembayaran_so, ringkasan_pesanan


async def _tolak_bila_ada_uang_muka_aktif(conn, order_id, tenant_id, aksi: str):
    # Q-011: dulu hanya customer_deposits.sales_order_id -> DP yang menunjuk proforma SO ini saja lolos.
    await tolak_bila_ada_uang_muka_aktif_pesanan(conn, order_id, tenant_id, aksi)


@router.delete("/{order_id}", response_model=SalesOrderResponse)
async def delete_sales_order(request: Request, order_id: str):
    """Delete a sales order (draft only)."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id, status, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2
            """,
                _so_uuid(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            if order["status"] != "draft":
                raise HTTPException(
                    status_code=400, detail="Only draft orders can be deleted"
                )

            await _tolak_bila_ada_uang_muka_aktif(
                conn, _so_uuid(order_id), ctx["tenant_id"], "dihapus"
            )

            # V230: siapa yang menghapus. Trigger `trg_log_deletion` membaca
            # `app.user_id`, dan GUC itu HANYA hidup di dalam transaksi —
            # terukur 2026-09-03: `SET LOCAL` sebagai statement lepas
            # menghasilkan WARNING "SET LOCAL can only be used in transaction
            # blocks" dan statement berikutnya membaca kosong. Karena itu SET
            # dan DELETE dibungkus SATU transaksi. `set_config(...)` dipakai
            # alih-alih `SET LOCAL` karena nilainya bisa diparameterkan,
            # sehingga tak ada interpolasi string ke dalam SQL.
            async with conn.transaction():
                # RACE (audit CW SO 26 Sep): cek status + guard DP dulu DI LUAR transaksi -> SO yang
                # dikonfirmasi / diberi DP di antaranya tetap terhapus. Kini baris SO DIKUNCI (mutex
                # bersama dengan cancel & pembuatan uang muka), guard diulang, DELETE bersyarat draft.
                await conn.execute(
                    "SELECT 1 FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    _so_uuid(order_id), ctx["tenant_id"],
                )
                await _tolak_bila_ada_uang_muka_aktif(conn, _so_uuid(order_id), ctx["tenant_id"], "dihapus")
                await conn.execute(
                    "SELECT set_config('app.user_id', $1, true)",
                    str(ctx["user_id"] or ""),
                )
                if not await conn.fetchval(
                    "DELETE FROM sales_orders WHERE id = $1 AND tenant_id = $2 AND status = 'draft' RETURNING id",
                    _so_uuid(order_id), ctx["tenant_id"],
                ):
                    raise HTTPException(status_code=409, detail="Pesanan sudah berubah status. Muat ulang halaman.")

            return SalesOrderResponse(
                success=True,
                message="Sales order deleted",
                data={"order_number": order["order_number"]},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete sales order")


# ============================================================================
# WORKFLOW ENDPOINTS
# ============================================================================


@router.post("/{order_id}/confirm", response_model=SalesOrderResponse)
async def confirm_sales_order(request: Request, order_id: str):
    """Confirm a sales order."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id, status, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2
            """,
                _so_uuid(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            if order["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot confirm order with status '{order['status']}'",
                )

            # UPDATE BERSYARAT status='draft': status dibaca di atas tanpa kunci -> tanpa syarat
            # ini dua permintaan bersamaan sama-sama 'berhasil' (konfirmasi menimpa aktor/waktu).
            ok = await conn.fetchval(
                """
                UPDATE sales_orders SET status = 'confirmed', confirmed_at = NOW(), confirmed_by = $3
                WHERE id = $1 AND tenant_id = $2 AND status = 'draft'
                RETURNING id
            """,
                _so_uuid(order_id),
                ctx["tenant_id"],
                ctx["user_id"],
            )
            if not ok:
                raise HTTPException(status_code=409, detail="Pesanan sudah berubah status. Muat ulang halaman.")

            return SalesOrderResponse(
                success=True,
                message="Sales order confirmed",
                data={"order_number": order["order_number"], "status": "confirmed"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to confirm sales order")


SO_TAK_BISA_BATAL = ("cancelled", "completed", "invoiced")


def _rp_teks(x) -> str:
    return "Rp" + f"{float(x):,.2f}".replace(",", "#").replace(".", ",").replace("#", ".").removesuffix(",00")


async def _rencana_batal_so(conn, ctx, order_id: str, reason) -> dict:
    """SATU-SATUNYA definisi aturan batal SO: dipakai POST /cancel DAN /cancel/preview (tanpa tulisan).
    SEMUA penghalang, urut = urutan /cancel lama: status -> ada kiriman/faktur -> uang muka DRAF.
    /cancel menaikkan blocks[0]["detail"] APA ADANYA (galat lama identik).
    Putusan pemilik 29 Sep (LANGSUNG di sesi BACKEND, pola SAP/NetSuite): uang muka POSTED/PARTIAL
    bukan penghalang -> tetap saldo uang muka pelanggan (nol jurnal); proforma TERBIT ikut dibatalkan.
    Uang muka DRAF tetap menghalangi: belum berjurnal, jadi bukan "saldo" -- posting atau hapus dulu.
    Pemanggil WAJIB sudah memegang FOR UPDATE baris SO (atau di transaksi yang di-rollback)."""
    order = await conn.fetchrow(
        """SELECT id, status, order_number, shipped_qty, invoiced_qty, total_amount FROM sales_orders
           WHERE id = $1 AND tenant_id = $2""",
        _so_uuid(order_id), ctx["tenant_id"],
    )
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    blocks, notes = [], []
    if order["status"] in SO_TAK_BISA_BATAL:
        blocks.append({
            "code": "SO_STATUS_NOT_CANCELLABLE",
            "message": f"Pesanan {order['order_number']} berstatus '{order['status']}' — tidak bisa dibatalkan.",
            "detail": f"Cannot cancel order with status '{order['status']}'",
        })
    if (order["shipped_qty"] or 0) > 0 or (order["invoiced_qty"] or 0) > 0:
        blocks.append({
            "code": "SO_HAS_SHIPMENTS_OR_INVOICES",
            "message": f"Pesanan {order['order_number']} sudah punya pengiriman atau faktur — tidak bisa dibatalkan.",
            "detail": "Cannot cancel order with shipments or invoices",
        })

    # uang muka DRAF (tertaut langsung / lewat proforma SO) -- predikat "aktif" dp_guard, status draft
    draf = await conn.fetch(
        """SELECT cd.id, cd.deposit_number, cd.amount FROM customer_deposits cd
           WHERE cd.tenant_id = $2 AND cd.status = 'draft'
             AND (cd.sales_order_id = $1
                  OR cd.proforma_id IN (SELECT p.id FROM proformas p WHERE p.tenant_id = $2 AND p.sales_order_id = $1))
           ORDER BY cd.deposit_number""",
        order["id"], ctx["tenant_id"],
    )
    if draf:
        pesan = (f"Pesanan {order['order_number']} punya uang muka draf ("
                 + ", ".join(d["deposit_number"] for d in draf)
                 + ") yang belum diposting. Posting atau hapus uang muka draf itu dulu.")
        blocks.append({"code": "SO_DEPOSIT_DRAFT", "message": pesan, "detail": pesan,
                       "deposits": [{"deposit_id": str(d["id"]), "deposit_number": d["deposit_number"]} for d in draf]})

    from .customer_deposits import compute_deposit_remaining, linked_so_deposits
    uang_muka = []
    for d in await linked_so_deposits(conn, ctx["tenant_id"], order["id"]):
        rem = await compute_deposit_remaining(conn, ctx["tenant_id"], d["id"])
        uang_muka.append({"deposit_id": str(d["id"]), "deposit_number": d["deposit_number"], "remaining": rem})
    tetap = [x for x in uang_muka if x["remaining"] > 0]
    if tetap:
        notes.append({"code": "SO_DEPOSIT_STAYS", "message": (
            "Uang muka " + ", ".join(f"{x['deposit_number']} (sisa {_rp_teks(x['remaining'])})" for x in tetap)
            + " tetap menjadi saldo uang muka pelanggan: bisa diterapkan ke faktur lain pelanggan ini atau dikembalikan.")})

    from .proformas import compute_paid_amount
    proforma = []
    for p in await conn.fetch(
        """SELECT id, proforma_number, status, amount FROM proformas
           WHERE tenant_id = $1 AND sales_order_id = $2 ORDER BY proforma_date, proforma_number NULLS LAST, created_at""",
        ctx["tenant_id"], order["id"],
    ):
        proforma.append({"proforma_id": str(p["id"]), "proforma_number": p["proforma_number"], "status": p["status"],
                         "amount": p["amount"], "paid": await compute_paid_amount(conn, ctx["tenant_id"], p["id"])})
    batal = [p for p in proforma if p["status"] == "issued"]
    if batal:
        notes.append({"code": "SO_PROFORMAS_CANCELLED", "message": (
            "Proforma " + ", ".join(p["proforma_number"] or "(tanpa nomor)" for p in batal)
            + " ikut dibatalkan bersama pesanan.")})
    return {"order": order, "blocks": blocks, "notes": notes, "deposits": uang_muka, "proformas": proforma}


@router.post("/{order_id}/cancel", response_model=SalesOrderResponse)
async def cancel_sales_order(
    request: Request, order_id: str, body: CancelSalesOrderRequest = None
):
    """Cancel a sales order. Aturan = _rencana_batal_so (dipakai juga /cancel/preview); penghalang pertama
    -> 400 dengan detail lama. Uang muka posted tetap saldo pelanggan (nol jurnal); proforma TERBIT SO ikut
    dibatalkan di transaksi yang sama (cancelled_reason menyebut SO, riwayat per proforma)."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()
        alasan_batal = ((body.reason if body else None) or "").strip() or None

        async with pool.acquire() as conn:
            async with conn.transaction():
                # mutex baris SO (sama dengan DELETE & pembuatan uang muka): uang muka/faktur/kirim yang
                # dibuat bersamaan menunggu, lalu rencana dibaca di bawah kunci yang sama.
                ada = await conn.fetchval(
                    "SELECT 1 FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                    _so_uuid(order_id), ctx["tenant_id"],
                )
                if not ada:
                    raise HTTPException(status_code=404, detail="Sales order not found")
                rencana = await _rencana_batal_so(conn, ctx, order_id, alasan_batal)
                if rencana["blocks"]:
                    raise HTTPException(status_code=400, detail=rencana["blocks"][0]["detail"])
                order = rencana["order"]
                # UPDATE BERSYARAT (sabuk kedua): syarat gagal -> 409, bukan SO batal berfaktur hidup.
                ok = await conn.fetchval(
                    """
                    UPDATE sales_orders SET status = 'cancelled'
                    WHERE id = $1 AND tenant_id = $2
                      AND status NOT IN ('cancelled', 'completed', 'invoiced')
                      AND COALESCE(shipped_qty, 0) = 0 AND COALESCE(invoiced_qty, 0) = 0
                    RETURNING id
                """,
                    _so_uuid(order_id),
                    ctx["tenant_id"],
                )
                if not ok:
                    raise HTTPException(status_code=409, detail="Pesanan sudah berubah (dikirim/difakturkan/diubah). Muat ulang halaman.")
                alasan_pf = f"Pesanan {order['order_number']} dibatalkan" + (f": {alasan_batal}" if alasan_batal else "")
                pf_batal = await conn.fetch(
                    """UPDATE proformas SET status = 'cancelled', cancelled_at = NOW(), cancelled_reason = $3
                       WHERE sales_order_id = $1 AND tenant_id = $2 AND status = 'issued'
                       RETURNING id, proforma_number""",
                    order["id"], ctx["tenant_id"], alasan_pf,
                )
                for p in pf_batal:
                    await catat_riwayat(
                        conn, ctx["tenant_id"], "proformas", p["id"], p["proforma_number"], "PROFORMA_CANCELLED",
                        ctx["user_id"], f"Proforma {p['proforma_number'] or ''} dibatalkan — {alasan_pf}".replace("  ", " "),
                        {"reason": alasan_batal, "sales_order_id": str(order["id"]), "sebab": "SO_CANCELLED"},
                        source="api:sales_orders.cancel",
                    )
                proformas_cancelled = [{"proforma_id": str(p["id"]), "proforma_number": p["proforma_number"]} for p in pf_batal]
                deposits_remaining = [{"deposit_id": x["deposit_id"], "deposit_number": x["deposit_number"],
                                       "remaining": float(x["remaining"])}
                                      for x in rencana["deposits"] if x["remaining"] > 0]
                await catat_riwayat(
                    conn, ctx["tenant_id"], "sales_orders", order["id"], order["order_number"],
                    "SALES_ORDER_CANCELLED", ctx["user_id"],
                    f"Pesanan {order['order_number']} dibatalkan" + (f": {alasan_batal}" if alasan_batal else ""),
                    {"reason": alasan_batal, "proformas_cancelled": proformas_cancelled,
                     "deposits_remaining": deposits_remaining},
                    source="api:sales_orders.cancel",
                )

            return SalesOrderResponse(
                success=True,
                message="Sales order cancelled",
                data={"order_number": order["order_number"], "status": "cancelled", "reason": alasan_batal,
                      "proformas_cancelled": proformas_cancelled, "deposits_remaining": deposits_remaining},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel sales order")


@router.post("/{order_id}/cancel/preview")
async def preview_cancel_sales_order(
    request: Request, order_id: str, body: CancelSalesOrderRequest = None
):
    """PRATINJAU batal SO: body SAMA dengan /cancel, NOL tulisan (transaksi SELALU di-ROLLBACK). Aturan =
    _rencana_batal_so. SEMUA penghalang; 200 walau tak bisa dibatalkan."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)
        pool = await get_pool()
        alasan = ((body.reason if body else None) or "").strip() or None
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                r = await _rencana_batal_so(conn, ctx, order_id, alasan)
            finally:
                await tr.rollback()  # SELALU: pratinjau tak pernah menulis
        o = r["order"]
        return {"success": True, "data": {
            "order_number": o["order_number"],
            "status": o["status"],
            "can_cancel": not r["blocks"],
            "status_after": "cancelled",
            "reason": alasan,
            "blocks": [{k: v for k, v in b.items() if k != "detail"} for b in r["blocks"]],
            "notes": r["notes"],
            "total_amount": _f(o["total_amount"]),
            "deposits": [{"deposit_id": x["deposit_id"], "deposit_number": x["deposit_number"],
                          "remaining": _f(x["remaining"]),
                          "after_cancel": "tetap_saldo" if x["remaining"] > 0 else None} for x in r["deposits"]],
            "proformas": [{"proforma_id": p["proforma_id"], "proforma_number": p["proforma_number"], "status": p["status"],
                           "amount": _f(p["amount"]), "paid": _f(p["paid"]),
                           "after_cancel": "dibatalkan" if p["status"] == "issued" else None} for p in r["proformas"]],
        }}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing cancel of sales order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to preview cancel")


SO_BISA_DITUTUP = ("confirmed", "invoiced", "shipped", "partial_invoiced", "partial_shipped")


async def _rencana_tutup_so(conn, ctx, order_id: str, reason) -> dict:
    """SATU-SATUNYA definisi aturan tutup SO: dipakai POST /close DAN /close/preview (tanpa tulisan).
    Mengembalikan SEMUA penghalang (tidak berhenti di yang pertama), urut = urutan /close dulu:
    status -> pendapatan tertahan -> belum terfakturkan penuh tanpa alasan.
    Uang muka bersisa BUKAN penghalang (putusan pemilik 28 Sep, pola NetSuite/SAP): ia tetap saldo
    uang muka pelanggan -> `notes` + deposits[].after_close='tetap_saldo'.
    /close menaikkan blocks[0]["detail"] APA ADANYA -> galatnya identik dengan sebelum refaktor.
    Nol tulisan di sini (lock xact saja)."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))", f"SO_CLOSE:{order_id}"
    )
    order = await conn.fetchrow(
        """
        SELECT id, status, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2
    """,
        _so_uuid(order_id),
        ctx["tenant_id"],
    )

    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")

    blocks = []
    # F1 (putusan pemilik 26 Sep): 'confirmed' (0 kirim/0 faktur) boleh ditutup = short close
    # SELURUH baris, alasan WAJIB (jatuh ke SO_NOT_FULLY_INVOICED tanpa alasan).
    if order["status"] not in SO_BISA_DITUTUP:
        blocks.append({
            "code": "SO_STATUS_NOT_CLOSABLE",
            "message": f"SO {order['order_number']} berstatus '{order['status']}' — tidak bisa ditutup.",
            "detail": f"Cannot close order with status '{order['status']}'",
        })

    from .customer_deposits import compute_deposit_remaining, linked_so_deposits

    # (b) uang muka bersisa -> TIDAK menghalangi (putusan pemilik 28 Sep, pola NetSuite Customer
    # Deposit / SAP down payment): menutup SO = kejadian NON-keuangan, nol jurnal; liabilitas uang
    # muka tak berubah dan tetap bisa DITERAPKAN ke faktur lain pelanggan yang SAMA (apply hanya
    # menjaga pihak sama, bukan SO) atau DIKEMBALIKAN (refund). Perencana otomatis tetap per-SO.
    uang_muka, notes = [], []
    for d in await linked_so_deposits(conn, ctx["tenant_id"], order["id"]):
        rem = await compute_deposit_remaining(conn, ctx["tenant_id"], d["id"])
        uang_muka.append({"deposit_id": str(d["id"]), "deposit_number": d["deposit_number"], "remaining": rem})
    sisa_dp = [x for x in uang_muka if x["remaining"] > 0]
    if sisa_dp:
        notes.append({
            "code": "SO_DEPOSIT_STAYS",
            "message": (
                "Uang muka "
                + ", ".join(f"{x['deposit_number']} (sisa Rp" + f"{float(x['remaining']):,.2f}".replace(",", "#").replace(".", ",").replace("#", ".").removesuffix(",00") + ")" for x in sisa_dp)
                + " tetap menjadi saldo uang muka pelanggan: bisa diterapkan ke faktur lain pelanggan ini atau dikembalikan."
            ),
        })

    # (c) F1: faktur SUDAH ditagih tapi pendapatan BELUM diakui (barang belum dikirim /
    # non-stok belum diakui) -> DITOLAK tanpa pengecualian: menutup di atasnya membuat
    # Pendapatan Diterima Dimuka tertahan selamanya. Kriteria = allocated - recognized
    # > 0.005 per baris (definisi SAMA dengan Check 16 & q018). Law 16.
    # (Nota kredit belum membuka ini: CN kini Dr Retur, tak menyentuh Dimuka -> BACKEND2.)
    tertahan = await conn.fetch(
        """
        SELECT si.invoice_number, sii.description,
               COALESCE(sii.allocated_amount, 0) - COALESCE(sii.recognized_amount, 0) AS sisa
        FROM sales_invoice_items sii
        JOIN sales_invoices si ON si.id = sii.invoice_id AND si.tenant_id = $2
        WHERE si.sales_order_id = $1 AND si.status NOT IN ('draft', 'void')
          AND COALESCE(sii.allocated_amount, 0) - COALESCE(sii.recognized_amount, 0) > 0.005
        ORDER BY si.invoice_number, sii.line_number NULLS LAST
    """,
        order["id"],
        ctx["tenant_id"],
    )
    if tertahan:
        detail = {
            "code": "SO_REVENUE_NOT_RECOGNIZED",
            "message": (
                f"SO {order['order_number']} tidak bisa ditutup: "
                + "; ".join(f"{t['invoice_number']} {t['description']}" for t in tertahan)
                + " sudah ditagih tetapi barangnya belum dikirim. "
                "Kirim barangnya / akui pendapatan non-stok dulu."
            ),
            "lines": [
                {"invoice_number": t["invoice_number"], "description": t["description"],
                 "unrecognized_amount": float(t["sisa"])}
                for t in tertahan
            ],
        }
        blocks.append({**detail, "detail": detail})

    # (a) qty pada faktur non-void yang TERTAUT, bukan pencacah quantity_invoiced
    lines = await conn.fetch(
        """
        SELECT soi.*,
               COALESCE((SELECT SUM(sii.quantity) FROM sales_invoice_items sii
                         JOIN sales_invoices si ON si.id = sii.invoice_id
                         WHERE sii.sales_order_item_id = soi.id AND si.status <> 'void'), 0) AS on_invoices
        FROM sales_order_items soi WHERE soi.sales_order_id = $1 ORDER BY soi.sort_order, soi.id
    """,
        order["id"],
    )
    kurang = [
        {"description": r["description"], "quantity_ordered": float(r["quantity"]),
         "quantity_on_invoices": float(r["on_invoices"])}
        for r in lines if r["on_invoices"] < r["quantity"]
    ]
    if kurang and not reason:
        detail = {
            "code": "SO_NOT_FULLY_INVOICED",
            "message": (
                f"SO {order['order_number']} belum terfakturkan penuh: "
                + "; ".join(f"{k['description']} {k['quantity_on_invoices']:g} dari {k['quantity_ordered']:g}" for k in kurang)
                + ". Buat fakturnya dulu, atau bila pelanggan membatalkan sisanya, tutup dengan alasan."
            ),
            "lines": kurang,
        }
        blocks.append({**detail, "detail": detail})

    return {"order": order, "blocks": blocks, "lines": lines, "kurang": kurang, "deposits": uang_muka,
            "notes": notes}


@router.post("/{order_id}/close", response_model=SalesOrderResponse)
async def close_sales_order(
    request: Request, order_id: str, body: CloseSalesOrderRequest = None
):
    """Close (complete) a sales order.

    Unit 7 (kasus Rahayu Umar): dulu SO ditutup HANYA berdasar status, dan status itu
    berasal dari PENCACAH quantity_invoiced -- yang bisa berkata 63/63 tanpa satu pun
    faktur. Kini sebelum menutup:
      (a) tiap baris: qty pada baris faktur NON-VOID YANG TERTAUT harus >= qty dipesan
          (BUKAN quantity_invoiced). Kurang -> ditolak, KECUALI dengan alasan (pelanggan
          membatalkan sisanya) -> ditutup + dicatat di audit_logs. Karena itu SO
          partial_invoiced / partial_shipped kini BISA ditutup, asal beralasan.
      (b) uang muka milik SO yang masih bersisa -> TIDAK ditolak lagi (putusan pemilik 28 Sep):
          tetap saldo uang muka pelanggan (nol jurnal), dicatat di riwayat + respons.
    Aturannya hidup di _rencana_tutup_so (dipakai juga /close/preview); di sini: penghalang
    pertama -> 400 dengan detail yang sama persis seperti sebelum pratinjau ada.
    """
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()
        reason = ((body.reason if body else None) or "").strip() or None

        async with pool.acquire() as conn:
            async with conn.transaction():
                rencana = await _rencana_tutup_so(conn, ctx, order_id, reason)
                if rencana["blocks"]:
                    raise HTTPException(status_code=400, detail=rencana["blocks"][0]["detail"])
                order, kurang = rencana["order"], rencana["kurang"]
                dp_tetap = [{"deposit_id": x["deposit_id"], "deposit_number": x["deposit_number"],
                             "remaining": float(x["remaining"])}
                            for x in rencana["deposits"] if x["remaining"] > 0]

                await conn.execute(
                    """
                    UPDATE sales_orders SET status = 'completed', completed_source = 'manual'
                    WHERE id = $1 AND tenant_id = $2
                """,
                    order["id"],
                    ctx["tenant_id"],
                )
                # V315: 'manual' = TERMINAL (aturan selesai-otomatis tak membukanya kembali)
                # sisa per baris yang DIBATALKAN oleh short close (keluar dari belum-dikirim/
                # belum-ditagih karena SO 'completed' tak lagi dihitung: so_agregat.AKTIF_TIDAK)
                for k_ in kurang:
                    k_["quantity_cancelled"] = k_["quantity_ordered"] - k_["quantity_on_invoices"]
                await catat_riwayat(
                    conn, ctx["tenant_id"], "sales_orders", order["id"], order["order_number"],
                    "SALES_ORDER_FORCE_CLOSED" if kurang else "SALES_ORDER_CLOSED", ctx["user_id"],
                    (f"Pesanan {order['order_number']} ditutup; sisa dibatalkan: "
                     + "; ".join(f"{k_['description']} {k_['quantity_cancelled']:g}" for k_ in kurang)
                     + f" — {reason}") if kurang
                    else f"Pesanan {order['order_number']} ditutup" + (f" — {reason}" if reason else ""),
                    # alasan SELALU disimpan (MASTER/WORKSPACE 28 Sep), bukan hanya saat ada sisa
                    {"reason": reason, "lines": kurang, "forced": bool(kurang), "deposits_remaining": dp_tetap},
                    source="api:sales_orders.close",
                )

                return SalesOrderResponse(
                    success=True,
                    message="Sales order closed",
                    data={"order_number": order["order_number"], "status": "completed",
                          "forced": bool(kurang), "reason": reason,
                          "cancelled_lines": kurang, "deposits_remaining": dp_tetap},
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing sales order: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to close sales order")


async def _nilai_batal(conn, ctx, order_id, rows: list, qty_batal: dict) -> dict:
    """Nilai sisa yang DIBATALKAN short close, di dasar yang SAMA dengan total SO: neto sesudah diskon
    baris, dikurangi bagian diskon dokumen SO (pro-rata menurut neto, rumus faktur SO non-terakhir),
    DITAMBAH PPN baris dihitung ulang dari DPP (faktor kode pajak, V289). Kalkulatornya = kalkulator
    to-invoice (plan_so_invoice) -> sama dengan nilai faktur untuk qty itu. ONGKIR TIDAK termasuk
    (ongkir bukan baris). Law 9: Decimal + HALF_UP 2dp."""
    order = await conn.fetchrow(
        "SELECT discount_amount FROM sales_orders WHERE id = $1 AND tenant_id = $2",
        _so_uuid(order_id), ctx["tenant_id"],
    )
    so_rows = [dict(r) for r in rows]
    if not any(_dd(q) > 0 for q in qty_batal.values()):
        return {}
    await attach_dpp_factors(conn, ctx["tenant_id"], so_rows, "tax_id")
    plan = plan_so_invoice(so_rows, qty_batal, order["discount_amount"] or 0, 0, is_last=False)
    return {str(ln["id"]): ln["dpp_harga_jual"] + ln["tax_amount"] for ln in plan["items"]}


@router.post("/{order_id}/close/preview")
async def preview_close_sales_order(
    request: Request, order_id: str, body: CloseSalesOrderRequest = None
):
    """PRATINJAU tutup SO: body SAMA dengan /close, NOL tulisan. Aturan = _rencana_tutup_so (yang
    dipakai /close), dijalankan di transaksi yang SELALU di-ROLLBACK. Berbeda dari /close: SEMUA
    penghalang dilaporkan (tidak berhenti di yang pertama), 200 walau tak bisa ditutup."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()
        reason = ((body.reason if body else None) or "").strip() or None
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                r = await _rencana_tutup_so(conn, ctx, order_id, reason)
                qty_batal = {
                    str(x["id"]): max(_dd(x["quantity"]) - _dd(x["on_invoices"]), _dd(0)) for x in r["lines"]
                }
                nilai = await _nilai_batal(conn, ctx, order_id, r["lines"], qty_batal)
            finally:
                await tr.rollback()  # SELALU: pratinjau tak pernah menulis
        order = r["order"]
        lines = [
            {
                "sales_order_item_id": str(x["id"]),
                "description": x["description"],
                "unit": x["unit"],
                "quantity_ordered": _f(x["quantity"]),
                "quantity_shipped": _f(x["quantity_shipped"] or 0),
                "quantity_on_invoices": _f(x["on_invoices"]),
                "quantity_cancelled": _f(qty_batal[str(x["id"])]),
                "cancelled_amount": _f(nilai.get(str(x["id"]), _dd(0))),
            }
            for x in r["lines"]
        ]
        return {
            "success": True,
            "data": {
                "order_number": order["order_number"],
                "status": order["status"],
                "can_close": not r["blocks"],
                "requires_reason": bool(r["kurang"]),
                "status_after": "completed",
                "blocks": [{k: v for k, v in b.items() if k != "detail"} for b in r["blocks"]],
                "notes": r["notes"],
                "lines": lines,
                "cancelled_total": _f(sum(nilai.values(), _dd(0))),
                "deposits": [
                    {"deposit_id": x["deposit_id"], "deposit_number": x["deposit_number"],
                     "remaining": _f(x["remaining"]),
                     "after_close": "tetap_saldo" if x["remaining"] > 0 else None}
                    for x in r["deposits"]
                ],
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing close of sales order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to preview close")


from ..services.so_pelunasan import SOReceivePaymentPreviewRequest, pratinjau_pelunasan_so  # noqa: E402


@router.post("/{order_id}/receive-payment/preview")
async def preview_receive_payment_from_order(
    request: Request, order_id: str, body: SOReceivePaymentPreviewRequest = None
):
    """PRATINJAU "Terima pelunasan" (halaman CW): rencana tertua-dulu atas faktur terbit SO + penerimaan NYATA
    lewat inti create yang sama (receive_payments.buat_penerimaan), di transaksi yang SELALU di-ROLLBACK.
    200 walau tak bisa disimpan (blocks). Tulis = POST /api/receive-payments dengan `payload` dari sini.
    Lihat services/so_pelunasan.py."""
    try:
        ctx = get_user_context(request)
        so_id = _so_uuid(order_id)  # id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()
        from .receive_payments import _ensure_receive_payments_role_preconditions
        await _ensure_receive_payments_role_preconditions(pool, ctx["tenant_id"])
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ctx["tenant_id"]))
                data = await pratinjau_pelunasan_so(conn, ctx, so_id, body or SOReceivePaymentPreviewRequest())
            finally:
                await tr.rollback()  # SELALU: pratinjau tak pernah menulis
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing receive payment for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to preview receive payment")


from ..services.so_pengiriman import (  # noqa: E402
    SOFulfillRequest,
    bentuk as _bentuk_kirim,
    kunci_faktur_so,
    pratinjau_kirim_so,
    rencana_kirim_so,
    tulis_kirim_so,
    _so_sesudah,
)


@router.post("/{order_id}/fulfill/preview")
async def preview_fulfill_order(request: Request, order_id: str, body: SOFulfillRequest = None):
    """PRATINJAU "Kirim barang" (halaman CW): baris SO -> faktur terbit tertua-dulu + Surat Jalan NYATA lewat inti
    yang sama dengan /sales-invoices/{id}/fulfill, di transaksi yang SELALU di-ROLLBACK. 200 walau diblok.
    Tulis = POST /{order_id}/fulfill dengan `payload` dari sini. Lihat services/so_pengiriman.py."""
    try:
        ctx = get_user_context(request)
        so_id = _so_uuid(order_id)  # id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ctx["tenant_id"]))
                data = await pratinjau_kirim_so(conn, ctx, so_id, body or SOFulfillRequest())
            finally:
                await tr.rollback()  # SELALU: pratinjau tak pernah menulis
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing fulfill for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to preview shipment")


@router.post("/{order_id}/fulfill")
async def fulfill_order(request: Request, response: Response, order_id: str, body: SOFulfillRequest = None):
    """KIRIM BARANG dari SO, ATOMIK: satu Surat Jalan per faktur, SEMUA dalam satu transaksi (satu gagal = nol
    tulisan). Rencana & inti = sama dengan /fulfill/preview. Blok -> 409 SO_FULFILL_BLOCKED berisi SEMUA blok.
    X-Idempotency-Key: kunci sama + isi sama = respons asli (X-Idempotent-Replay); isi beda = 409."""
    try:
        ctx = get_user_context(request)
        so_id = _so_uuid(order_id)
        body = body or SOFulfillRequest()
        try:
            kunci_klien = kunci_idempotensi_klien(request)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(ctx["tenant_id"]))
                kunci_penuh = sidik = None
                if kunci_klien:
                    kunci_penuh = f"SO_FULFILL:{ctx['user_id']}:{so_id}:{kunci_klien}"
                    sidik = hash_payload(body.model_dump(mode="json"))
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                                       f"IDEM:{ctx['tenant_id']}:{kunci_penuh}")
                    try:
                        lama = await ambil_replay_klien(conn, ctx["tenant_id"], kunci_penuh, sidik)
                    except LookupError:
                        raise HTTPException(status_code=409, detail={
                            "code": "IDEMPOTENCY_KEY_REUSED",
                            "message": "Idempotency-Key sudah dipakai untuk pengiriman dengan isi berbeda"})
                    if lama is not None:
                        response.headers["X-Idempotent-Replay"] = "true"
                        return lama
                # urutan kunci = /sales-invoices/{id}/fulfill (faktur dulu; baris SO dikunci trigger sesudahnya)
                await kunci_faktur_so(conn, ctx["tenant_id"], so_id)
                r = await rencana_kirim_so(conn, ctx, so_id, body)
                if r["blocks"]:
                    periode = any(b["code"] == "SO_FULFILL_PERIOD_CLOSED" for b in r["blocks"])  # Law 5: 403
                    raise HTTPException(status_code=403 if periode else 409, detail={
                        "code": "SO_FULFILL_BLOCKED", "message": r["blocks"][0]["message"], "blocks": r["blocks"]})
                hasil = await tulis_kirim_so(conn, ctx, r, kunci_klien and f"{ctx['user_id']}:{kunci_klien}")
                data = _bentuk_kirim(r, hasil, await _so_sesudah(conn, ctx["tenant_id"], so_id))
                respons = {"success": True, "data": data}
                if kunci_penuh:
                    await simpan_replay_klien(conn, ctx["tenant_id"], kunci_penuh, "SO_FULFILL", sidik,
                                              respons, result_id=so_id)
        return respons
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fulfilling order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to ship order")


@router.post("/{order_id}/ship", response_model=SalesOrderResponse)
async def create_shipment(request: Request, order_id: str, body: CreateShipmentRequest):
    """DINONAKTIFKAN — keputusan K2 (rencana Proforma, butir 3.4.5).

    Jalur penyerahan barang di MilkyHoop adalah JALUR TUNGGAL:
    `invoice_fulfillments` (buat/pakai faktur penjualan, lalu catat penyerahan
    lewat POST /api/sales-invoices/{invoice_id}/fulfill). Jalur itulah yang
    mengakui penyerahan, pendapatan, DAN HPP dengan jurnal seimbang.

    Jalur Sales Order (`sales_order_shipments`) TIDAK PERNAH dieksekusi di
    produksi (0 baris) dan TIDAK menjurnal apa pun — stok keluar tanpa HPP,
    pendapatan tak diakui. Dua jalur penyerahan yang hidup berdampingan =
    pembukuan bercabang; sistem ini sudah punya dua presedennya (dua sumber
    navigasi, dua sumber status PKP).

    Endpoint SENGAJA TIDAK DIHAPUS: pemanggil lama harus menerima pesan yang
    menunjuk jalur pengganti, bukan 404 yang membingungkan.

    HTTP 409 Conflict dipilih (bukan 400/410/501): permintaannya sendiri sah
    dan terbentuk benar, yang bertabrakan adalah ATURAN BISNIS — hanya boleh
    ada satu jalur penyerahan. 410 Gone salah karena endpoint masih ada dan
    ada penggantinya; 501 salah karena fitur ini pernah diimplementasikan,
    bukan belum dibuat; 400 salah karena tak ada yang keliru pada payload.

    ⚠️ JANGAN "diperbaiki" balik menjadi jalur tulis. Menghidupkan kembali blok
    INSERT ke sales_order_shipments = membelah pembukuan. Lihat keputusan K2.
    Tabel `sales_order_shipments` sengaja TIDAK di-DROP (0 baris, tak ada data
    hilang, tapi menghapus tabel produksi berisiko tanpa manfaat), dan
    GET /{order_id}/shipments TETAP HIDUP untuk membaca riwayat.
    """
    raise HTTPException(
        status_code=409,
        detail=(
            "Pencatatan pengiriman lewat Sales Order sudah dinonaktifkan. "
            "Gunakan jalur resmi: buat atau pakai Faktur Penjualan untuk pesanan ini, "
            "lalu catat penyerahan barang lewat faktur tersebut "
            "(POST /api/sales-invoices/{invoice_id}/fulfill) sehingga penyerahan, "
            "pendapatan, dan HPP tercatat dalam satu jurnal yang seimbang."
        ),
    )


@router.get("/{order_id}/shipments")
async def get_order_shipments(request: Request, order_id: str):
    """Get all shipments for an order."""
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()

        async with pool.acquire() as conn:
            order = await conn.fetchrow(
                """
                SELECT id FROM sales_orders WHERE id = $1 AND tenant_id = $2
            """,
                _so_uuid(order_id),
                ctx["tenant_id"],
            )

            if not order:
                raise HTTPException(status_code=404, detail="Sales order not found")

            shipments = await conn.fetch(
                """
                SELECT * FROM sales_order_shipments WHERE sales_order_id = $1 ORDER BY shipment_date DESC
            """,
                _so_uuid(order_id),
            )

            result = []
            for shp in shipments:
                items = await conn.fetch(
                    """
                    SELECT si.*, soi.description
                    FROM sales_order_shipment_items si
                    JOIN sales_order_items soi ON si.sales_order_item_id = soi.id
                    WHERE si.shipment_id = $1
                """,
                    shp["id"],
                )

                result.append(
                    {
                        "id": str(shp["id"]),
                        "shipment_number": shp["shipment_number"],
                        "shipment_date": shp["shipment_date"].isoformat(),
                        "carrier": shp["carrier"],
                        "tracking_number": shp["tracking_number"],
                        "status": shp["status"],
                        "items": [
                            {
                                "id": str(i["id"]),
                                "description": i["description"],
                                "quantity_shipped": float(i["quantity_shipped"]),
                            }
                            for i in items
                        ],
                    }
                )

            return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting shipments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get shipments")


# ============================================================================
# CONVERSION ENDPOINTS
# ============================================================================


def _so_uuid(order_id) -> uuid_module.UUID:
    """C6 (26 Sep 2026): id SO di jalur yang bukan UUID -> 404 (dulu ValueError -> 500 "Failed to ...").
    Sama dengan proformas._uuid_or_404. Dipakai SEBELUM kueri apa pun."""
    try:
        return uuid_module.UUID(str(order_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Sales order not found")


def _baris_tagih(inv_item) -> tuple:
    """C6: satu baris body to-invoice -> (so_item_id UUID, quantity float|None). Masukan buruk = 422 yang bisa
    ditampilkan (dulu: so_item_id hilang -> KeyError 500; quantity teks -> TypeError 500; quantity <= 0 lolos
    `qty > remaining` lalu faktur DRAF 0 baris tercipta)."""
    if not isinstance(inv_item, dict) or not inv_item.get("so_item_id"):
        raise HTTPException(status_code=422, detail="Setiap baris wajib memuat so_item_id.")
    try:
        sid = uuid_module.UUID(str(inv_item["so_item_id"]))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail=f"so_item_id tidak sah: {inv_item['so_item_id']}")
    if inv_item.get("quantity") is None:
        return sid, None
    q = inv_item["quantity"]
    try:
        if isinstance(q, bool):
            raise TypeError
        qty = float(q)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail=f"quantity harus angka (baris {sid}).")
    if not qty > 0:  # juga menolak NaN
        raise HTTPException(status_code=422, detail=f"quantity harus lebih dari 0 (baris {sid}).")
    return sid, qty


def _cek_uuid_badan_so(body) -> None:
    """C6: medan id di badan buat SO yang bukan UUID -> 422 bernama medan (dulu ValueError di tengah
    transaksi -> 500 "Failed to create sales order"). Dicek SEBELUM kueri apa pun."""
    def cek(nilai, medan):
        if nilai in (None, ""):
            return
        try:
            uuid_module.UUID(str(nilai))
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"{medan} tidak sah: {nilai}")
    cek(body.customer_id, "customer_id")
    cek(getattr(body, "quote_id", None), "quote_id")
    cek(getattr(body, "shipping_tax_code_id", None), "shipping_tax_code_id")
    for n, it in enumerate(getattr(body, "items", None) or [], start=1):
        for medan in ("item_id", "warehouse_id"):  # tax_id: jalur kode pajak sudah 400 (kontrak t34)
            cek(getattr(it, medan, None) if not isinstance(it, dict) else it.get(medan), f"items[{n}].{medan}")


def _rek_eksplisit(body, nama: str):
    """Nilai rekening yang dikirim EKSPLISIT di body convert, kalau ada.

    `body` boleh None (kedua endpoint convert memberinya default None), dan
    string kosong diperlakukan sama dengan tidak mengirim -- keduanya jatuh ke
    nilai warisan lewat `or` di sisi pemanggil.
    """
    nilai = getattr(body, nama, None) if body is not None else None
    if isinstance(nilai, str):
        nilai = nilai.strip() or None
    return nilai



# WORKSPACE 27 Sep: 404 SO hilang dibedakan dari 404 rute FastAPI ({"detail":"Not Found"}).
SO_TIDAK_ADA = {"code": "SO_TIDAK_ADA", "message": "Pesanan penjualan tidak ditemukan."}


def _so_uuid_faktur(order_id):
    try:
        return uuid_module.UUID(str(order_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail=SO_TIDAK_ADA)


async def _buat_faktur_dari_so(conn, ctx, order_id: str, body) -> dict:
    """Inti to-invoice (validasi + kalkulator + INSERT faktur DRAF + qty tertagih SO), di transaksi PEMANGGIL.
    SATU-SATUNYA definisi: dipakai POST /to-invoice DAN /to-invoice/preview (yang selalu ROLLBACK) — pratinjau
    tak punya rumus sendiri (kelas angka-palsu/tautologi)."""
    order = await conn.fetchrow(
        """
        SELECT * FROM sales_orders WHERE id = $1 AND tenant_id = $2 FOR UPDATE
    """,
        _so_uuid_faktur(order_id),
        ctx["tenant_id"],
    )

    if not order:
        raise HTTPException(status_code=404, detail=SO_TIDAK_ADA)

    if order["status"] in ("draft", "cancelled", "invoiced", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot invoice order with status '{order['status']}'",
        )

    # Get items to invoice
    if body and body.items:
        # Partial invoice with specific quantities
        items_to_invoice = []
        for inv_item in body.items:
            so_item_id, qty_minta = _baris_tagih(inv_item)
            soi = await conn.fetchrow(
                """
                SELECT * FROM sales_order_items WHERE id = $1 AND sales_order_id = $2
            """,
                so_item_id,
                _so_uuid_faktur(order_id),
            )

            if not soi:
                raise HTTPException(
                    status_code=400,
                    detail=f"Item {inv_item['so_item_id']} not found",
                )

            remaining = float(soi["quantity"]) - float(
                soi["quantity_invoiced"]
            )
            qty = remaining if qty_minta is None else qty_minta

            if qty > remaining:
                raise HTTPException(
                    status_code=400,
                    detail=f"Quantity {qty} exceeds uninvoiced {remaining}",
                )

            items_to_invoice.append({**dict(soi), "invoice_qty": qty})
    else:
        # Invoice all uninvoiced quantities
        all_items = await conn.fetch(
            """
            SELECT * FROM sales_order_items WHERE sales_order_id = $1 AND quantity > quantity_invoiced
        """,
            _so_uuid_faktur(order_id),
        )

        items_to_invoice = [
            {
                **dict(i),
                "invoice_qty": float(i["quantity"])
                - float(i["quantity_invoiced"]),
            }
            for i in all_items
        ]

    if not items_to_invoice:
        raise HTTPException(status_code=400, detail="No items to invoice")

    invoice_number = await conn.fetchval(
        "SELECT generate_sales_invoice_number($1::text, 'INV')",
        ctx["tenant_id"],
    )

    invoice_id = uuid_module.uuid4()
    invoice_date = (
        body.invoice_date if body and body.invoice_date else await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
    )
    # F3: tanpa due_date -> termin (NET <n> SO -> termin pelanggan -> 0); dulu SELALU = invoice_date
    # (termin 0 -> terlambat sejak besok). Isian pengguna tetap menang.
    due_date, due_date_source = await tentukan_jatuh_tempo(
        conn, ctx["tenant_id"], invoice_date, body.due_date if body else None,
        order.get("payment_terms"), order.get("customer_id"),
    )

    # Satu kalkulator bersama: diskon & ongkir SO DIBAWA ke faktur (dulu
    # HILANG -- pelanggan ditagih lebih besar sebesar diskonnya, ongkir tak
    # tertagih). Pro-rata menurut neto yang ditagih; faktur yang MENUNTASKAN
    # SO menyerap sisa (SO - yang sudah dibawa faktur lain yang tidak void),
    # jadi SIGMA faktur parsial == total SO. PPN DIHITUNG ULANG dari DPP
    # (dulu disalin pro-rata dengan int()).
    _all_so_items = await conn.fetch(
        """SELECT * FROM sales_order_items WHERE sales_order_id = $1
           ORDER BY sort_order, id""",
        _so_uuid_faktur(order_id),
    )
    _inv_qty = {}
    for item in items_to_invoice:
        _k = str(item["id"])
        _inv_qty[_k] = _dd(_inv_qty.get(_k, 0)) + _dd(item["invoice_qty"])
    for r in _all_so_items:
        _rem = _dd(r["quantity"]) - _dd(r["quantity_invoiced"])
        if _inv_qty.get(str(r["id"]), _dd(0)) > _rem:
            raise HTTPException(
                status_code=400,
                detail=f"Quantity {_inv_qty[str(r['id'])]} exceeds uninvoiced {_rem}",
            )
    _is_last = all(
        _dd(r["quantity_invoiced"]) + _inv_qty.get(str(r["id"]), _dd(0))
        >= _dd(r["quantity"])
        for r in _all_so_items
    )
    _prior = await conn.fetchrow(
        """SELECT COALESCE(SUM(discount_amount), 0) AS disc,
                  COALESCE(SUM(shipping_amount), 0) AS ship
           FROM sales_invoices
           WHERE tenant_id = $1 AND sales_order_id = $2 AND status <> 'void'""",
        ctx["tenant_id"],
        _so_uuid_faktur(order_id),
    )
    _so_rows = [dict(r) for r in _all_so_items]
    await attach_dpp_factors(conn, ctx["tenant_id"], _so_rows, "tax_id")
    # V290: pajak ongkir faktur = pilihan eksplisit SO, atau ikut kode barang SO.
    _so_ship_code = (
        str(order["shipping_tax_code_id"]) if order.get("shipping_tax_code_id") else None
    )
    _ship = await resolve_shipping_tax(
        conn, ctx["tenant_id"], _so_ship_code, _so_rows,
        order["shipping_amount"] or 0, "tax_id",
    )
    try:
        _plan = plan_so_invoice(
            _so_rows,
            _inv_qty,
            order["discount_amount"] or 0,
            order["shipping_amount"] or 0,
            prior_discount=_prior["disc"],
            prior_shipping=_prior["ship"],
            is_last=_is_last,
            shipping_tax=_ship,
        )
    except DocumentDiscountError as _e:
        raise HTTPException(status_code=400, detail=str(_e))
    subtotal = _plan["gross_subtotal"]
    tax_total = _plan["tax_amount"]
    total = _plan["total_amount"]

    header_tax_rate = (
        float(items_to_invoice[0].get("tax_rate") or 0)
        if items_to_invoice
        else 0
    )
    # B2 (2026-06-19): pass recognize_at + warehouse_id from request so the
    # caller can reach the canonical PSAK-72 defer path. Null -> existing
    # post-time fallback (tenant_config policy -> 'invoice'), backward-safe.
    _recognize_at = body.recognize_at if body else None
    _warehouse_id = None
    if body and getattr(body, "warehouse_id", None):
        _warehouse_id = uuid_module.UUID(body.warehouse_id)
    await conn.execute(
        """
        INSERT INTO sales_invoices (
            id, tenant_id, invoice_number, invoice_date, due_date,
            customer_id, customer_name,
            subtotal, tax_rate, tax_amount, total_amount,
            status, sales_order_id, created_by,
            recognize_at, warehouse_id,
            payment_bank_name, payment_account_number, payment_account_holder,
            discount_percent, discount_amount, shipping_amount,
            shipping_tax_code_id, shipping_tax_rate, shipping_tax_amount, shipping_dpp, notes
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12, $13, $14, $15, $16, $17, $18, 0, $19, $20, $21, $22, $23, $24, $25)
    """,
        invoice_id,
        ctx["tenant_id"],
        invoice_number,
        invoice_date,
        due_date,
        str(order["customer_id"]),
        order["customer_name"],
        subtotal,
        header_tax_rate,
        tax_total,
        total,
        _so_uuid_faktur(order_id),
        ctx["user_id"],
        _recognize_at,
        _warehouse_id,
        # Pewarisan rekening tujuan cetak (tiket MASTER); eksplisit menang.
        _rek_eksplisit(body, "payment_bank_name")
        or order["payment_bank_name"],
        _rek_eksplisit(body, "payment_account_number")
        or order["payment_account_number"],
        _rek_eksplisit(body, "payment_account_holder")
        or order["payment_account_holder"],
        _plan["doc_discount"],
        _plan["shipping_amount"],
        uuid_module.UUID(_so_ship_code) if _so_ship_code else None,
        _plan["shipping_tax_rate"],
        _plan["shipping_tax_amount"],
        _plan["shipping_dpp"],
        _rek_eksplisit(body, "notes"),  # WORKSPACE 27 Sep: catatan faktur dari body (dulu dibuang)
    )

    # item["quantity"] di _plan = qty yang DITAGIH faktur ini.
    for line_idx, item in enumerate(_plan["items"], start=1):
        # Resolve tax_code_id: prefer SO field, fallback to rate lookup
        _item_tcid = item.get("tax_id")
        if not _item_tcid and float(item.get("tax_rate") or 0) > 0:
            _item_tcid = await conn.fetchval(
                "SELECT id FROM tax_codes WHERE tenant_id=$1 AND tax_type='ppn' AND rate=$2 AND is_active=true ORDER BY (name ILIKE '%%Keluaran%%') DESC LIMIT 1",
                ctx["tenant_id"],
                item["tax_rate"],
            )

        await conn.execute(
            """
            INSERT INTO sales_invoice_items (
                id, invoice_id, item_id, description,
                quantity, unit, unit_price, discount_percent,
                tax_code_id, tax_rate, tax_amount, subtotal, total, line_number,
                sales_order_item_id, discount_amount, dpp, dpp_harga_jual
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
        """,
            uuid_module.uuid4(),
            invoice_id,
            item["item_id"],
            item["description"],
            item["quantity"],
            item["unit"],
            item["unit_price"],
            item["discount_percent"],
            _item_tcid,
            item["tax_rate"],
            item["tax_amount"],
            item["subtotal"],
            item["total"],
            line_idx,
            item["id"],  # V271: link invoice line -> SO line (void decrement)
            item["discount_amount"],
            item["dpp"],
            item["dpp_harga_jual"],  # 3c
        )

        await conn.execute(
            """
            UPDATE sales_order_items SET quantity_invoiced = quantity_invoiced + $2
            WHERE id = $1
        """,
            item["id"],
            item["quantity"],
        )

    return {
        "invoice_id": invoice_id, "invoice_number": invoice_number, "order_number": order["order_number"],
        "invoice_date": invoice_date, "due_date": due_date, "due_date_source": due_date_source, "total": total,
    }


async def _to_invoice_dalam_tx(conn, ctx, order_id: str, body) -> dict:
    """Buat (+ terbitkan bila body.post) dalam transaksi pemanggil. Terbit = sales_invoices.terbitkan_faktur,
    jalur yang SAMA dengan POST /sales-invoices/{id}/post."""
    hasil = await _buat_faktur_dari_so(conn, ctx, order_id, body)
    hasil.update(posted=False, deposit_applications=[], post_result=None)
    if body is not None and body.post:
        from . import sales_invoices as _si
        pr = await _si.terbitkan_faktur(
            conn, ctx, hasil["invoice_id"], hasil["invoice_number"], hasil["total"], hasil["invoice_date"],
            apply_deposits=body.apply_deposits, skip_deposit_ids=body.skip_deposit_ids,
        )
        hasil.update(posted=True, deposit_applications=pr["deposit_applications"], post_result=pr)
    return hasil


def _f(v):
    return float(v) if v is not None else None


async def _uang_muka_terterap(conn, tenant_id: str, invoice_id) -> list:
    """Uang muka yang BENAR-BENAR tertulis untuk faktur ini (customer_deposit_applications aktif), bukan
    salinan rencana — sumber applied_deposits (to-invoice) dan deposit_plan (pratinjau ber-post)."""
    rows = await conn.fetch(
        """SELECT cda.deposit_id, d.deposit_number, cda.amount_applied
           FROM customer_deposit_applications cda
           JOIN customer_deposits d ON d.id = cda.deposit_id AND d.tenant_id = cda.tenant_id
           WHERE cda.tenant_id = $1 AND cda.invoice_id = $2 AND cda.status = 'active'
           ORDER BY cda.created_at, d.deposit_number""",
        tenant_id, invoice_id,
    )
    return [{"deposit_id": str(r["deposit_id"]), "deposit_number": r["deposit_number"],
             "amount": r["amount_applied"]} for r in rows]


@router.post("/{order_id}/to-invoice", response_model=SalesOrderResponse)
async def convert_to_invoice(
    request: Request, response: Response, order_id: str, body: ConvertToInvoiceRequest = None
):
    """Convert sales order to invoice. {post:true} = buat + terbitkan (+ uang muka SO) dalam SATU transaksi.
    idempotency_key (body; header X-Idempotency-Key cadangan): kunci sama + isi sama = respons asli diulang
    (X-Idempotent-Replay: true); kunci sama + isi beda = 409 IDEMPOTENCY_KEY_REUSED."""
    try:
        ctx = get_user_context(request)
        _so_uuid_faktur(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        try:
            _kunci_klien = (body.idempotency_key.strip() if body is not None and body.idempotency_key
                            and body.idempotency_key.strip() else None) or kunci_idempotensi_klien(request)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        pool = await get_pool()
        if body is not None and body.post:
            from . import sales_invoices as _si
            await _si._ensure_role_preconditions(pool, ctx["tenant_id"])  # sama dengan POST /{id}/post

        async with pool.acquire() as conn:
            async with conn.transaction():
                _kunci_penuh = _sidik = None
                if _kunci_klien:
                    _kunci_penuh = f"SO_TO_INVOICE:{ctx['user_id']}:{order_id}:{_kunci_klien}"
                    _sidik = hash_payload(body.model_dump(mode="json", exclude={"idempotency_key"}))
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                                       f"IDEM:{ctx['tenant_id']}:{_kunci_penuh}")
                    try:
                        _lama = await ambil_replay_klien(conn, ctx["tenant_id"], _kunci_penuh, _sidik)
                    except LookupError as _e:
                        _asli = (getattr(_e, "respons", None) or {}).get("data") or {}
                        raise HTTPException(status_code=409, detail={
                            "code": "IDEMPOTENCY_KEY_REUSED",
                            "message": "Idempotency-Key sudah dipakai untuk faktur dengan isi berbeda",
                            "invoice_id": _asli.get("invoice_id"), "invoice_number": _asli.get("invoice_number"),
                        })
                    if _lama is not None:
                        response.headers["X-Idempotent-Replay"] = "true"
                        return SalesOrderResponse(**_lama)

                h = await _to_invoice_dalam_tx(conn, ctx, order_id, body)
                pr = h["post_result"] or {}
                # dibaca dari yang BENAR-BENAR tertulis (WORKSPACE 27 Sep), bukan dari rencana
                st = await conn.fetchval("SELECT status FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                                         h["invoice_id"], ctx["tenant_id"])
                dp = await _uang_muka_terterap(conn, ctx["tenant_id"], h["invoice_id"])
                hasil = SalesOrderResponse(
                    success=True,
                    message="Invoice created and posted from sales order" if h["posted"]
                    else "Invoice created from sales order",
                    data={
                        "invoice_id": str(h["invoice_id"]),
                        "invoice_number": h["invoice_number"],
                        "order_number": h["order_number"],
                        "status": st,
                        "due_date": h["due_date"].isoformat(),
                        "due_date_source": h["due_date_source"],
                        "posted": h["posted"],
                        "journal_number": pr.get("journal_number"),
                        "fulfillment_status": pr.get("fulfillment_status"),
                        "revenue_status": pr.get("revenue_status"),
                        "applied_deposits": [{**a, "amount": _f(a["amount"])} for a in dp],
                        "total_applied": _f(sum((a["amount"] for a in dp), Decimal("0"))),
                    },
                )
                if _kunci_penuh:
                    await simpan_replay_klien(conn, ctx["tenant_id"], _kunci_penuh, "SO_TO_INVOICE", _sidik,
                                              hasil.model_dump(mode="json"), result_id=h["invoice_id"])
                return hasil

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting to invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to convert to invoice")


@router.post("/{order_id}/to-invoice/preview")
async def preview_to_invoice(request: Request, order_id: str, body: ConvertToInvoiceRequest = None):
    """PRATINJAU to-invoice: body SAMA, NOL tulisan. Menjalankan jalur to-invoice yang SAMA (buat [+ terbit +
    uang muka bila post]) di transaksi yang SELALU di-ROLLBACK, lalu membaca hasilnya sebelum rollback ->
    angka = angka yang akan dibuat, dan validasi/galat 400/422 identik. Tanpa `post`: rencana uang muka dari
    perencana yang sama (plan_so_deposit_application) atas total faktur."""
    try:
        ctx = get_user_context(request)
        _so_uuid_faktur(order_id)
        pool = await get_pool()
        if body is not None and body.post:
            from . import sales_invoices as _si
            await _si._ensure_role_preconditions(pool, ctx["tenant_id"])
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            try:
                h = await _to_invoice_dalam_tx(conn, ctx, order_id, body)
                data = await _baca_pratinjau(conn, ctx, h, body)
            finally:
                await tr.rollback()  # SELALU: pratinjau tak pernah menulis
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing invoice from order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to preview invoice")


async def _baca_pratinjau(conn, ctx, h: dict, body) -> dict:
    inv = await conn.fetchrow(
        """SELECT subtotal, discount_amount, shipping_amount, tax_amount, total_amount, due_date
           FROM sales_invoices WHERE id = $1 AND tenant_id = $2""",
        h["invoice_id"], ctx["tenant_id"],
    )
    items = await conn.fetch(
        """SELECT sales_order_item_id, description, quantity, unit_price, subtotal, total
           FROM sales_invoice_items WHERE invoice_id = $1 ORDER BY line_number""",
        h["invoice_id"],
    )
    if h["posted"]:
        rencana = await _uang_muka_terterap(conn, ctx["tenant_id"], h["invoice_id"])  # yang tertulis (lalu rollback)
    elif body is None or body.apply_deposits:
        from .customer_deposits import plan_so_deposit_application
        rencana = [{"deposit_id": p["deposit_id"], "deposit_number": p["deposit_number"], "amount": p["planned_amount"]}
                   for p in await plan_so_deposit_application(
                       conn, ctx["tenant_id"], h["invoice_id"], inv["total_amount"],
                       body.skip_deposit_ids if body else ())
                   if p["planned_amount"] > 0]
    else:
        rencana = []
    dp = sum((Decimal(str(r["amount"])) for r in rencana), Decimal("0"))
    return {
        "lines": [{"so_item_id": str(i["sales_order_item_id"]) if i["sales_order_item_id"] else None,
                   "name": i["description"], "qty": _f(i["quantity"]), "unit_price": _f(i["unit_price"]),
                   "line_total": _f(i["subtotal"])} for i in items],
        "subtotal": _f(inv["subtotal"]),
        "discount": _f(inv["discount_amount"] or 0),
        "shipping": _f(inv["shipping_amount"] or 0),
        "tax": _f(inv["tax_amount"]),
        "total": _f(inv["total_amount"]),
        "due_date": inv["due_date"].isoformat(),
        "due_date_source": h["due_date_source"],
        "posted": h["posted"],
        "deposit_plan": [{**r, "amount": _f(r["amount"])} for r in rencana],
        "remaining_after_dp": _f(Decimal(str(inv["total_amount"])) - dp),
    }


@router.get("/{order_id}/history")
async def get_sales_order_history(request: Request, order_id: str, limit: int = Query(200, ge=1, le=500)):
    """Riwayat SO + dokumen turunannya (uang muka, proforma, faktur, Surat Jalan, pembayaran),
    terbaru dulu. Dokumen terkait disaring per izin BACA pemanggil; yang tersaring -> `omitted`
    (daftar modul). Lihat services/so_riwayat.py."""
    try:
        so_id = _so_uuid(order_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        ctx = get_user_context(request)
        _so_uuid(order_id)  # C6: id jalur tak sah -> 404 SEBELUM DB
        pool = await get_pool()
        async with pool.acquire() as conn:
            data = await riwayat_so(conn, ctx["tenant_id"], so_id, lambda m: boleh_baca(request, m), limit)
        if data is None:
            raise HTTPException(status_code=404, detail="Sales order not found")
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sales order history {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal memuat riwayat pesanan")
