from fastapi import APIRouter, Query, Request
from ..services.db_pool import get_db_pool

router = APIRouter()


@router.get("")
async def search(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(20, le=50),
):
    tenant_id = request.state.user.get("tenant_id")
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_id)

        results = []
        per_cat = min(limit // 4, 5)
        pattern = f"%{q}%"

        # Customers
        rows = await conn.fetch(
            """
            SELECT id, nama, display_name, telepon FROM customers
            WHERE tenant_id = $1 AND deleted_at IS NULL
              AND (nama ILIKE $2 OR display_name ILIKE $2 OR email ILIKE $2 OR telepon ILIKE $2)
            LIMIT $3
        """,
            tenant_id,
            pattern,
            per_cat,
        )
        for r in rows:
            results.append(
                {
                    "type": "customer",
                    "id": str(r["id"]),
                    "title": r["display_name"] or r["nama"],
                    "subtitle": r["telepon"] or "",
                    "panel": "customer",
                }
            )

        # Vendors
        rows = await conn.fetch(
            """
            SELECT id, name, phone FROM vendors
            WHERE tenant_id = $1 AND deleted_at IS NULL
              AND (name ILIKE $2 OR email ILIKE $2 OR phone ILIKE $2)
            LIMIT $3
        """,
            tenant_id,
            pattern,
            per_cat,
        )
        for r in rows:
            results.append(
                {
                    "type": "vendor",
                    "id": str(r["id"]),
                    "title": r["name"],
                    "subtitle": r["phone"] or "",
                    "panel": "vendor",
                }
            )

        # Items (products)
        rows = await conn.fetch(
            """
            SELECT id, nama_produk, sku, item_type FROM products
            WHERE tenant_id = $1 AND deleted_at IS NULL
              AND (nama_produk ILIKE $2 OR sku ILIKE $2)
            LIMIT $3
        """,
            tenant_id,
            pattern,
            per_cat,
        )
        for r in rows:
            subtitle_parts = [r["sku"] or "", r["item_type"] or ""]
            results.append(
                {
                    "type": "item",
                    "id": str(r["id"]),
                    "title": r["nama_produk"],
                    "subtitle": " · ".join(p for p in subtitle_parts if p),
                    "panel": "items",
                }
            )

        # Sales Invoices
        rows = await conn.fetch(
            """
            SELECT id, invoice_number, customer_name, status, total_amount
            FROM sales_invoices
            WHERE tenant_id = $1
              AND (invoice_number ILIKE $2 OR customer_name ILIKE $2)
            ORDER BY created_at DESC
            LIMIT $3
        """,
            tenant_id,
            pattern,
            per_cat,
        )
        for r in rows:
            amt = f"Rp {int(r['total_amount'] or 0):,}".replace(",", ".")
            results.append(
                {
                    "type": "invoice",
                    "id": str(r["id"]),
                    "title": r["invoice_number"],
                    "subtitle": f"{r['customer_name'] or ''} · {amt}",
                    "panel": "faktur-penjualan",
                }
            )

        # Bills
        rows = await conn.fetch(
            """
            SELECT id, invoice_number, vendor_name, status_v2, amount
            FROM bills
            WHERE tenant_id = $1
              AND (invoice_number ILIKE $2 OR vendor_name ILIKE $2)
            ORDER BY created_at DESC
            LIMIT $3
        """,
            tenant_id,
            pattern,
            per_cat,
        )
        for r in rows:
            amt = f"Rp {int(r['amount'] or 0):,}".replace(",", ".")
            results.append(
                {
                    "type": "bill",
                    "id": str(r["id"]),
                    "title": r["invoice_number"],
                    "subtitle": f"{r['vendor_name'] or ''} · {amt}",
                    "panel": "pembelian",
                }
            )

        return {"results": results[:limit], "query": q, "total": len(results)}
