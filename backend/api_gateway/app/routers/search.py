"""
Powerful Search — one engine (pg_trgm + unaccent, GIN on search_text) for the
global ⌘K palette (scope=all) and the per-module pills (scope=<type>).

Contract: backend/docs/search-contract.md. All amounts as float; dates ISO.
Tenant from JWT; per-group READ authz via policy_engine.can(); groups omitted
if not permitted. q min 2 chars; statement_timeout 500ms; one request.
"""
import logging
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api_gateway.app.dependencies.auth import get_current_user
from backend.api_gateway.app.services.policy_engine_client import get_policy_engine

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_pool() -> asyncpg.Pool:
    from backend.api_gateway.app.services.db_pool import get_db_pool

    return await get_db_pool()


PAT = "'%' || lower(unaccent($2)) || '%'"
SIM = "similarity(coalesce(search_text,''), lower(unaccent($2)))"

# 26 Sep 2026 (audit izin): modul otorisasi per grup WAJIB = modul rute daftarnya
# di PermissionMiddleware (dulu quotes/proformas/deliveries/customer_deposits/
# credit_notes memakai modul induk -> pencarian menampilkan dokumen yang daftarnya
# 403, mis. VIEWER melihat nota kredit). Dijaga tests/unit/test_search_modul_selaras.py.
# type -> (authz module, url prefix, SQL). All queries take $1 tenant, $2 q,
# $3 customer ids (text[]), $4 limit — uniform param list.
GROUPS = {
    "customers": ("customer", "/kontak/pelanggan/", f"""
        SELECT id::text AS id,
          COALESCE(NULLIF(name,''), nama, display_name, company_name, '(tanpa nama)') AS title,
          NULLIF(concat_ws(', ',
            NULLIF(COALESCE(NULLIF(address,''), alamat), ''),
            NULLIF(COALESCE(NULLIF(phone,''), telepon, mobile_phone), '')), '') AS subtitle,
          code AS number, NULL::numeric AS amount, NULL::text AS status, NULL::date AS due_date,
          CASE WHEN lower(unaccent(coalesce(name,'')||' '||coalesce(nama,''))) LIKE {PAT}
               THEN 'name' ELSE 'attribute' END AS matched_field
        FROM customers
        WHERE tenant_id=$1 AND coalesce(deleted_at::text,'')='' AND search_text LIKE {PAT} AND cardinality($3::text[]) >= 0
        ORDER BY {SIM} DESC, updated_at DESC NULLS LAST
        LIMIT $4"""),
    "vendors": ("supplier", "/kontak/vendor/", f"""
        SELECT id::text AS id,
          COALESCE(NULLIF(name,''), display_name, company_name, '(tanpa nama)') AS title,
          NULLIF(concat_ws(', ', NULLIF(address,''), NULLIF(COALESCE(NULLIF(phone,''), mobile_phone),'')), '') AS subtitle,
          code AS number, NULL::numeric AS amount, NULL::text AS status, NULL::date AS due_date,
          CASE WHEN lower(unaccent(coalesce(name,''))) LIKE {PAT} THEN 'name' ELSE 'attribute' END AS matched_field
        FROM vendors
        WHERE tenant_id=$1 AND search_text LIKE {PAT} AND cardinality($3::text[]) >= 0
        ORDER BY {SIM} DESC, updated_at DESC NULLS LAST
        LIMIT $4"""),
    "sales_invoices": ("sales_invoice", "/penjualan/faktur/", f"""
        SELECT id::text AS id, COALESCE(customer_name,'(tanpa nama)') AS title,
          invoice_number AS subtitle, invoice_number AS number, total_amount AS amount,
          status, due_date,
          CASE WHEN search_text LIKE {PAT}
               THEN CASE WHEN lower(coalesce(invoice_number,'')) LIKE {PAT} THEN 'number' ELSE 'text' END
               ELSE 'customer' END AS matched_field
        FROM sales_invoices
        WHERE tenant_id=$1 AND (search_text LIKE {PAT} OR customer_id::text = ANY($3::text[]))
        ORDER BY {SIM} DESC, invoice_date DESC NULLS LAST
        LIMIT $4"""),
    "sales_orders": ("sales_order", "/penjualan/pesanan/", f"""
        SELECT id::text AS id, COALESCE(customer_name,'(tanpa nama)') AS title,
          NULLIF(reference,'') AS subtitle, order_number AS number, total_amount AS amount,
          status, expected_ship_date AS due_date,
          CASE WHEN search_text LIKE {PAT}
               THEN CASE WHEN lower(coalesce(order_number,'')) LIKE {PAT} THEN 'number' ELSE 'text' END
               ELSE 'customer' END AS matched_field
        FROM sales_orders
        WHERE tenant_id=$1 AND (search_text LIKE {PAT} OR customer_id::text = ANY($3::text[]))
        ORDER BY {SIM} DESC, order_date DESC NULLS LAST
        LIMIT $4"""),
    "quotes": ("quote", "/penjualan/penawaran/", f"""
        SELECT id::text AS id, COALESCE(customer_name,'(tanpa nama)') AS title,
          NULLIF(subject,'') AS subtitle, quote_number AS number, total_amount AS amount,
          status, expiry_date AS due_date,
          CASE WHEN search_text LIKE {PAT}
               THEN CASE WHEN lower(coalesce(quote_number,'')) LIKE {PAT} THEN 'number' ELSE 'text' END
               ELSE 'customer' END AS matched_field
        FROM quotes
        WHERE tenant_id=$1 AND (search_text LIKE {PAT} OR customer_id::text = ANY($3::text[]))
        ORDER BY {SIM} DESC, quote_date DESC NULLS LAST
        LIMIT $4"""),
    "proformas": ("proforma", "/penjualan/proforma/", f"""
        SELECT id::text AS id, COALESCE(customer_name,'(tanpa nama)') AS title,
          NULLIF(purpose,'') AS subtitle, proforma_number AS number, amount AS amount,
          status, due_date,
          CASE WHEN search_text LIKE {PAT}
               THEN CASE WHEN lower(coalesce(proforma_number,'')) LIKE {PAT} THEN 'number' ELSE 'text' END
               ELSE 'customer' END AS matched_field
        FROM proformas
        WHERE tenant_id=$1 AND (search_text LIKE {PAT} OR customer_id::text = ANY($3::text[]))
        ORDER BY {SIM} DESC, proforma_date DESC NULLS LAST
        LIMIT $4"""),
    "deliveries": ("sales_invoice", "/penjualan/pengiriman/", f"""
        SELECT id::text AS id, shipment_number AS title,
          NULLIF(concat_ws(' · ', NULLIF(carrier,''), NULLIF(tracking_number,'')),'') AS subtitle,
          shipment_number AS number, NULL::numeric AS amount, status, shipment_date AS due_date,
          CASE WHEN search_text LIKE {PAT} THEN 'text' ELSE 'customer' END AS matched_field
        FROM sales_order_shipments
        WHERE tenant_id=$1 AND (search_text LIKE {PAT}
          OR sales_order_id IN (SELECT id FROM sales_orders WHERE tenant_id=$1 AND customer_id::text = ANY($3::text[])))
        ORDER BY {SIM} DESC, shipment_date DESC NULLS LAST
        LIMIT $4"""),
    "receive_payments": ("receive_payment", "/penjualan/pembayaran/", f"""
        SELECT id::text AS id, COALESCE(customer_name,'(tanpa nama)') AS title,
          payment_number AS subtitle, payment_number AS number, total_amount AS amount,
          status, payment_date AS due_date,
          CASE WHEN search_text LIKE {PAT}
               THEN CASE WHEN lower(coalesce(payment_number,'')) LIKE {PAT} THEN 'number' ELSE 'text' END
               ELSE 'customer' END AS matched_field
        FROM receive_payments
        WHERE tenant_id=$1 AND (search_text LIKE {PAT} OR customer_id::text = ANY($3::text[]))
        ORDER BY {SIM} DESC, payment_date DESC NULLS LAST
        LIMIT $4"""),
    "customer_deposits": ("customer_deposit", "/penjualan/uang-muka/", f"""
        SELECT id::text AS id, COALESCE(customer_name,'(tanpa nama)') AS title,
          deposit_number AS subtitle, deposit_number AS number, amount AS amount,
          status, deposit_date AS due_date,
          CASE WHEN search_text LIKE {PAT}
               THEN CASE WHEN lower(coalesce(deposit_number,'')) LIKE {PAT} THEN 'number' ELSE 'text' END
               ELSE 'customer' END AS matched_field
        FROM customer_deposits
        WHERE tenant_id=$1 AND (search_text LIKE {PAT} OR customer_id::text = ANY($3::text[]))
        ORDER BY {SIM} DESC, deposit_date DESC NULLS LAST
        LIMIT $4"""),
    "credit_notes": ("credit_note", "/penjualan/nota-kredit/", f"""
        SELECT id::text AS id, COALESCE(customer_name,'(tanpa nama)') AS title,
          credit_note_number AS subtitle, credit_note_number AS number, total_amount AS amount,
          status, credit_note_date AS due_date,
          CASE WHEN search_text LIKE {PAT}
               THEN CASE WHEN lower(coalesce(credit_note_number,'')) LIKE {PAT} THEN 'number' ELSE 'text' END
               ELSE 'customer' END AS matched_field
        FROM credit_notes
        WHERE tenant_id=$1 AND (search_text LIKE {PAT} OR customer_id::text = ANY($3::text[]))
        ORDER BY {SIM} DESC, credit_note_date DESC NULLS LAST
        LIMIT $4"""),
}

GROUP_ORDER = ["customers", "vendors", "sales_invoices", "sales_orders", "quotes", "proformas",
               "deliveries", "receive_payments", "customer_deposits", "credit_notes"]


def _item(row, url_prefix):
    amount = row["amount"]
    due = row["due_date"]
    return {
        "id": row["id"],
        "type": row["_type"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "number": row["number"],
        "amount": float(amount) if amount is not None else None,
        "status": row["status"],
        "due_date": due.isoformat() if due is not None else None,
        "url_hint": f"{url_prefix}{row['id']}",
        "matched_field": row["matched_field"],
    }


@router.get("")
async def search(
    request: Request,
    q: str = Query("", description="Kata kunci, min 2 karakter"),
    scope: Optional[str] = Query(None, description="all | <type>"),
    limit: int = Query(8, ge=1, le=20),
    user: dict = Depends(get_current_user),
):
    query = (q or "").strip()
    if len(query) < 2:
        return {"query": query, "groups": []}

    scope = (scope or "all").strip().lower()
    if scope in ("all", ""):
        wanted = list(GROUP_ORDER)
    elif scope in GROUPS:
        wanted = [scope]
    else:
        raise HTTPException(status_code=400, detail=f"scope tidak dikenal: {scope}")

    tenant_id = user["tenant_id"]

    # Per-group READ authz (OWNER bypasses). Groups without access are omitted.
    policy = get_policy_engine()
    ctx = await policy.get_user_context(
        user_id=user["user_id"], tenant_id=tenant_id,
        subscription_role=user.get("role", "USER"),
    )
    allowed = []
    for g in wanted:
        module = GROUPS[g][0]
        try:
            if await policy.can(ctx, "R", module):
                allowed.append(g)
        except Exception as e:  # fail-closed on authz error
            logger.warning(f"search authz {g}: {e}")
    if not allowed:
        return {"query": query, "groups": []}

    pool = await get_pool()
    groups = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '500ms'")
            # customer cross-match ids (bounded)
            cust_rows = await conn.fetch(
                "SELECT id::text FROM customers WHERE tenant_id=$1 "
                "AND coalesce(deleted_at::text,'')='' "
                "AND search_text LIKE '%' || lower(unaccent($2)) || '%' LIMIT 500",
                tenant_id, query,
            )
            cust_ids = [r["id"] for r in cust_rows]

            for g in allowed:
                module, url_prefix, sql = GROUPS[g]
                # Errors propagate (clear 500), never swallowed into a 200-empty result.
                rows = await conn.fetch(sql, tenant_id, query, cust_ids, limit)
                if not rows:
                    continue
                items = []
                for r in rows:
                    d = dict(r)
                    d["_type"] = g
                    items.append(_item(d, url_prefix))
                groups.append({"type": g, "count": len(items), "items": items})

    return {"query": query, "groups": groups}
