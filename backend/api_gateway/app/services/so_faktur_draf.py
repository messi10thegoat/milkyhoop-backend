"""Q-016 (a) (25 Sep 2026): penanda faktur DRAF per SO — status SO TETAP (penahan draf benar).

Status SO 'invoiced' diturunkan trigger dari sales_order_items.quantity_invoiced = Σ qty baris faktur
NON-VOID yang bertaut per baris (sales_invoice_items.sales_order_item_id) — draf TERMASUK, sengaja: draf
menahan faktur ganda. Yang menyesatkan hanya labelnya ("Ditagih" padahal fakturnya draf). Server kini
memberi penanda dari SUMBER YANG SAMA (tautan per baris, non-void), dipecah draf vs terbit:
- draft_invoice_count  = jumlah faktur DRAF yang bertaut ke baris SO ini
- has_draft_invoice    = draft_invoice_count > 0
- posted_invoiced_qty  = Σ qty baris faktur TERBIT (bukan draf/void) — draf + terbit = quantity_invoiced
Satu kueri untuk semua SO di halaman; berpagar tenant.
"""

SQL_PENANDA = """
    SELECT soi.sales_order_id AS so_id,
           COUNT(DISTINCT si.id) FILTER (WHERE si.status = 'draft') AS draft_invoice_count,
           COALESCE(SUM(sii.quantity) FILTER (WHERE si.status NOT IN ('draft', 'void')), 0) AS posted_invoiced_qty
    FROM sales_invoice_items sii
    JOIN sales_invoices si ON si.id = sii.invoice_id
    JOIN sales_order_items soi ON soi.id = sii.sales_order_item_id
    WHERE si.tenant_id = $1
      AND si.status <> 'void'
      AND soi.sales_order_id = ANY($2::uuid[])
    GROUP BY soi.sales_order_id
"""

KOSONG = {"has_draft_invoice": False, "draft_invoice_count": 0, "posted_invoiced_qty": 0.0}


async def penanda_faktur_so(conn, tenant_id: str, so_ids) -> dict:
    """{str(so_id): {has_draft_invoice, draft_invoice_count, posted_invoiced_qty}} — setiap id selalu ada."""
    ids = [str(i) for i in so_ids]
    hasil = {i: dict(KOSONG) for i in ids}
    if not ids:
        return hasil
    for r in await conn.fetch(SQL_PENANDA, tenant_id, ids):
        k = str(r["so_id"])
        if k in hasil:
            n = int(r["draft_invoice_count"] or 0)
            hasil[k] = {"has_draft_invoice": n > 0, "draft_invoice_count": n,
                        "posted_invoiced_qty": float(r["posted_invoiced_qty"] or 0)}
    return hasil
