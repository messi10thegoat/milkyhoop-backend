"""
Quotes (Penawaran) Router
Pre-sale quotes before conversion to Invoice or Sales Order.
NO journal entries - accounting impact happens on conversion.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from datetime import date, datetime, timedelta

from ..utils.tanggal_tenant import tanggal_dokumen
from ..services.penawaran_kedaluwarsa import kedaluwarsa, sql_kedaluwarsa, sql_menunggu_aktif
from ..services.sales_doc_calc import (
    compute_document, line_net, q2 as _q2, DocumentDiscountError,
)
from ..services.tax_factor import attach_dpp_factors, turunkan_tarif_baris
from ..services.pkp_guard import tolak_ppn_bila_non_pkp
from decimal import Decimal, ROUND_HALF_UP
import asyncpg
import logging
import uuid as uuid_module

from ..schemas.quotes import (
    CreateQuoteRequest,
    UpdateQuoteRequest,
    SendQuoteRequest,
    DeclineQuoteRequest,
    VoidQuoteRequest,
    ConvertToInvoiceRequest,
    ConvertToOrderRequest,
    DuplicateQuoteRequest,
    QuoteListResponse,
    QuoteDetailResponse,
    QuoteResponse,
    QuoteSummaryResponse,
    ExpiringQuotesResponse,
    QuoteListItem,
    QuoteDetail,
    QuoteItemResponse,
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


async def _quote_calc(conn, tenant_id, lines, discount_type, discount_value, fixed_amount=None) -> dict:
    """3h -- SATU jalan hitung Penawaran: create, update, dan konversi (3g) semuanya lewat
    kalkulator bersama (sama dengan Faktur/SO). Arti kolom Penawaran dipertahankan:
    subtotal = SIGMA neto baris (sesudah diskon baris), line_total = neto baris + PPN baris.
    percentage -> persen atas neto; fixed -> nilainya (atau `fixed_amount` bila diberikan).
    #39: tarif baris diturunkan SERVER dari kode pajak (sama dengan SO #34) -- di sini, jadi
    create, PATCH (termasuk hitung-ulang baris tersimpan), dan konversi ikut semuanya."""
    await turunkan_tarif_baris(conn, tenant_id, lines, "tax_id")
    await attach_dpp_factors(conn, tenant_id, lines, "tax_id")
    dval = Decimal(str(discount_value or 0))
    amt, pct = Decimal("0"), Decimal("0")
    if dval > 0 and discount_type == "percentage":
        pct = dval
    elif dval > 0:
        amt = dval if fixed_amount is None else fixed_amount
    try:
        doc = compute_document(lines, doc_discount_amount=amt, doc_discount_percent=pct)
    except DocumentDiscountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for ln in doc["items"]:
        ln["line_total"] = ln["total"]
    return doc


def _quote_line(r) -> dict:
    """Baris quote_items tersimpan -> masukan kalkulator."""
    return {
        "item_id": str(r["item_id"]) if r["item_id"] else None,
        "description": r["description"],
        "quantity": r["quantity"],
        "unit": r["unit"],
        "unit_price": r["unit_price"],
        "discount_percent": r["discount_percent"] or 0,
        "tax_id": str(r["tax_id"]) if r["tax_id"] else None,
        "tax_rate": r["tax_rate"] or 0,
        "group_name": r.get("group_name"),
        "sort_order": r.get("sort_order"),
    }


async def _quote_converted_doc(conn, tenant_id, quote, items) -> dict:
    """3g -- dokumen hasil konversi Penawaran (Faktur ATAU SO) dihitung ulang lewat kalkulator
    bersama, SAMA dengan create Faktur/SO: faktor DPP per kode, diskon dokumen dialokasikan
    SEBELUM PPN, Decimal 2dp. Dulu konversi MENYALIN pajak baris Penawaran (int(), sebelum
    diskon dokumen, tanpa faktor DPP) dan MEMBUANG diskon dokumen Penawaran -- faktur menagih
    lebih dari yang ditawarkan.

    Diskon dokumen Penawaran: 'percentage' -> persen yang sama atas neto baris yang dikonversi;
    'fixed' -> nilainya, dibagi pro rata neto baris bila hanya sebagian baris dikonversi
    (Penawaran berstatus 'converted' sesudahnya, jadi sisanya tak pernah ditagih)."""
    sel = [_quote_line(r) for r in items]
    dtype, dval = quote["discount_type"], Decimal(str(quote["discount_value"] or 0))
    fixed = None
    if dval > 0 and dtype != "percentage":
        all_rows = await conn.fetch("SELECT * FROM quote_items WHERE quote_id = $1", quote["id"])
        if len(sel) != len(all_rows):
            net_all = sum((line_net(_quote_line(r))["net"] for r in all_rows), Decimal("0"))
            net_sel = sum((line_net(ln)["net"] for ln in sel), Decimal("0"))
            fixed = dval if net_all == 0 else _q2(dval * net_sel / net_all)
    return await _quote_calc(conn, tenant_id, sel, dtype, dval, fixed_amount=fixed)


def calculate_item_totals(item: dict) -> dict:
    """Calculate line item totals."""
    quantity = Decimal(str(item.get("quantity", 1)))
    unit_price = Decimal(str(item.get("unit_price", 0)))
    discount_percent = Decimal(str(item.get("discount_percent", 0)))
    tax_rate = Decimal(str(item.get("tax_rate", 0)))

    subtotal = quantity * unit_price
    discount = subtotal * discount_percent / 100
    after_discount = subtotal - discount
    tax_amount = after_discount * tax_rate / 100
    line_total = after_discount + tax_amount

    return {**item, "tax_amount": int(tax_amount), "line_total": int(line_total)}


def calculate_quote_totals(
    items: list, discount_type: str, discount_value: float
) -> dict:
    """Calculate quote totals from items."""
    subtotal = sum(
        item.get("line_total", 0) - item.get("tax_amount", 0) for item in items
    )
    total_tax = sum(item.get("tax_amount", 0) for item in items)

    if discount_type == "percentage":
        discount_amount = int(
            Decimal(str(subtotal)) * Decimal(str(discount_value)) / 100
        )
    else:
        discount_amount = int(discount_value)

    total_amount = subtotal - discount_amount + total_tax

    return {
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "tax_amount": total_tax,
        "total_amount": total_amount,
    }


def resolve_dp(
    total_amount: int,
    dp_amount: Optional[int],
    dp_percent: Optional[float],
) -> dict:
    """FIX_P2_QUOTEDP 2026-06-16 — resolve canonical down-payment.

    NO-LEDGER: this is purely a number stored on the non-posting quote document.
    Canonical rule: dp_amount is authoritative.
    - If only dp_percent is provided -> dp_amount = round(total * pct / 100, 2).
    - If dp_amount is provided -> keep it, derive dp_percent for display.
    - If neither -> both None (block absent in PDF; byte-identical to pre-change).
    Returns ints for amount (rupiah convention) and float for percent.
    """
    if dp_amount is None and dp_percent is None:
        return {"dp_amount": None, "dp_percent": None}

    total = Decimal(str(total_amount or 0))

    if dp_amount is not None:
        amount = Decimal(str(dp_amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        # derive percent for display (only when total > 0)
        if total > 0:
            pct = (amount / total * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            pct = Decimal(str(dp_percent)) if dp_percent is not None else None
        return {
            "dp_amount": int(amount),
            "dp_percent": float(pct) if pct is not None else None,
        }

    # Only dp_percent provided -> compute dp_amount = round(total * pct / 100, 2)
    pct = Decimal(str(dp_percent))
    amount = (total * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "dp_amount": int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        "dp_percent": float(pct),
    }


# ============================================================================
# LIST & DETAIL ENDPOINTS
# ============================================================================


@router.get("", response_model=QuoteListResponse)
async def list_quotes(
    request: Request,
    # Kosakata = CHECK constraint tabel (hidup di MIGRASI, jadi ia KODE) UNION
    # nilai khusus yang punya CABANG SENDIRI di handler ini. Mengambilnya dari
    # `SELECT DISTINCT status` akan MEMBEKUKAN DRIFT jadi spesifikasi: `posted`
    # ada di constraint sales_invoices tapi nol baris memakainya, dan
    # `unpaid`/`active`/`overdue`/`all` tak pernah tersimpan sebagai nilai kolom
    # sama sekali -- mereka dihitung. Sebelum ini `status` adalah str polos,
    # jadi nilai asing dijawab 200 daftar kosong dan pemanggil tak bisa
    # membedakan "tidak ada data" dari "parameter tidak dimengerti".
    status: Optional[
        Literal["all", "draft", "sent", "viewed", "accepted", "declined",
                "expired", "converted", "void", "invoiced"]
    ] = Query("all"),
    customer_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    page: int = Query(1, ge=1),
    offset: int = Query(0, ge=0),
):
    """List quotes with filters."""
    # Terima 3 konvensi paginasi: skip (warisan) + page/offset (dulu DIABAIKAN diam —
    # hanya skip bekerja). Prioritas: offset > page > skip -> satu OFFSET efektif.
    eff_offset = offset if offset > 0 else ((page - 1) * limit if page > 1 else skip)
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            # Map frontend status aliases
            if status == "invoiced":
                status = "converted"
            if status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}")
                params.append(uuid_module.UUID(customer_id))
                param_idx += 1

            if start_date:
                conditions.append(f"quote_date >= ${param_idx}")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"quote_date <= ${param_idx}")
                params.append(end_date)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(quote_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR subject ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = quotes.tenant_id AND c.search_text ILIKE ${param_idx}))"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(quote_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR subject ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = quotes.tenant_id AND c.search_text ILIKE ${param_idx}))"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            where_clause = " AND ".join(conditions)

            # Count
            count_query = f"SELECT COUNT(*) FROM quotes WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # List
            list_query = f"""
                SELECT id, quote_number, quote_date, expiry_date, customer_id, customer_name,
                       subject, subtotal, discount_amount, tax_amount, total_amount,
                       dp_amount, dp_percent, status,
                       converted_to_type, converted_to_id, created_at
                FROM quotes
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, eff_offset])
            rows = await conn.fetch(list_query, *params)
            # Q-017: SATU aturan (services/penawaran_kedaluwarsa) di tanggal BISNIS tenant
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])

            items = []
            for row in rows:
                is_expired = kedaluwarsa(row["status"], row["expiry_date"], hari_ini)
                items.append(
                    QuoteListItem(
                        id=str(row["id"]),
                        quote_number=row["quote_number"],
                        quote_date=row["quote_date"].isoformat(),
                        expiry_date=row["expiry_date"].isoformat()
                        if row["expiry_date"]
                        else None,
                        customer_id=str(row["customer_id"]),
                        customer_name=row["customer_name"],
                        subject=row["subject"],
                        subtotal=row["subtotal"],
                        discount_amount=row["discount_amount"],
                        tax_amount=row["tax_amount"],
                        total_amount=row["total_amount"],
                        # FIX_P2_QUOTEDP 2026-06-16
                        dp_amount=int(row["dp_amount"]) if row["dp_amount"] is not None else None,
                        dp_percent=float(row["dp_percent"]) if row["dp_percent"] is not None else None,
                        status=row["status"],
                        converted_to_type=row["converted_to_type"],
                        converted_to_id=str(row["converted_to_id"])
                        if row["converted_to_id"]
                        else None,
                        created_at=row["created_at"].isoformat(),
                        is_expired=is_expired,
                    )
                )

            page = (eff_offset // limit) + 1 if limit > 0 else 1
            total_pages = (total + limit - 1) // limit if limit > 0 else 1

            return QuoteListResponse(
                items=items,
                total=total,
                has_more=(eff_offset + limit) < total,
                page=page,
                limit=limit,
                total_pages=total_pages,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing quotes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list quotes")


@router.get("/expiring", response_model=ExpiringQuotesResponse)
async def get_expiring_quotes(
    request: Request, days: int = Query(7, ge=1, le=30, description="Days until expiry")
):
    """Get quotes expiring within specified days."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Q-017: menunggu jawaban (sent/viewed), BELUM kedaluwarsa, habis dalam `days` hari — tanggal BISNIS
            hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])
            query = f"""
                SELECT id, quote_number, customer_name, expiry_date, total_amount
                FROM quotes
                WHERE tenant_id = $1
                AND {sql_menunggu_aktif("$3")}
                AND expiry_date IS NOT NULL
                AND expiry_date <= $3::date + ($2::INTEGER)
                ORDER BY expiry_date ASC
            """
            rows = await conn.fetch(query, ctx["tenant_id"], days, hari_ini)

            items = []
            for row in rows:
                days_until = (row["expiry_date"] - hari_ini).days
                items.append(
                    {
                        "id": str(row["id"]),
                        "quote_number": row["quote_number"],
                        "customer_name": row["customer_name"],
                        "expiry_date": row["expiry_date"].isoformat(),
                        "total_amount": row["total_amount"],
                        "days_until_expiry": days_until,
                    }
                )

            return ExpiringQuotesResponse(success=True, data=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting expiring quotes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get expiring quotes")


@router.get("/summary", response_model=QuoteSummaryResponse)
async def get_quote_summary(request: Request):
    """Get quote statistics summary."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            query = """
                SELECT
                    COUNT(*) as total_quotes,
                    COUNT(*) FILTER (WHERE status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE status = 'sent') as sent_count,
                    COUNT(*) FILTER (WHERE status = 'accepted') as accepted_count,
                    COUNT(*) FILTER (WHERE status = 'declined') as declined_count,
                    COUNT(*) FILTER (WHERE status = 'expired') as expired_count,
                    COUNT(*) FILTER (WHERE status = 'converted') as converted_count,
                    COUNT(*) FILTER (WHERE status = 'viewed') as viewed_count,
                    COUNT(*) FILTER (WHERE status = 'void') as void_count,
                    COALESCE(SUM(total_amount), 0) as total_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'accepted'), 0) as accepted_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'sent'), 0) as pending_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status <> 'void'), 0) as active_value,
                    COALESCE(SUM(total_amount) FILTER (WHERE status = 'void'), 0) as void_value,
                    -- Q-017: medan BARU dengan SATU aturan (services/penawaran_kedaluwarsa); medan lama tetap artinya
                    COUNT(*) FILTER (WHERE {aktif}) as pending_active_count,
                    COALESCE(SUM(total_amount) FILTER (WHERE {aktif}), 0) as pending_active_value,
                    COUNT(*) FILTER (WHERE {lewat}) as expired_pending_count,
                    COALESCE(SUM(total_amount) FILTER (WHERE {lewat}), 0) as expired_pending_value
                FROM quotes
                WHERE tenant_id = $1
            """.format(aktif=sql_menunggu_aktif("$2"), lewat=sql_kedaluwarsa("$2"))
            row = await conn.fetchrow(query, ctx["tenant_id"], await tanggal_dokumen(conn, ctx["tenant_id"]))

            return QuoteSummaryResponse(
                success=True,
                data={
                    "total_quotes": row["total_quotes"],
                    "draft_count": row["draft_count"],
                    "sent_count": row["sent_count"],
                    "accepted_count": row["accepted_count"],
                    "declined_count": row["declined_count"],
                    "expired_count": row["expired_count"],
                    "converted_count": row["converted_count"],
                    "viewed_count": row["viewed_count"],
                    "void_count": row["void_count"],
                    "total_value": row["total_value"],
                    "accepted_value": row["accepted_value"],
                    "pending_value": row["pending_value"],
                    "active_value": row["active_value"],
                    "void_value": row["void_value"],
                    "pending_active_count": row["pending_active_count"],
                    "pending_active_value": row["pending_active_value"],
                    "expired_pending_count": row["expired_pending_count"],
                    "expired_pending_value": row["expired_pending_value"],
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quote summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get quote summary")


@router.get("/{quote_id}", response_model=QuoteDetailResponse)
async def get_quote_detail(request: Request, quote_id: str):
    """Get quote detail with items."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get quote header
            quote_query = """
                SELECT * FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """
            quote = await conn.fetchrow(
                quote_query, uuid_module.UUID(quote_id), ctx["tenant_id"]
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            # Get items
            items_query = """
                SELECT qi.*, p.nama_produk AS product_name
                FROM quote_items qi
                LEFT JOIN products p ON p.id = qi.item_id AND p.tenant_id = $2
                WHERE qi.quote_id = $1
                ORDER BY qi.sort_order, qi.id
            """
            items = await conn.fetch(items_query, uuid_module.UUID(quote_id), ctx["tenant_id"])

            # Q-017: SATU aturan (services/penawaran_kedaluwarsa) di tanggal BISNIS tenant
            is_expired = kedaluwarsa(quote["status"], quote["expiry_date"], await tanggal_dokumen(conn, ctx["tenant_id"]))

            return QuoteDetailResponse(
                success=True,
                data=QuoteDetail(
                    id=str(quote["id"]),
                    quote_number=quote["quote_number"],
                    quote_date=quote["quote_date"].isoformat(),
                    expiry_date=quote["expiry_date"].isoformat()
                    if quote["expiry_date"]
                    else None,
                    customer_id=str(quote["customer_id"]),
                    customer_name=quote["customer_name"],
                    customer_email=quote["customer_email"],
                    reference=quote["reference"],
                    subject=quote["subject"],
                    subtotal=quote["subtotal"],
                    discount_type=quote["discount_type"],
                    discount_value=float(quote["discount_value"]),
                    discount_amount=quote["discount_amount"],
                    tax_amount=quote["tax_amount"],
                    total_amount=quote["total_amount"],
                    # FIX_P2_QUOTEDP 2026-06-16 — down-payment (NO-LEDGER, display only)
                    dp_amount=int(quote["dp_amount"]) if quote["dp_amount"] is not None else None,
                    dp_percent=float(quote["dp_percent"]) if quote["dp_percent"] is not None else None,
                    status=quote["status"],
                    converted_to_type=quote["converted_to_type"],
                    converted_to_id=str(quote["converted_to_id"])
                    if quote["converted_to_id"]
                    else None,
                    converted_at=quote["converted_at"].isoformat()
                    if quote["converted_at"]
                    else None,
                    notes=quote["notes"],
                    terms=quote["terms"],
                    footer=quote["footer"],
                    opening_text=quote["opening_text"],
                    closing_text=quote["closing_text"],
                    # BATCH1 B1: quote IS the DP tagih instrument -> surface the transfer rekening
                    payment_bank_name=quote["payment_bank_name"],
                    payment_account_number=quote["payment_account_number"],
                    payment_account_holder=quote["payment_account_holder"],
                    items=[
                        QuoteItemResponse(
                            id=str(item["id"]),
                            item_id=str(item["item_id"]) if item["item_id"] else None,
                            description=item["description"],
                            product_name=item["product_name"],
                            quantity=float(item["quantity"]),
                            unit=item["unit"],
                            unit_price=item["unit_price"],
                            discount_percent=float(item["discount_percent"]),
                            tax_id=str(item["tax_id"]) if item["tax_id"] else None,
                            tax_rate=float(item["tax_rate"]),
                            tax_amount=item["tax_amount"],
                            line_total=item["line_total"],
                            group_name=item["group_name"],
                            sort_order=item["sort_order"],
                        )
                        for item in items
                    ],
                    created_at=quote["created_at"].isoformat(),
                    updated_at=quote["updated_at"].isoformat(),
                    created_by=str(quote["created_by"])
                    if quote["created_by"]
                    else None,
                    sent_at=quote["sent_at"].isoformat() if quote["sent_at"] else None,
                    viewed_at=quote["viewed_at"].isoformat()
                    if quote["viewed_at"]
                    else None,
                    accepted_at=quote["accepted_at"].isoformat()
                    if quote["accepted_at"]
                    else None,
                    declined_at=quote["declined_at"].isoformat()
                    if quote["declined_at"]
                    else None,
                    declined_reason=quote["declined_reason"],
                    is_expired=is_expired,
                ),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting quote detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get quote detail")


# ============================================================================
# CREATE, UPDATE, DELETE ENDPOINTS
# ============================================================================


@router.post("", response_model=QuoteResponse)
async def create_quote(request: Request, body: CreateQuoteRequest):
    """Create a new quote (draft status)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate quote number
                from ..services.document_number import bersihkan_nomor_dokumen_opsional
                _nomor_manual = bersihkan_nomor_dokumen_opsional(getattr(body, "quote_number", None))
                if _nomor_manual:
                    if await conn.fetchval(
                        "SELECT 1 FROM quotes WHERE tenant_id=$1 AND quote_number=$2",
                        ctx["tenant_id"], _nomor_manual):
                        raise HTTPException(status_code=409, detail="Nomor sudah dipakai di tenant ini.")
                    quote_number = _nomor_manual
                else:
                    quote_number = await conn.fetchval(
                        "SELECT generate_quote_number($1, 'QUO')", ctx["tenant_id"]
                    )

                # 3h: kalkulator bersama (bukan calculate_item_totals/int()).
                _doc = await _quote_calc(
                    conn, ctx["tenant_id"], [item.model_dump() for item in body.items],
                    body.discount_type, body.discount_value,
                )
                # #39: tenant non-PKP tak boleh menawarkan PPN (sama dengan SO #34 / faktur).
                await tolak_ppn_bila_non_pkp(conn, ctx["tenant_id"], _doc["tax_amount"])
                calculated_items = _doc["items"]
                totals = {
                    "subtotal": _doc["net_subtotal"],
                    "discount_amount": _doc["doc_discount"],
                    "tax_amount": _doc["tax_amount"],
                    "total_amount": _doc["total_amount"],
                }

                # FIX_P2_QUOTEDP 2026-06-16 — resolve canonical down-payment (NO-LEDGER)
                dp = resolve_dp(
                    totals["total_amount"], body.dp_amount, body.dp_percent
                )

                # Auto-resolve customer_name if not provided
                if not body.customer_name and body.customer_id:
                    cust = await conn.fetchrow(
                        "SELECT nama FROM customers WHERE id = $1 AND tenant_id = $2",
                        uuid_module.UUID(body.customer_id),
                        ctx["tenant_id"],
                    )
                    if cust:
                        body.customer_name = cust["nama"]

                # Create quote
                quote_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO quotes (
                        id, tenant_id, quote_number, quote_date, expiry_date,
                        customer_id, customer_name, customer_email,
                        reference, subject,
                        subtotal, discount_type, discount_value, discount_amount,
                        tax_amount, total_amount, status,
                        notes, terms, footer, opening_text, closing_text,
                        payment_bank_name, payment_account_number, payment_account_holder, created_by,
                        dp_amount, dp_percent
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8,
                        $9, $10,
                        $11, $12, $13, $14,
                        $15, $16, 'draft',
                        $17, $18, $19, $20, $21,
                        $22, $23, $24, $25,
                        $26, $27
                    )
                """,
                    quote_id,
                    ctx["tenant_id"],
                    quote_number,
                    body.quote_date,
                    body.expiry_date,
                    uuid_module.UUID(body.customer_id),
                    body.customer_name,
                    body.customer_email,
                    body.reference,
                    body.subject,
                    totals["subtotal"],
                    body.discount_type,
                    body.discount_value,
                    totals["discount_amount"],
                    totals["tax_amount"],
                    totals["total_amount"],
                    body.notes,
                    body.terms,
                    body.footer,
                    body.opening_text,
                    body.closing_text,
                    body.payment_bank_name,
                    body.payment_account_number,
                    body.payment_account_holder,
                    ctx["user_id"],
                    # FIX_P2_QUOTEDP 2026-06-16 (NUMERIC columns expect Decimal/None)
                    Decimal(str(dp["dp_amount"])) if dp["dp_amount"] is not None else None,
                    Decimal(str(dp["dp_percent"])) if dp["dp_percent"] is not None else None,
                )

                # Create items
                for idx, item in enumerate(calculated_items):
                    await conn.execute(
                        """
                        INSERT INTO quote_items (
                            id, quote_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total,
                            group_name, sort_order
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                        )
                    """,
                        uuid_module.uuid4(),
                        quote_id,
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
                        item.get("group_name"),
                        item.get("sort_order", idx),
                    )

                return QuoteResponse(
                    success=True,
                    message="Quote created successfully",
                    data={
                        "id": str(quote_id),
                        "quote_number": quote_number,
                        "total_amount": totals["total_amount"],
                        # FIX_P2_QUOTEDP 2026-06-16
                        "dp_amount": dp["dp_amount"],
                        "dp_percent": dp["dp_percent"],
                    },
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create quote")


@router.patch("/{quote_id}", response_model=QuoteResponse)
async def update_quote(request: Request, quote_id: str, body: UpdateQuoteRequest):
    """Update an existing quote (draft only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        # Optimistic concurrency (opt-in If-Match): reject a stale write.
        from ..services.optimistic_concurrency import assert_if_match_row
        await assert_if_match_row(request, "quotes", quote_id)

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Check quote exists and is draft
                quote = await conn.fetchrow(
                    """
                    SELECT id, status FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                )

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                from ..services.document_number import bersihkan_nomor_dokumen_opsional
                _new_num = bersihkan_nomor_dokumen_opsional(getattr(body, "quote_number", None))
                if _new_num is None:
                    body.__pydantic_fields_set__.discard("quote_number")
                else:
                    if quote["status"] != "draft":
                        raise HTTPException(status_code=400, detail="Nomor dokumen yang sudah terbit tidak dapat diubah")
                    if await conn.fetchval(
                        "SELECT 1 FROM quotes WHERE tenant_id=$1 AND quote_number=$2 AND id <> $3",
                        ctx["tenant_id"], _new_num, uuid_module.UUID(quote_id)):
                        raise HTTPException(status_code=409, detail="Nomor sudah dipakai di tenant ini.")
                    body.quote_number = _new_num

                if quote["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft quotes can be updated"
                    )

                # Build update query
                updates = []
                params = []
                param_idx = 1

                # `exclude_unset=True` adalah pembedanya: Pydantic mencatat
                # field mana yang BENAR-BENAR dikirim (`model_fields_set`), jadi
                # null eksplisit ikut terbawa sementara yang absen tidak.
                #
                # Yang lama, `if value is not None`, membuat TIGA bentuk
                # permintaan tak bisa dibedakan sama sekali:
                #   kunci absen -> tak diubah        (benar)
                #   null        -> DIABAIKAN, 200    (SALAH: pengguna meminta
                #                                     mengosongkan, dijawab
                #                                     "berhasil", nilainya tetap)
                #   ""          -> tersimpan sebagai string kosong
                # Akibatnya Pesanan menyimpan NULL dan Penawaran menyimpan ''
                # untuk makna yang sama. Sekarang keduanya seragam:
                # absen = jangan ubah, null ATAU "" = NULL.
                #
                # Sama dengan PATCH Pesanan (f1ce3564) dan faktur (fd5a9dc5).
                # `items` dan field DP ditangani blok tersendiri di bawah.
                update_data = body.model_dump(
                    exclude_unset=True, exclude={"items", "dp_amount", "dp_percent"}
                )

                for field, value in update_data.items():
                    if field == "customer_id" and value is not None:
                        value = uuid_module.UUID(str(value))
                    updates.append(f"{field} = ${param_idx}")
                    params.append(value)
                    param_idx += 1

                # 3h: hitung ulang bila BARIS atau DISKON berubah. Dulu suntingan diskon-saja
                # menulis discount_type/value tapi membiarkan discount_amount/tax/total basi.
                _recalc = body.items is not None or bool(
                    {"discount_type", "discount_value"} & body.model_fields_set
                )
                if _recalc:
                    if body.items is not None:
                        _lines = [item.model_dump() for item in body.items]
                    else:
                        _lines = [_quote_line(r) for r in await conn.fetch(
                            "SELECT * FROM quote_items WHERE quote_id = $1 ORDER BY sort_order, id",
                            uuid_module.UUID(quote_id),
                        )]
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM quote_items WHERE quote_id = $1",
                        uuid_module.UUID(quote_id),
                    )

                    discount_type = body.discount_type or "fixed"
                    discount_value = body.discount_value or 0

                    # Get current discount info if not provided
                    if body.discount_type is None or body.discount_value is None:
                        current = await conn.fetchrow(
                            "SELECT discount_type, discount_value FROM quotes WHERE id = $1",
                            uuid_module.UUID(quote_id),
                        )
                        discount_type = body.discount_type or current["discount_type"]
                        discount_value = (
                            body.discount_value
                            if body.discount_value is not None
                            else float(current["discount_value"])
                        )

                    _doc = await _quote_calc(conn, ctx["tenant_id"], _lines, discount_type, discount_value)
                    await tolak_ppn_bila_non_pkp(conn, ctx["tenant_id"], _doc["tax_amount"])  # #39
                    calculated_items = _doc["items"]
                    totals = {
                        "subtotal": _doc["net_subtotal"],
                        "discount_amount": _doc["doc_discount"],
                        "tax_amount": _doc["tax_amount"],
                        "total_amount": _doc["total_amount"],
                    }

                    # Add totals to update
                    updates.append(f"subtotal = ${param_idx}")
                    params.append(totals["subtotal"])
                    param_idx += 1

                    updates.append(f"discount_amount = ${param_idx}")
                    params.append(totals["discount_amount"])
                    param_idx += 1

                    updates.append(f"tax_amount = ${param_idx}")
                    params.append(totals["tax_amount"])
                    param_idx += 1

                    updates.append(f"total_amount = ${param_idx}")
                    params.append(totals["total_amount"])
                    param_idx += 1

                    # Insert new items
                    for idx, item in enumerate(calculated_items):
                        await conn.execute(
                            """
                            INSERT INTO quote_items (
                                id, quote_id, item_id, description,
                                quantity, unit, unit_price, discount_percent,
                                tax_id, tax_rate, tax_amount, line_total,
                                group_name, sort_order
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                            )
                        """,
                            uuid_module.uuid4(),
                            uuid_module.UUID(quote_id),
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
                            item.get("group_name"),
                            item.get("sort_order") if item.get("sort_order") is not None else idx,
                        )

                # FIX_P2_QUOTEDP 2026-06-16 — resolve canonical down-payment (NO-LEDGER).
                # dp_amount is authoritative. Recompute against the effective total
                # (new total if items changed this update, else the stored total).
                # Only touch dp columns when the client actually sent a dp_* field.
                if body.dp_amount is not None or body.dp_percent is not None:
                    if _recalc:
                        effective_total = totals["total_amount"]
                    else:
                        eff = await conn.fetchrow(
                            "SELECT total_amount FROM quotes WHERE id = $1",
                            uuid_module.UUID(quote_id),
                        )
                        effective_total = eff["total_amount"] if eff else 0

                    dp = resolve_dp(effective_total, body.dp_amount, body.dp_percent)

                    updates.append(f"dp_amount = ${param_idx}")
                    params.append(
                        Decimal(str(dp["dp_amount"]))
                        if dp["dp_amount"] is not None
                        else None
                    )
                    param_idx += 1

                    updates.append(f"dp_percent = ${param_idx}")
                    params.append(
                        Decimal(str(dp["dp_percent"]))
                        if dp["dp_percent"] is not None
                        else None
                    )
                    param_idx += 1

                if updates:
                    params.append(uuid_module.UUID(quote_id))
                    params.append(ctx["tenant_id"])
                    update_query = f"""
                        UPDATE quotes SET {', '.join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """
                    await conn.execute(update_query, *params)

                return QuoteResponse(
                    success=True,
                    message="Quote updated successfully",
                    data={"id": quote_id},
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update quote")


@router.delete("/{quote_id}", response_model=QuoteResponse)
async def delete_quote(request: Request, quote_id: str):
    """Delete a quote (draft only)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check quote exists and is draft
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote["status"] != "draft":
                raise HTTPException(
                    status_code=400, detail="Only draft quotes can be deleted"
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
                await conn.execute(
                    "SELECT set_config('app.user_id', $1, true)",
                    str(ctx["user_id"] or ""),
                )
                # Delete (cascade deletes items)
                await conn.execute(
                    "DELETE FROM quotes WHERE id = $1 AND tenant_id = $2",
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                )

            return QuoteResponse(
                success=True,
                message="Quote deleted successfully",
                data={"quote_number": quote["quote_number"]},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete quote")


# ============================================================================
# WORKFLOW ENDPOINTS
# ============================================================================


@router.post("/{quote_id}/send", response_model=QuoteResponse)
async def send_quote(request: Request, quote_id: str, body: SendQuoteRequest = None):
    """Mark quote as sent."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check quote
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote["status"] not in ("draft", "sent"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot send quote with status '{quote['status']}'",
                )

            # SUREL PENAWARAN BELUM ADA -- DAN JALUR INI DULU BERPURA-PURA.
            # Sampai 12 Sep 2026, `send_email=true` mengubah status jadi
            # 'sent', mencatat log "Quote email notification queued", lalu
            # menjawab "Quote sent successfully" -- tanpa satu surel pun keluar
            # (panggilan pengirimnya dikomentari; `email_service` tak punya
            # pengirim penawaran). Pemilik bisa mengira pelanggannya sudah
            # menerima penawaran. Kepura-puraan lebih buruk daripada fitur yang
            # tak ada.
            #
            # Penolakan diletakkan SEBELUM `UPDATE status`: permintaan yang
            # ditolak tak boleh diam-diam menandai penawaran "terkirim".
            # Fitur surel sungguhan = tiket terpisah (kunci Resend terpasang di
            # produksi; yang belum ada templat, lampiran PDF, domain pengirim).
            if body and body.send_email:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Pengiriman penawaran lewat surel belum tersedia. "
                        "Penawaran TIDAK ditandai terkirim. Kirim tanpa surel "
                        "(send_email=false) untuk menandainya terkirim, lalu "
                        "bagikan PDF-nya lewat saluran lain."
                    ),
                )

            # Update status
            await conn.execute(
                """
                UPDATE quotes SET status = 'sent', sent_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            return QuoteResponse(
                success=True,
                message="Quote sent successfully",
                data={"quote_number": quote["quote_number"], "status": "sent"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send quote")


@router.post("/{quote_id}/accept", response_model=QuoteResponse)
async def accept_quote(request: Request, quote_id: str):
    """Mark quote as accepted."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote["status"] not in ("sent", "viewed"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot accept quote with status '{quote['status']}'",
                )

            await conn.execute(
                """
                UPDATE quotes SET status = 'accepted', accepted_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            return QuoteResponse(
                success=True,
                message="Quote accepted",
                data={"quote_number": quote["quote_number"], "status": "accepted"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error accepting quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to accept quote")


# ═════════════════════════════════════════════════════════════════════════
# GUARD UANG MUKA — definisinya PINDAH ke services/dp_guard.py.
#
# Alasannya bukan kerapian: `customer_deposits` menempel ke TIGA dokumen
# lewat tiga kolom (`quote_id`, `sales_order_id`, `proforma_id`), jadi guard
# yang sama dibutuhkan di router lain. Terukur 2026-09-03: Pesanan Penjualan
# bocor di DUA jalur (cancel dan delete), sementara Proforma sudah punya
# penjagaan sendiri lewat `compute_paid_amount`.
# ═════════════════════════════════════════════════════════════════════════

from ..services.dp_guard import tolak_bila_ada_uang_muka_aktif


async def _tolak_bila_ada_uang_muka_aktif(conn, quote_id, tenant_id, aksi: str):
    """Selubung tipis supaya pemanggil di berkas ini tetap ringkas."""
    await tolak_bila_ada_uang_muka_aktif(
        conn, "quote_id", quote_id, tenant_id, aksi, "Penawaran"
    )


@router.post("/{quote_id}/decline", response_model=QuoteResponse)
async def decline_quote(request: Request, quote_id: str, body: DeclineQuoteRequest):
    """Mark quote as declined."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote["status"] not in ("sent", "viewed"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot decline quote with status '{quote['status']}'",
                )

            await _tolak_bila_ada_uang_muka_aktif(
                conn, uuid_module.UUID(quote_id), ctx["tenant_id"], "ditolak"
            )

            await conn.execute(
                """
                UPDATE quotes SET status = 'declined', declined_at = NOW(), declined_reason = $3
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
                body.reason,
            )

            return QuoteResponse(
                success=True,
                message="Quote declined",
                data={"quote_number": quote["quote_number"], "status": "declined"},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error declining quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to decline quote")


# ============================================================================
# VOID QUOTE
# ============================================================================


@router.post("/{quote_id}/void", response_model=QuoteResponse)
async def void_quote(request: Request, quote_id: str, body: VoidQuoteRequest = None):
    """
    Void a quote. Only quotes in 'draft' or 'sent' status can be voided.
    Quotes have no accounting impact, so no journal reversals are needed.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number, total_amount, customer_name
                FROM quotes
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            if quote["status"] not in ("draft", "sent"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot void quote with status '{quote['status']}'. Only draft or sent quotes can be voided.",
                )

            await _tolak_bila_ada_uang_muka_aktif(
                conn, uuid_module.UUID(quote_id), ctx["tenant_id"], "dibatalkan"
            )

            await conn.execute(
                """
                UPDATE quotes SET status = 'void', updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            reason_str = body.reason if body and body.reason else "No reason given"
            logger.info(
                f"Quote voided: {quote['quote_number']} "
                f"(customer={quote['customer_name']}, amount={quote['total_amount']}, "
                f"reason={reason_str})"
            )

            return QuoteResponse(
                success=True,
                message="Quote voided successfully",
                data={
                    "quote_id": quote_id,
                    "quote_number": quote["quote_number"],
                    "status": "void",
                    "previous_status": quote["status"],
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding quote {quote_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void quote")


@router.post("/{quote_id}/duplicate", response_model=QuoteResponse)
async def duplicate_quote(
    request: Request, quote_id: str, body: DuplicateQuoteRequest = None
):
    """Duplicate a quote."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get original quote
                quote = await conn.fetchrow(
                    """
                    SELECT * FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                )

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                # Get original items
                items = await conn.fetch(
                    """
                    SELECT * FROM quote_items WHERE quote_id = $1 ORDER BY sort_order
                """,
                    uuid_module.UUID(quote_id),
                )

                # Generate new number
                new_number = await conn.fetchval(
                    "SELECT generate_quote_number($1, 'QUO')", ctx["tenant_id"]
                )

                # Create new quote
                new_id = uuid_module.uuid4()
                new_date = (
                    body.quote_date
                    if body and body.quote_date
                    else await tanggal_dokumen(conn, ctx["tenant_id"])
                )
                new_expiry = (
                    body.expiry_date
                    if body and body.expiry_date
                    else quote["expiry_date"]
                )

                await conn.execute(
                    """
                    INSERT INTO quotes (
                        id, tenant_id, quote_number, quote_date, expiry_date,
                        customer_id, customer_name, customer_email,
                        reference, subject,
                        subtotal, discount_type, discount_value, discount_amount,
                        tax_amount, total_amount, status,
                        notes, terms, footer, opening_text, closing_text, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8,
                        $9, $10,
                        $11, $12, $13, $14,
                        $15, $16, 'draft',
                        $17, $18, $19, $20, $21, $22
                    )
                """,
                    new_id,
                    ctx["tenant_id"],
                    new_number,
                    new_date,
                    new_expiry,
                    str(quote["customer_id"]),
                    quote["customer_name"],
                    quote["customer_email"],
                    quote["reference"],
                    quote["subject"],
                    quote["subtotal"],
                    quote["discount_type"],
                    quote["discount_value"],
                    quote["discount_amount"],
                    quote["tax_amount"],
                    quote["total_amount"],
                    quote["notes"],
                    quote["terms"],
                    quote["footer"],
                    quote["opening_text"],
                    quote["closing_text"],
                    ctx["user_id"],
                )

                # Copy items
                for item in items:
                    await conn.execute(
                        """
                        INSERT INTO quote_items (
                            id, quote_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total,
                            group_name, sort_order
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                        )
                    """,
                        uuid_module.uuid4(),
                        new_id,
                        item["item_id"],
                        item["description"],
                        item["quantity"],
                        item["unit"],
                        item["unit_price"],
                        item["discount_percent"],
                        item["tax_id"],
                        item["tax_rate"],
                        item["tax_amount"],
                        item["line_total"],
                        item["group_name"],
                        item["sort_order"],
                    )

                return QuoteResponse(
                    success=True,
                    message="Quote duplicated successfully",
                    data={
                        "id": str(new_id),
                        "quote_number": new_number,
                        "original_quote_number": quote["quote_number"],
                    },
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error duplicating quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to duplicate quote")


# ============================================================================
# CONVERSION ENDPOINTS
# ============================================================================


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



@router.post("/{quote_id}/to-invoice", response_model=QuoteResponse)
async def convert_to_invoice(
    request: Request, quote_id: str, body: ConvertToInvoiceRequest = None
):
    """Convert quote to invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get quote
                quote = await conn.fetchrow(
                    """
                    SELECT * FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                )

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                if quote["status"] not in ("sent", "accepted", "viewed"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot convert quote with status '{quote['status']}'",
                    )

                # Get items
                items_query = "SELECT * FROM quote_items WHERE quote_id = $1"
                if body and body.item_ids:
                    items_query += " AND id = ANY($2)"
                    items = await conn.fetch(
                        items_query,
                        uuid_module.UUID(quote_id),
                        [uuid_module.UUID(id) for id in body.item_ids],
                    )
                else:
                    items = await conn.fetch(items_query, uuid_module.UUID(quote_id))

                if not items:
                    raise HTTPException(status_code=400, detail="No items to convert")

                # Generate invoice number
                invoice_number = await conn.fetchval(
                    "SELECT generate_sales_invoice_number($1, 'INV')", ctx["tenant_id"]
                )

                # Create invoice
                invoice_id = uuid_module.uuid4()
                invoice_date = (
                    body.invoice_date
                    if body and body.invoice_date
                    else await tanggal_dokumen(conn, ctx["tenant_id"])
                )
                due_date = (
                    body.due_date
                    if body and body.due_date
                    else (invoice_date + timedelta(days=30))
                )

                # 3g: kalkulator bersama (lihat _quote_converted_doc), bukan salinan angka Penawaran.
                _doc = await _quote_converted_doc(conn, ctx["tenant_id"], quote, items)

                await conn.execute(
                    """
                    INSERT INTO sales_invoices (
                        id, tenant_id, invoice_number, invoice_date, due_date,
                        customer_id, customer_name,
                        subtotal, tax_amount, total_amount,
                        status, quote_id, created_by,
                        payment_bank_name, payment_account_number, payment_account_holder,
                        discount_percent, discount_amount
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7,
                        $8, $9, $10,
                        'draft', $11, $12, $13, $14, $15, $16, $17
                    )
                """,
                    invoice_id,
                    ctx["tenant_id"],
                    invoice_number,
                    invoice_date,
                    due_date,
                    str(quote["customer_id"]),
                    quote["customer_name"],
                    _doc["gross_subtotal"],
                    _doc["tax_amount"],
                    _doc["total_amount"],
                    uuid_module.UUID(quote_id),
                    ctx["user_id"],
                    # Pewarisan rekening tujuan cetak (tiket MASTER). Yang
                    # EKSPLISIT di body menang; kalau absen, warisi dari
                    # Penawaran. Preseden persis ini sudah ada di T199
                    # (quote -> Sales Order).
                    _rek_eksplisit(body, "payment_bank_name")
                    or quote["payment_bank_name"],
                    _rek_eksplisit(body, "payment_account_number")
                    or quote["payment_account_number"],
                    _rek_eksplisit(body, "payment_account_holder")
                    or quote["payment_account_holder"],
                    Decimal(str(quote["discount_value"] or 0)) if quote["discount_type"] == "percentage" else Decimal("0"),
                    _doc["doc_discount"],
                )

                # Baris faktur = baris kalkulator (kolom sama dengan create Faktur).
                for line_no, ln in enumerate(_doc["items"], start=1):
                    await conn.execute(
                        """
                        INSERT INTO sales_invoice_items (
                            id, invoice_id, item_id, description,
                            quantity, unit, unit_price, discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount, subtotal, total,
                            line_number, tax_code_id, dpp, dpp_harga_jual
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18
                        )
                    """,
                        uuid_module.uuid4(),
                        invoice_id,
                        uuid_module.UUID(ln["item_id"]) if ln["item_id"] else None,
                        ln["description"],
                        ln["quantity"],
                        ln["unit"],
                        ln["unit_price"],
                        ln["discount_percent"],
                        ln["discount_amount"],
                        "PPN",
                        ln["tax_rate"],
                        ln["tax_amount"],
                        ln["subtotal"],
                        ln["total"],
                        line_no,
                        uuid_module.UUID(ln["tax_id"]) if ln["tax_id"] else None,
                        ln["dpp"],
                        ln["dpp_harga_jual"],
                    )

                # Update quote status
                await conn.execute(
                    """
                    UPDATE quotes SET
                        status = 'converted',
                        converted_to_type = 'invoice',
                        converted_to_id = $3,
                        converted_at = NOW()
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                    invoice_id,
                )

                return QuoteResponse(
                    success=True,
                    message="Quote converted to invoice",
                    data={
                        "quote_id": quote_id,
                        "invoice_id": str(invoice_id),
                        "invoice_number": invoice_number,
                    },
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting quote to invoice: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to convert quote to invoice"
        )


@router.post("/{quote_id}/to-order", response_model=QuoteResponse)
async def convert_to_sales_order(
    request: Request, quote_id: str, body: ConvertToOrderRequest = None
):
    """Convert quote to sales order."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Get quote
                quote = await conn.fetchrow(
                    """
                    SELECT * FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                )

                if not quote:
                    raise HTTPException(status_code=404, detail="Quote not found")

                if quote["status"] not in ("sent", "accepted", "viewed"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot convert quote with status '{quote['status']}'",
                    )

                # Get items
                items_query = "SELECT * FROM quote_items WHERE quote_id = $1"
                if body and body.item_ids:
                    items_query += " AND id = ANY($2)"
                    items = await conn.fetch(
                        items_query,
                        uuid_module.UUID(quote_id),
                        [uuid_module.UUID(id) for id in body.item_ids],
                    )
                else:
                    items = await conn.fetch(items_query, uuid_module.UUID(quote_id))

                if not items:
                    raise HTTPException(status_code=400, detail="No items to convert")

                # Generate SO number
                so_number = await conn.fetchval(
                    "SELECT generate_sales_order_number($1, 'SO')", ctx["tenant_id"]
                )

                # Create sales order
                so_id = uuid_module.uuid4()
                order_date = (
                    body.order_date
                    if body and body.order_date
                    else await tanggal_dokumen(conn, ctx["tenant_id"])
                )

                # 3g: kalkulator bersama. Arti kolom SO: subtotal = SIGMA neto baris (lihat _so_doc).
                _doc = await _quote_converted_doc(conn, ctx["tenant_id"], quote, items)
                # #34: SO ber-PPN untuk tenant non-PKP ditolak di SEMUA jalur pembuat SO (faktur dari
                # penawaran tetap draf dan dijaga saat posting).
                from ..services.pkp_guard import tolak_ppn_bila_non_pkp
                await tolak_ppn_bila_non_pkp(conn, ctx["tenant_id"], _doc["tax_amount"])

                # T199 (2026-09-01): syarat DP terbawa dari Penawaran ke Sales Order.
                # SEBELUMNYA keenam kolom DP quote (dp_percent, dp_amount, terms,
                # payment_bank_name, payment_account_number, payment_account_holder)
                # LENYAP tanpa peringatan saat konversi -- Proforma (Tahap 3) tidak
                # punya sumber untuk tahu berapa yang ditagih.
                # `notes` adalah bagian dari PERBAIKAN YANG SAMA: kolomnya sudah ADA
                # di sales_orders sejak awal, tapi tidak pernah disalin.
                # Pemetaan nama: quotes.terms -> sales_orders.payment_terms (V224).
                # Tetap TIDAK menjurnal: konversi quote->SO nol journal_entries.
                await conn.execute(
                    """
                    INSERT INTO sales_orders (
                        id, tenant_id, order_number, order_date, expected_ship_date,
                        customer_id, customer_name,
                        subtotal, tax_amount, total_amount, discount_amount,
                        status, quote_id, created_by,
                        notes,
                        dp_percent, dp_amount, payment_terms,
                        payment_bank_name, payment_account_number,
                        payment_account_holder
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7,
                        $8, $9, $10, $20,
                        'draft', $11, $12,
                        $13,
                        $14, $15, $16,
                        $17, $18,
                        $19
                    )
                """,
                    so_id,
                    ctx["tenant_id"],
                    so_number,
                    order_date,
                    body.expected_ship_date if body else None,
                    str(quote["customer_id"]),
                    quote["customer_name"],
                    _doc["net_subtotal"],
                    _doc["tax_amount"],
                    _doc["total_amount"],
                    uuid_module.UUID(quote_id),
                    ctx["user_id"],
                    quote["notes"],
                    quote["dp_percent"],
                    quote["dp_amount"],
                    quote["terms"],
                    quote["payment_bank_name"],
                    quote["payment_account_number"],
                    quote["payment_account_holder"],
                    _doc["doc_discount"],
                )

                for idx, ln in enumerate(_doc["items"]):
                    await conn.execute(
                        """
                        INSERT INTO sales_order_items (
                            id, sales_order_id, item_id, description,
                            quantity, unit, unit_price, discount_percent,
                            tax_id, tax_rate, tax_amount, line_total, sort_order, dpp
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
                        )
                    """,
                        uuid_module.uuid4(),
                        so_id,
                        uuid_module.UUID(ln["item_id"]) if ln["item_id"] else None,
                        ln["description"],
                        ln["quantity"],
                        ln["unit"],
                        ln["unit_price"],
                        ln["discount_percent"],
                        uuid_module.UUID(ln["tax_id"]) if ln["tax_id"] else None,
                        ln["tax_rate"],
                        ln["tax_amount"],
                        ln["total"],
                        idx,
                        ln["dpp"],
                    )

                # FIX_P3_BRIDGE 2026-06-16: propagate the new sales_order_id to
                # any customer deposit already taken at the quote stage. WAJIB:
                # a DP taken against the quote must spine-match the invoice that
                # is later created from this SO (sales_order_id linkage).
                # Tenant-scoped; only stamp deposits that aren't already linked.
                propagated = await conn.execute(
                    """
                    UPDATE customer_deposits
                    SET sales_order_id = $1, updated_at = NOW()
                    WHERE quote_id = $2
                      AND tenant_id = $3
                      AND sales_order_id IS NULL
                    """,
                    so_id,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                )
                logger.info(
                    f"Quote->SO convert {quote_id}->{so_id}: deposit propagation {propagated}"
                )

                # Update quote status
                await conn.execute(
                    """
                    UPDATE quotes SET
                        status = 'converted',
                        converted_to_type = 'sales_order',
                        converted_to_id = $3,
                        converted_at = NOW()
                    WHERE id = $1 AND tenant_id = $2
                """,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                    so_id,
                )

                return QuoteResponse(
                    success=True,
                    message="Quote converted to sales order",
                    data={
                        "quote_id": quote_id,
                        "sales_order_id": str(so_id),
                        "order_number": so_number,
                    },
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting quote to sales order: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to convert quote to sales order"
        )


# =============================================================================
# GENERATE PDF
# =============================================================================
from io import BytesIO
from fastapi.responses import StreamingResponse
from ..services.pdf_service import get_pdf_service
import base64
from pathlib import Path as _Path


@router.get("/{quote_id}/pdf")
async def get_quote_pdf(
    request: Request,
    quote_id: str,
    format: Literal["url", "inline"] = Query(
        "inline",
        description="Response format: 'inline' returns PDF bytes, 'url' returns gateway path to the inline PDF (Unit 2)",
    ),
):
    """
    Generate PDF for a quote (penawaran harga).

    Format options:
    - inline (default): Returns PDF bytes directly for browser preview
    - url: Returns gateway path (?format=inline) for download/share; needs Bearer auth, no expiry (Unit 2)
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Fetch quote header
            quote = await conn.fetchrow(
                """
                SELECT * FROM quotes
                WHERE id = $1 AND tenant_id = $2
                """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Quote not found")

            # Fetch items
            items = await conn.fetch(
                """
                SELECT * FROM quote_items
                WHERE quote_id = $1
                ORDER BY sort_order, id
                """,
                uuid_module.UUID(quote_id),
            )

            # Fetch tenant info for PDF header
            tenant_row = await conn.fetchrow(
                'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
                ctx["tenant_id"],
            )
            if tenant_row:
                tenant_info = {
                    "name": tenant_row["display_name"],
                    "address": tenant_row["address"],
                    "phone": tenant_row["phone"],
                    "logo_url": tenant_row["logo_url"],
                }
            else:
                tenant_info = {
                    "name": ctx["tenant_id"],
                    "address": None,
                    "phone": None,
                    "logo_url": None,
                }

            # Resolve logo to base64 data URI for PDF embedding
            _logo_data = None
            _logo_filename = tenant_info.get("logo_url")
            if _logo_filename:
                _logo_path = (
                    _Path(__file__).parent.parent / "static" / "logos" / _logo_filename
                )
                if _logo_path.exists():
                    with open(_logo_path, "rb") as _lf:
                        _logo_b64 = base64.b64encode(_lf.read()).decode()
                    _logo_data = f"data:image/png;base64,{_logo_b64}"
            tenant_info["logo_data"] = _logo_data

            # Build quote data dict for template
            quote_data = {
                "id": str(quote["id"]),
                "quote_number": quote["quote_number"],
                "quote_date": quote["quote_date"].isoformat()
                if quote["quote_date"]
                else None,
                "expiry_date": quote["expiry_date"].isoformat()
                if quote["expiry_date"]
                else None,
                "customer_id": str(quote["customer_id"])
                if quote["customer_id"]
                else None,
                "customer_name": quote["customer_name"],
                "customer_email": quote["customer_email"],
                "reference": quote["reference"],
                "subject": quote["subject"],
                "status": quote["status"],
                "subtotal": quote["subtotal"],
                "discount_type": quote["discount_type"],
                "discount_value": float(quote["discount_value"] or 0),
                "discount_amount": quote["discount_amount"],
                "tax_amount": quote["tax_amount"],
                "total_amount": quote["total_amount"],
                # FIX_P2_QUOTEDP 2026-06-16 — down-payment block (NO-LEDGER, display only)
                "dp_amount": quote["dp_amount"],
                "dp_percent": float(quote["dp_percent"]) if quote["dp_percent"] is not None else None,
                # 3h: Decimal, bukan int() -- sisa dari total bersen tak boleh terpotong.
                "dp_remaining": (
                    quote["total_amount"] - quote["dp_amount"]
                    if quote["dp_amount"] is not None
                    else None
                ),
                "shipping_charges": None,
                "adjustment": None,
                "adjustment_label": None,
                "opening_text": quote["opening_text"],
                "closing_text": quote["closing_text"],
                "notes": quote["notes"],
                "terms": quote["terms"],
                "footer": quote["footer"],
                "payment_bank_name": quote.get("payment_bank_name"),
                "payment_account_number": quote.get("payment_account_number"),
                "payment_account_holder": quote.get("payment_account_holder"),
                "items": [
                    {
                        "id": str(item["id"]),
                        "item_id": str(item["item_id"]) if item["item_id"] else None,
                        "description": item["description"],
                        "quantity": float(item["quantity"]),
                        "unit": item["unit"],
                        "unit_price": item["unit_price"],
                        "discount_percent": float(item["discount_percent"] or 0),
                        "tax_rate": float(item["tax_rate"] or 0),
                        "tax_amount": item["tax_amount"],
                        "line_total": item["line_total"],
                        "group_name": item["group_name"],
                        "sort_order": item["sort_order"],
                    }
                    for item in items
                ],
            }

        # Generate PDF
        pdf_service = get_pdf_service()
        # 3h: dua desimal hanya bila Penawaran ini bersen (lihat filter `rupiah`).
        quote_data["has_cents"] = pdf_service.money_has_cents(
            quote_data["subtotal"], quote_data["discount_amount"], quote_data["tax_amount"],
            quote_data["total_amount"], quote_data["dp_amount"], quote_data["dp_remaining"],
            *[v for it in quote_data["items"] for v in (it["unit_price"], it["tax_amount"], it["line_total"])],
        )
        pdf_bytes = pdf_service.generate_quote_pdf(quote_data, tenant_info)

        # Generate filename
        quote_num = quote["quote_number"] or str(quote_id)[:8]
        from ..utils.content_disposition import pdf_content_disposition, sanitize_filename
        filename = sanitize_filename(quote_num) + ".pdf"

        if format == "inline":
            return StreamingResponse(
                BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": pdf_content_disposition(quote_num),
                    "Cache-Control": "no-store",  # FIX_LOGO_CACHEBUST 2026-06-16
                },
            )

        # Unit 2: path relatif gateway, BUKAN presign MinIO (mati sejak port
        # publik ditutup 23 Sep) dan tanpa salinan PDF di bucket.
        from ..utils.pdf_url import respons_pdf_url
        return respons_pdf_url("quotes", quote_id, filename)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating PDF for quote {quote_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate PDF")
