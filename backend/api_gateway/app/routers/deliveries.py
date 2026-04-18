"""
Deliveries Router - Pengiriman Barang

Read-only endpoints for delivery management (invoice_fulfillments wrapper).
Create still uses POST /api/sales-invoices/{id}/fulfill.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional
import logging
from io import BytesIO
from fastapi.responses import StreamingResponse
from ..services.pdf_service import get_pdf_service

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool():
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = request.state.user
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")
    return {"tenant_id": tenant_id, "user_id": user.get("user_id")}


@router.get("/summary")
async def get_deliveries_summary(request: Request):
    """Summary counts for Pengiriman Barang dashboard."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'posted') AS total_posted,
                COUNT(*) FILTER (WHERE status = 'voided') AS total_voided,
                COUNT(*) FILTER (WHERE fulfillment_date = CURRENT_DATE) AS today_count,
                COUNT(*) AS total_all
            FROM invoice_fulfillments
            WHERE tenant_id = $1
            """,
            ctx["tenant_id"],
        )
    return {
        "total_posted": row["total_posted"],
        "total_voided": row["total_voided"],
        "today_count": row["today_count"],
        "total_all": row["total_all"],
    }


@router.get("")
async def list_deliveries(
    request: Request,
    status: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("delivery_date"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """List deliveries with pagination, filters, and sorting."""
    ctx = get_user_context(request)
    pool = await get_pool()

    sort_map = {
        "delivery_date": "f.fulfillment_date",
        "delivery_number": "f.fulfillment_number",
        "customer_name": "c.nama",
        "status": "f.status",
        "created_at": "f.created_at",
    }
    db_sort = sort_map.get(sort_by, "f.fulfillment_date")
    direction = "ASC" if sort_order.lower() == "asc" else "DESC"

    conditions = ["f.tenant_id = $1"]
    params = [ctx["tenant_id"]]
    idx = 2

    if status:
        conditions.append(f"f.status = ${idx}")
        params.append(status.upper())
        idx += 1

    if customer_id:
        conditions.append(f"si.customer_id = ${idx}")
        params.append(customer_id)
        idx += 1

    if search:
        conditions.append(
            f"(f.fulfillment_number ILIKE ${idx} OR c.nama ILIKE ${idx} OR si.invoice_number ILIKE ${idx})"
        )
        params.append(f"%{search}%")
        idx += 1

    where_clause = " AND ".join(conditions)
    offset = (page - 1) * per_page

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        total_row = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS total
            FROM invoice_fulfillments f
            JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN customers c ON c.id = si.customer_id
            LEFT JOIN warehouses w ON w.id = f.warehouse_id
            WHERE {where_clause}
            """,
            *params,
        )
        total = total_row["total"]

        rows = await conn.fetch(
            f"""
            SELECT
                f.id,
                f.fulfillment_number AS delivery_number,
                f.fulfillment_date AS delivery_date,
                f.status,
                f.notes,
                f.created_at,
                f.posted_at,
                f.voided_at,
                f.voided_reason,
                si.id AS invoice_id,
                si.invoice_number,
                si.customer_id,
                c.nama AS customer_name,
                c.telepon AS customer_phone,
                c.alamat AS customer_address,
                w.name AS warehouse_name,
                (SELECT COUNT(*) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS item_count,
                (SELECT COALESCE(SUM(fi.total_cost), 0) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS total_cogs
            FROM invoice_fulfillments f
            JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN customers c ON c.id = si.customer_id
            LEFT JOIN warehouses w ON w.id = f.warehouse_id
            WHERE {where_clause}
            ORDER BY {db_sort} {direction}
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            per_page,
            offset,
        )

    items = [
        {
            "id": str(r["id"]),
            "delivery_number": r["delivery_number"],
            "delivery_date": r["delivery_date"].isoformat()
            if r["delivery_date"]
            else None,
            "status": r["status"],
            "notes": r["notes"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "posted_at": r["posted_at"].isoformat() if r["posted_at"] else None,
            "voided_at": r["voided_at"].isoformat() if r["voided_at"] else None,
            "voided_reason": r["voided_reason"],
            "invoice_id": str(r["invoice_id"]),
            "invoice_number": r["invoice_number"],
            "customer_id": str(r["customer_id"]) if r["customer_id"] else None,
            "customer_name": r["customer_name"],
            "customer_phone": r["customer_phone"],
            "customer_address": r["customer_address"],
            "warehouse_name": r["warehouse_name"],
            "item_count": r["item_count"],
            "total_cogs": float(r["total_cogs"]) if r["total_cogs"] else 0,
        }
        for r in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 0,
    }


@router.get("/{delivery_id}")
async def get_delivery_detail(delivery_id: str, request: Request):
    """Full delivery detail with items and journals."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        row = await conn.fetchrow(
            """
            SELECT
                f.id,
                f.fulfillment_number AS delivery_number,
                f.fulfillment_date AS delivery_date,
                f.status,
                f.notes,
                f.created_at,
                f.updated_at,
                f.posted_at,
                f.voided_at,
                f.voided_reason,
                f.journal_id,
                f.revenue_journal_id,
                si.id AS invoice_id,
                si.invoice_number,
                si.customer_id,
                c.nama AS customer_name,
                c.telepon AS customer_phone,
                c.alamat AS customer_address,
                w.name AS warehouse_name,
                (SELECT COUNT(*) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS item_count,
                (SELECT COALESCE(SUM(fi.total_cost), 0) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS total_cogs
            FROM invoice_fulfillments f
            JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN customers c ON c.id = si.customer_id
            LEFT JOIN warehouses w ON w.id = f.warehouse_id
            WHERE f.id = $1 AND f.tenant_id = $2
            """,
            delivery_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Delivery not found")

        items_rows = await conn.fetch(
            """
            SELECT
                fi.id,
                fi.invoice_item_id,
                fi.product_id,
                fi.quantity,
                fi.unit_cost,
                fi.total_cost,
                fi.notes,
                fi.created_at,
                p.nama_produk AS product_name,
                p.sku AS product_sku
            FROM invoice_fulfillment_items fi
            LEFT JOIN products p ON p.id = fi.product_id
            WHERE fi.fulfillment_id = $1
            ORDER BY fi.created_at
            """,
            delivery_id,
        )

        journals_rows = await conn.fetch(
            """
            SELECT
                je.id AS journal_id,
                je.journal_number,
                je.journal_date,
                je.description,
                je.source_type,
                je.total_debit,
                je.total_credit,
                je.status,
                json_agg(
                    json_build_object(
                        'account_code', coa.account_code,
                        'account_name', coa.name,
                        'debit', jl.debit,
                        'credit', jl.credit,
                        'description', jl.memo
                    ) ORDER BY jl.line_number
                ) AS lines
            FROM journal_entries je
            LEFT JOIN journal_lines jl ON jl.journal_id = je.id
            LEFT JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.source_type IN ('INVOICE_FULFILLMENT', 'INVOICE_REVENUE')
              AND je.source_id = $1::uuid
              AND je.tenant_id = $2
            GROUP BY je.id, je.journal_number, je.journal_date, je.description,
                     je.source_type, je.total_debit, je.total_credit, je.status
            ORDER BY je.journal_date
            """,
            delivery_id,
            ctx["tenant_id"],
        )

    detail = {
        "id": str(row["id"]),
        "delivery_number": row["delivery_number"],
        "delivery_date": row["delivery_date"].isoformat()
        if row["delivery_date"]
        else None,
        "status": row["status"],
        "notes": row["notes"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
        "voided_at": row["voided_at"].isoformat() if row["voided_at"] else None,
        "voided_reason": row["voided_reason"],
        "journal_id": str(row["journal_id"]) if row["journal_id"] else None,
        "revenue_journal_id": str(row["revenue_journal_id"])
        if row["revenue_journal_id"]
        else None,
        "invoice_id": str(row["invoice_id"]),
        "invoice_number": row["invoice_number"],
        "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
        "customer_name": row["customer_name"],
        "customer_phone": row["customer_phone"],
        "customer_address": row["customer_address"],
        "warehouse_name": row["warehouse_name"],
        "item_count": row["item_count"],
        "total_cogs": float(row["total_cogs"]) if row["total_cogs"] else 0,
        "items": [
            {
                "id": str(i["id"]),
                "invoice_item_id": str(i["invoice_item_id"]),
                "product_id": str(i["product_id"]) if i["product_id"] else None,
                "product_name": i["product_name"],
                "product_sku": i["product_sku"],
                "quantity": str(i["quantity"]),
                "unit_cost": str(i["unit_cost"])
                if i["unit_cost"] is not None
                else None,
                "total_cost": str(i["total_cost"])
                if i["total_cost"] is not None
                else None,
                "notes": i["notes"],
            }
            for i in items_rows
        ],
        "journals": [
            {
                "journal_id": str(j["journal_id"]),
                "entry_number": j["journal_number"],
                "entry_date": j["journal_date"].isoformat()
                if j["journal_date"]
                else None,
                "description": j["description"],
                "source_type": j["source_type"],
                "total_debit": str(j["total_debit"])
                if j["total_debit"] is not None
                else None,
                "total_credit": str(j["total_credit"])
                if j["total_credit"] is not None
                else None,
                "status": j["status"],
                "lines": j["lines"],
            }
            for j in journals_rows
        ],
    }

    return detail


# =============================================================================
# GENERATE PDF — SURAT JALAN
# =============================================================================


@router.get("/{delivery_id}/pdf")
async def get_delivery_pdf(
    delivery_id: str,
    request: Request,
):
    """
    Generate Surat Jalan PDF for a delivery.
    Returns PDF bytes inline (for browser preview / download).
    """
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")

        row = await conn.fetchrow(
            """
            SELECT
                f.id,
                f.fulfillment_number AS delivery_number,
                f.fulfillment_date AS delivery_date,
                f.status,
                f.notes,
                si.id AS invoice_id,
                si.invoice_number,
                si.customer_id,
                c.nama AS customer_name,
                c.telepon AS customer_phone,
                c.alamat AS customer_address,
                w.name AS warehouse_name,
                (SELECT COUNT(*) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS item_count,
                (SELECT COALESCE(SUM(fi.total_cost), 0) FROM invoice_fulfillment_items fi WHERE fi.fulfillment_id = f.id) AS total_cogs
            FROM invoice_fulfillments f
            JOIN sales_invoices si ON si.id = f.invoice_id
            LEFT JOIN customers c ON c.id = si.customer_id
            LEFT JOIN warehouses w ON w.id = f.warehouse_id
            WHERE f.id = $1 AND f.tenant_id = $2
            """,
            delivery_id,
            ctx["tenant_id"],
        )

        if not row:
            raise HTTPException(status_code=404, detail="Delivery not found")

        items_rows = await conn.fetch(
            """
            SELECT
                fi.id,
                fi.quantity,
                fi.notes,
                p.nama_produk AS product_name,
                p.sku AS product_sku,
                sii.unit
            FROM invoice_fulfillment_items fi
            LEFT JOIN products p ON p.id = fi.product_id
            LEFT JOIN sales_invoice_items sii ON sii.id = fi.invoice_item_id
            WHERE fi.fulfillment_id = $1
            ORDER BY fi.created_at
            """,
            delivery_id,
        )

        # Fetch tenant info
        tenant_row = await conn.fetchrow(
            'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
            ctx["tenant_id"],
        )
        tenant_info = {
            "name": tenant_row["display_name"] if tenant_row else str(ctx["tenant_id"]),
            "address": tenant_row["address"] if tenant_row else None,
            "phone": tenant_row["phone"] if tenant_row else None,
        }

    delivery_data = {
        "id": str(row["id"]),
        "delivery_number": row["delivery_number"],
        "delivery_date": row["delivery_date"].isoformat()
        if row["delivery_date"]
        else None,
        "status": row["status"],
        "notes": row["notes"],
        "invoice_id": str(row["invoice_id"]),
        "invoice_number": row["invoice_number"],
        "customer_id": str(row["customer_id"]) if row["customer_id"] else None,
        "customer_name": row["customer_name"],
        "customer_phone": row["customer_phone"],
        "customer_address": row["customer_address"],
        "tenant": tenant_info,
        "items": [
            {
                "id": str(i["id"]),
                "product_name": i["product_name"],
                "product_sku": i["product_sku"],
                "quantity": str(i["quantity"]),
                "unit": i["unit"],
                "notes": i["notes"],
            }
            for i in items_rows
        ],
    }

    pdf_service = get_pdf_service()
    pdf_bytes = pdf_service.generate_delivery_note_pdf(delivery_data)

    delivery_num = row["delivery_number"] or delivery_id[:8]
    filename = f"SuratJalan-{delivery_num}.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )
