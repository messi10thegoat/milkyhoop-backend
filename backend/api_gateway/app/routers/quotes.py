"""
Quotes (Penawaran) Router
Pre-sale quotes before conversion to Invoice or Sales Order.
NO journal entries - accounting impact happens on conversion.
"""
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi import Response as _Response
from typing import Optional, Literal
from datetime import date, datetime

from ..utils.tanggal_tenant import tanggal_dokumen
from ..services.pihak_helpers import pelanggan_kanonik_tenant  # pelanggan WAJIB satu tenant (30 Sep)
from ..services import faktur_cetak as _fc_snap
from ..services import teks_galat as tg
from ..services.termin_bayar import tentukan_jatuh_tempo
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

            # U1e: ?search= juga mencocokkan nomor/kode/judul SO HASIL konversi (helper sama dgn 6 modul lain)
            from ..services.kode_order import sql_cari_so_induk as _cari_so

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(quote_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR subject ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = quotes.tenant_id AND c.search_text ILIKE ${param_idx}) OR {_cari_so('quote', 'quotes', '$1', f'${param_idx}')})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(quote_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR subject ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = quotes.tenant_id AND c.search_text ILIKE ${param_idx}) OR {_cari_so('quote', 'quotes', '$1', f'${param_idx}')})"
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
            from ..services.kode_order import so_hasil_penawaran as _so_hasil
            so_hasil = await _so_hasil(conn, ctx["tenant_id"], [r["id"] for r in rows])  # batch, nol N+1

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
                        **so_hasil[str(row["id"])],
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


@router.get("/defaults")
async def get_quote_defaults(request: Request, customer_id: Optional[str] = None):
    """Q6 (3 Okt 2026): default form Penawaran CW -- bentuk SAMA dengan /sales-orders/defaults (dp_percent,
    receiving_account; null = tak ada default) + opening_text/closing_text {value, source} + validity_days
    {value, source, expiry_date}. Nol tulis."""
    from ..services.default_dokumen import default_penawaran
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await default_penawaran(conn, ctx["tenant_id"], await tanggal_dokumen(conn, ctx["tenant_id"]),
                                       customer_id)
    return {"success": True, "data": data}


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
                raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

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
            # Q5 (3 Okt): kontak + nama usaha untuk pesan Kirim -- sumber & aturan SAMA dgn bundel SO /documents
            _telp = await conn.fetchval(
                """SELECT COALESCE(mobile_phone, phone, telepon) FROM customers WHERE id = $1 AND tenant_id = $2""",
                quote["customer_id"], ctx["tenant_id"]) if quote["customer_id"] else None
            _usaha = (await conn.fetchval('SELECT display_name FROM "Tenant" WHERE id = $1', ctx["tenant_id"])) \
                or ctx["tenant_id"]
            from ..services.kode_order import so_hasil_penawaran as _so_hasil
            _so = (await _so_hasil(conn, ctx["tenant_id"], [quote["id"]]))[str(quote["id"])]

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
                    # 4 medan teks lama sudah dioper eksplisit di atas -> jangan dobel (TypeError, ditangkap nyata kaos)
                    **{k: v for k, v in _surat_keluaran(quote).items() if k not in _TEKS_LAMA},
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
                    customer_phone=_telp or None,
                    business_name=_usaha,
                    **_so,
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


_TEKS_LAMA = ("opening_text", "closing_text", "notes", "terms")


def _surat_keluaran(quote) -> dict:
    """6 Okt 2026: medan surat + `<medan>_source` + terbilang total untuk GET detail."""
    from ..services.penawaran_surat import keluaran
    from ..utils.terbilang import terbilang
    out = keluaran(quote)
    out["total_in_words"] = terbilang(int(quote["total_amount"] or 0))  # sudah berakhiran "Rupiah"
    return out


@router.post("", response_model=QuoteResponse)
async def create_quote(request: Request, body: CreateQuoteRequest, response: _Response = None):
    """Create a new quote (draft status).
    X-Idempotency-Key opsional (4 Okt 2026, MASTER GO): klik ganda = 1 draf; badan beda = 409 (services/idem_buat)."""
    try:
        ctx = get_user_context(request)
        from ..services import idem_buat
        _kunci = idem_buat.kunci_dari(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                _kp, _sd, _lama = await idem_buat.mulai(conn, ctx, _kunci, "QUOTE_CREATE", body, "quotes", "quote",
                                                        response)
                if _lama is not None:
                    return _lama
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

                # Pelanggan WAJIB milik tenant ini (30 Sep 2026; dulu hanya format UUID).
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
                    await _fc_snap.pemilik_cetak(conn, ctx["tenant_id"], body.payment_bank_name, body.payment_account_number, body.payment_account_holder),  # 28 Sep: snapshot pemilik rekening, bukan nama akun
                    ctx["user_id"],
                    # FIX_P2_QUOTEDP 2026-06-16 (NUMERIC columns expect Decimal/None)
                    Decimal(str(dp["dp_amount"])) if dp["dp_amount"] is not None else None,
                    Decimal(str(dp["dp_percent"])) if dp["dp_percent"] is not None else None,
                )

                # 6 Okt 2026: isian SURAT (up., pembuka/penutup, catatan khusus, S&K, penanda tangan) -- SNAPSHOT dari
                # bawaan (default_penawaran) untuk medan yang tak dikirim; sumber per medan (services/penawaran_surat).
                from ..services import penawaran_surat as _surat
                from ..services.default_dokumen import default_penawaran as _bawaan_pnw
                await _surat.terapkan(conn, ctx["tenant_id"], quote_id, body, await _bawaan_pnw(
                    conn, ctx["tenant_id"], await tanggal_dokumen(conn, ctx["tenant_id"]), body.customer_id), buat=True)

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

                return await idem_buat.simpan(conn, ctx, _kp, _sd, "QUOTE_CREATE", QuoteResponse(
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
                ), quote_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create quote")


@router.post("/calculate")
async def calculate_quote(request: Request, body: CreateQuoteRequest):
    """Pratinjau Penawaran TANPA menyimpan: _quote_calc + resolve_dp + penjaga PPN non-PKP YANG SAMA dengan create
    (pola SO /calculate). KONTRAK: mengembalikan PERSIS yang akan disimpan create. Nol tulis."""
    ctx = get_user_context(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        _doc = await _quote_calc(conn, ctx["tenant_id"], [item.model_dump() for item in body.items],
                                 body.discount_type, body.discount_value)
        from ..services.pkp_guard import tolak_ppn_bila_non_pkp as _non_pkp
        await _non_pkp(conn, ctx["tenant_id"], _doc["tax_amount"])
    dp = resolve_dp(_doc["total_amount"], body.dp_amount, body.dp_percent)
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    return {"success": True, "data": {
        "subtotal": f(_doc["net_subtotal"]), "discount_amount": f(_doc["doc_discount"]),
        "tax_amount": f(_doc["tax_amount"]), "total_amount": f(_doc["total_amount"]),
        "dp_amount": f(dp["dp_amount"]), "dp_percent": f(dp["dp_percent"]),
        "items": [{"line_number": i + 1, "description": ln.get("description"), "quantity": f(ln.get("quantity")),
                   "unit_price": f(ln.get("unit_price")), "discount_percent": f(ln.get("discount_percent") or 0),
                   "tax_rate": f(ln.get("tax_rate") or 0), "tax_amount": f(ln["tax_amount"]),
                   "line_total": f(ln["line_total"]), "dpp": f(ln.get("dpp"))}
                  for i, ln in enumerate(_doc["items"])],
    }}


@router.get("/{quote_id}/history")
async def get_quote_history(request: Request, quote_id: str, limit: int = Query(200, ge=1, le=500),
                            format: Optional[str] = Query(None, pattern="^events$")):
    """Riwayat penawaran bentuk SAMA dengan GET /sales-orders/{id}/history (services/so_riwayat.riwayat_penawaran).
    Pesanan hasil konversi disaring izin baca -> omitted[]."""
    ctx = get_user_context(request)
    try:
        qid = uuid_module.UUID(quote_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")
    from ..services.dashboard_izin import boleh_baca
    from ..services.so_riwayat import riwayat_penawaran
    pool = await get_pool()
    async with pool.acquire() as conn:
        data = await riwayat_penawaran(conn, ctx["tenant_id"], qid, lambda m: boleh_baca(request, m), limit)
    if data is None:
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")
    return {"success": True, "data": data}


async def _perpanjang_penawaran(conn, ctx: dict, quote_id: str, body, quote) -> QuoteResponse:
    """Perpanjang (2 Okt 2026, MASTER): HANYA expiry_date pada penawaran sent/viewed. Fungsi terpisah dari
    update_quote supaya penjaga pelanggan-satu-tenant tetap mendahului UPDATE quotes di jalur ubah penuh."""
    quote = await conn.fetchrow(
        "SELECT id, quote_number, quote_date, expiry_date FROM quotes WHERE id = $1 AND tenant_id = $2",
        quote["id"], ctx["tenant_id"])
    baru = body.expiry_date
    if baru is None:
        raise HTTPException(status_code=422, detail="Tanggal berlaku wajib diisi.")
    if quote["quote_date"] and baru < quote["quote_date"]:
        raise HTTPException(status_code=422, detail="Tanggal berlaku tidak boleh sebelum tanggal penawaran.")
    from ..utils.tanggal_tenant import tanggal_dokumen as _hari_ini
    if baru < await _hari_ini(conn, ctx["tenant_id"]):
        raise HTTPException(status_code=422, detail="Tanggal berlaku baru tidak boleh di masa lalu.")
    await conn.execute(
        "UPDATE quotes SET expiry_date = $3, updated_at = NOW() WHERE id = $1 AND tenant_id = $2",
        quote["id"], ctx["tenant_id"], baru)
    await catat_riwayat(
        conn, ctx["tenant_id"], "quotes", quote["id"], quote["quote_number"], "QUOTE_EXPIRY_EXTENDED",
        ctx.get("user_id"), f"Masa berlaku penawaran {quote['quote_number']} diperpanjang sampai "
        f"{baru.isoformat()}", {"old": quote["expiry_date"].isoformat() if quote["expiry_date"] else None,
                                "new": baru.isoformat()}, source="api:quotes")
    return QuoteResponse(success=True, message="Quote validity extended",
                         data={"quote_id": quote_id, "expiry_date": baru.isoformat()})


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
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}"
                )
                quote = await conn.fetchrow(
                    """
                    SELECT id, status FROM quotes
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                """,
                    uuid_module.UUID(quote_id),
                    ctx["tenant_id"],
                )

                if not quote:
                    raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

                # 2 Okt 2026 (MASTER, praktik umum): "Perpanjang" = HANYA expiry_date, pada penawaran terkirim/
                # dilihat. Field lain tetap hanya draf (penjaga di bawah).
                if body.model_fields_set == {"expiry_date"} and quote["status"] in ("sent", "viewed"):
                    return await _perpanjang_penawaran(conn, ctx, quote_id, body, quote)

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
                        status_code=400, detail="Hanya penawaran berstatus Draf yang bisa diubah."
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
                from ..services import penawaran_surat as _surat
                update_data = body.model_dump(
                    exclude_unset=True, exclude={"items", "dp_amount", "dp_percent", "signer_user_id",
                                                 *_surat.MEDAN, *(m + "_source" for m in _surat.MEDAN)}
                )

                for field, value in update_data.items():
                    if field == "customer_id" and value is not None:
                        _c = await pelanggan_kanonik_tenant(conn, ctx["tenant_id"], value)  # satu tenant
                        value = uuid_module.UUID(_c) if _c else None
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

                # 6 Okt 2026: isian surat (snapshot + sumber); bawaan dibaca HANYA bila ada `_source:'default'` tanpa nilai
                _perlu = any(getattr(body, m + "_source") == "default" and m not in body.model_fields_set for m in _surat.MEDAN)
                _bwn = {}
                if _perlu:
                    from ..services.default_dokumen import default_penawaran as _bawaan_pnw
                    _cid = await conn.fetchval("SELECT customer_id::text FROM quotes WHERE id = $1 AND tenant_id = $2",
                                               uuid_module.UUID(quote_id), ctx["tenant_id"])
                    _bwn = await _bawaan_pnw(conn, ctx["tenant_id"], await tanggal_dokumen(conn, ctx["tenant_id"]), _cid)
                _ubah_surat = await _surat.terapkan(conn, ctx["tenant_id"], uuid_module.UUID(quote_id), body, _bwn, buat=False)

                _ubah = sorted(set(update_data) | set(_ubah_surat) | ({"items"} if body.items is not None else set())
                               | {f for f in ("dp_amount", "dp_percent") if getattr(body, f) is not None})
                if _ubah:
                    # 3 Okt 2026 (MASTER): riwayat Penawaran 'diubah' -- pola SO (SALES_ORDER_UPDATED): tak punya kolom
                    # aktor -> audit_logs di transaksi YANG SAMA (gagal mencatat = suntingan batal). 'Dibuat' tetap
                    # dari kolom created_at/created_by (beraktor) seperti SO -- tak perlu QUOTE_CREATED.
                    _nomor = await conn.fetchval("SELECT quote_number FROM quotes WHERE id = $1 AND tenant_id = $2",
                                                 uuid_module.UUID(quote_id), ctx["tenant_id"])
                    await catat_riwayat(
                        conn, ctx["tenant_id"], "quotes", uuid_module.UUID(quote_id), _nomor, "QUOTE_UPDATED",
                        ctx.get("user_id"), "Penawaran diubah (" + ", ".join(_ubah) + ")", {"fields": _ubah},
                        source="api:quotes.update",
                    )

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


async def alasan_tak_bisa_hapus(conn, tenant_id: str, quote) -> Optional[str]:
    """Pola Xero/NetSuite (pemilik 3 Okt, "ikuti pola SaaS mapan"): yang boleh DIHAPUS hanya draf yang belum pernah
    keluar -- sent_at kosong, 0 tautan dokumen, tak dirujuk pesanan. Selain itu jejaknya harus tinggal: Batalkan.
    -> None (boleh) atau kalimat alasan."""
    n = quote["quote_number"]
    if quote["status"] != "draft":
        return f"Penawaran {n} berstatus {tg.status_id('quote', quote['status'])}; hanya draf yang bisa dihapus. Batalkan saja."
    if quote["sent_at"] is not None:
        return f"Penawaran {n} sudah pernah ditandai terkirim. Batalkan saja."
    if await conn.fetchval("""SELECT 1 FROM document_shares WHERE tenant_id = $1 AND kind = 'quotation' AND doc_id = $2
                              LIMIT 1""", tenant_id, quote["id"]):
        return f"Penawaran {n} sudah pernah dibagikan ke pelanggan. Batalkan saja."
    if await conn.fetchval("SELECT 1 FROM sales_orders WHERE tenant_id = $1 AND quote_id = $2 LIMIT 1",
                           tenant_id, quote["id"]):
        return f"Penawaran {n} sudah dijadikan pesanan. Batalkan saja."
    return None


async def rencana_hapus_penawaran(conn, ctx: dict, quote_id) -> list:
    """Penentu BACA hapus penawaran (U1b F3, pratinjau massal): tak ada tulisan. Aturan = alasan_tak_bisa_hapus (pola Xero:
    hanya draf yang belum pernah keluar). -> [] bila boleh, selain itu blok {code,status,message}."""
    quote = await conn.fetchrow(
        "SELECT id, status, quote_number, sent_at FROM quotes WHERE id = $1 AND tenant_id = $2",
        uuid_module.UUID(str(quote_id)), ctx["tenant_id"])
    if not quote:
        return [{"code": "QUOTE_TAK_ADA", "status": 404, "message": "Penawaran tidak ditemukan."}]
    alasan = await alasan_tak_bisa_hapus(conn, ctx["tenant_id"], quote)
    return [{"code": "QUOTE_NOT_DELETABLE", "status": 409, "message": alasan}] if alasan else []


async def hapus_penawaran_core(conn, ctx: dict, quote_id) -> dict:
    """Inti DELETE /quotes/{id}, di transaksi PEMANGGIL: rute tunggal DAN hapus massal (U1b F3). Isi = isi rute lama tanpa
    perubahan (hanya dipindah dari handler). -> {quote_number}."""
    quote_id = str(quote_id)
    # V230: siapa yang menghapus. Trigger `trg_log_deletion` membaca `app.user_id`, dan GUC itu HANYA hidup di
    # dalam transaksi -- set_config + DELETE di SATU transaksi. 3 Okt: kunci QUOTE + FOR UPDATE di transaksi
    # yang sama (dulu status dibaca tanpa kunci -> bisa berpacu dengan kirim/bagikan bersamaan).
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}"
    )
    quote = await conn.fetchrow(
        """
        SELECT id, status, quote_number, sent_at FROM quotes
        WHERE id = $1 AND tenant_id = $2
        FOR UPDATE
    """,
        uuid_module.UUID(quote_id),
        ctx["tenant_id"],
    )

    if not quote:
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

    alasan = await alasan_tak_bisa_hapus(conn, ctx["tenant_id"], quote)
    if alasan:
        raise HTTPException(status_code=409, detail={"code": "QUOTE_NOT_DELETABLE", "message": alasan})

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
    return {"quote_number": quote["quote_number"]}


@router.delete("/{quote_id}", response_model=QuoteResponse)
async def delete_quote(request: Request, quote_id: str):
    """Hapus penawaran: HANYA draf yang belum pernah keluar (alasan_tak_bisa_hapus); selain itu 409
    QUOTE_NOT_DELETABLE. Hapus keras; jejak = trigger trg_log_deletion (audit DOCUMENT_DELETED beraktor + nomor).
    Isi + aturan: hapus_penawaran_core (juga dipakai hapus massal)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                data = await hapus_penawaran_core(conn, ctx, quote_id)

            return QuoteResponse(
                success=True,
                message="Quote deleted successfully",
                data=data,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete quote")


# ============================================================================
# WORKFLOW ENDPOINTS
# ============================================================================


async def kirim_penawaran_core(conn, ctx: dict, quote_id, send_email: bool = False) -> dict:
    """Inti POST /quotes/{id}/send ("tandai terkirim"), di transaksi PEMANGGIL: rute tunggal DAN kirim massal (U1b F3). Isi = isi rute
    lama tanpa perubahan (hanya dipindah dari handler; `body.send_email` -> `send_email`). -> {quote_number, status}."""
    quote_id = str(quote_id)
    # 2 Okt 2026: kunci QUOTE + FOR UPDATE -- dua klik/tab tak lagi saling menimpa status
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}")
    # Check quote
    quote = await conn.fetchrow(
        """
        SELECT id, status, quote_number FROM quotes
        WHERE id = $1 AND tenant_id = $2
        FOR UPDATE
    """,
        uuid_module.UUID(quote_id),
        ctx["tenant_id"],
    )

    if not quote:
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

    if quote["status"] not in ("draft", "sent"):
        raise HTTPException(
            status_code=400,
            detail=tg.tak_bisa_status("quote", quote["status"], "dikirim", quote["quote_number"]),
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
    if send_email:
        raise HTTPException(
            status_code=422,
            detail=(
                "Pengiriman penawaran lewat surel belum tersedia. "
                "Penawaran TIDAK ditandai terkirim. Kirim tanpa surel "
                "(send_email=false) untuk menandainya terkirim, lalu "
                "bagikan PDF-nya lewat saluran lain."
            ),
        )

    # 3 Okt (Q5): penanda bersama dengan POST /documents/quotation/{id}/share
    from ..services.penawaran_bagikan import tandai_terkirim
    await tandai_terkirim(conn, ctx["tenant_id"], quote["id"], quote["quote_number"], ctx.get("user_id"),
                          "api:quotes")
    return {"quote_number": quote["quote_number"], "status": "sent"}


@router.post("/{quote_id}/send", response_model=QuoteResponse)
async def send_quote(request: Request, quote_id: str, body: SendQuoteRequest = None):
    """Mark quote as sent. Isi + aturan: kirim_penawaran_core (juga dipakai kirim massal)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn, conn.transaction():
            data = await kirim_penawaran_core(conn, ctx, quote_id, bool(body and body.send_email))
            return QuoteResponse(
                success=True,
                message="Quote sent successfully",
                data=data,
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

        async with pool.acquire() as conn, conn.transaction():
            # 2 Okt 2026: kunci QUOTE + FOR UPDATE -- dua klik/tab tak lagi saling menimpa status
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}")
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
                FOR UPDATE
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

            if quote["status"] not in ("sent", "viewed"):
                raise HTTPException(
                    status_code=400,
                    detail=tg.tak_bisa_status("quote", quote["status"], "diterima", quote["quote_number"]),
                )

            await conn.execute(
                """
                UPDATE quotes SET status = 'accepted', accepted_at = NOW()
                WHERE id = $1 AND tenant_id = $2
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            await catat_riwayat(
                conn, ctx["tenant_id"], "quotes", quote["id"], quote["quote_number"], "QUOTE_ACCEPTED", ctx.get("user_id"),
                f"Penawaran {quote['quote_number']} disetujui pelanggan", source="api:quotes",
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
from ..services.so_riwayat import catat_riwayat


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

        async with pool.acquire() as conn, conn.transaction():
            # 2 Okt 2026: kunci QUOTE + FOR UPDATE -- dua klik/tab tak lagi saling menimpa status
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}")
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number FROM quotes
                WHERE id = $1 AND tenant_id = $2
                FOR UPDATE
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

            if quote["status"] not in ("sent", "viewed"):
                raise HTTPException(
                    status_code=400,
                    detail=tg.tak_bisa_status("quote", quote["status"], "ditolak", quote["quote_number"]),
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

            await catat_riwayat(
                conn, ctx["tenant_id"], "quotes", quote["id"], quote["quote_number"], "QUOTE_DECLINED", ctx.get("user_id"),
                f"Penawaran {quote['quote_number']} ditolak" + (f": {body.reason}" if body.reason else ""), source="api:quotes",
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

        async with pool.acquire() as conn, conn.transaction():
            # 2 Okt 2026: kunci QUOTE + FOR UPDATE -- dua klik/tab tak lagi saling menimpa status
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}")
            quote = await conn.fetchrow(
                """
                SELECT id, status, quote_number, total_amount, customer_name
                FROM quotes
                WHERE id = $1 AND tenant_id = $2
                FOR UPDATE
            """,
                uuid_module.UUID(quote_id),
                ctx["tenant_id"],
            )

            if not quote:
                raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

            if quote["status"] not in ("draft", "sent"):
                raise HTTPException(
                    status_code=400,
                    detail=tg.tak_bisa_status("quote", quote["status"], "dibatalkan", quote["quote_number"])
                + " Hanya penawaran berstatus Draf atau Terkirim yang bisa dibatalkan.",
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

            await catat_riwayat(
                conn, ctx["tenant_id"], "quotes", quote["id"], quote["quote_number"], "QUOTE_VOIDED", ctx.get("user_id"),
                f"Penawaran {quote['quote_number']} dibatalkan" + (f": {body.reason}" if body and body.reason else ""), source="api:quotes",
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
                    raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

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
                # 6 Okt 2026: salin isian surat + sumbernya (snapshot dokumen asal, bukan bawaan saat ini)
                await conn.execute(
                    """UPDATE quotes q SET attention_name = s.attention_name, attention_title = s.attention_title,
                              signer_user_id = s.signer_user_id, signer_name = s.signer_name, signer_title = s.signer_title,
                              signer_phone = s.signer_phone, signer_email = s.signer_email, field_sources = s.field_sources
                       FROM quotes s WHERE q.id = $1 AND q.tenant_id = $3 AND s.id = $2 AND s.tenant_id = $3""",
                    new_id, quote["id"], ctx["tenant_id"])

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
                    raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

                if quote["status"] not in ("sent", "accepted", "viewed"):
                    raise HTTPException(
                        status_code=400,
                        detail=tg.tak_bisa_status("quote", quote["status"], "dijadikan pesanan", quote["quote_number"]),
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
                    raise HTTPException(status_code=400, detail="Penawaran tidak punya barang untuk dijadikan pesanan.")

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
                # F3 (26 Sep 2026, putusan pemilik via MASTER): SATU aturan termin untuk semua faktur — isian
                # menang; "NET <n>" di syarat penawaran; termin pelanggan; selain itu tanggal faktur. Dulu +30 hardcode.
                due_date, due_date_source = await tentukan_jatuh_tempo(
                    conn, ctx["tenant_id"], invoice_date, body.due_date if body else None,
                    quote["terms"], quote["customer_id"],
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
                        "due_date": due_date.isoformat(),
                        "due_date_source": due_date_source,
                    },
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting quote to invoice: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to convert quote to invoice"
        )


async def _konversi_penawaran(conn, ctx: dict, quote_id: str, body) -> QuoteResponse:
    """SATU jalan konversi Penawaran -> SO (to-order DAN pratinjaunya). Pemanggil membuka transaksi.

    2 Okt 2026 (MASTER, bug nyata): dulu TANPA kunci -- klik ganda = dua SO dari satu penawaran (cek status di luar
    kunci, keduanya lolos). Kini kunci QUOTE:{tenant}:{id} + FOR UPDATE; satu penawaran = paling banyak SATU SO,
    jadi permintaan ulang (klik ganda / coba-ulang jaringan) MENGEMBALIKAN SO yang sama (idempoten alami), bukan
    galat dan bukan SO kedua."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))", f"QUOTE:{ctx['tenant_id']}:{quote_id}"
    )
    # Get quote
    quote = await conn.fetchrow(
        """
        SELECT * FROM quotes
        WHERE id = $1 AND tenant_id = $2
        FOR UPDATE
    """,
        uuid_module.UUID(quote_id),
        ctx["tenant_id"],
    )

    if not quote:
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

    if (quote["status"] == "converted" and quote.get("converted_to_type") == "sales_order"
            and quote.get("converted_to_id")):
        so_lama = await conn.fetchrow(
            "SELECT id, order_number FROM sales_orders WHERE id = $1 AND tenant_id = $2",
            quote["converted_to_id"], ctx["tenant_id"],
        )
        if so_lama:
            return QuoteResponse(
                success=True,
                message="Quote already converted to sales order",
                data={"quote_id": quote_id, "sales_order_id": str(so_lama["id"]),
                      "order_number": so_lama["order_number"], "already_converted": True},
            )

    if quote["status"] not in ("sent", "accepted", "viewed"):
        raise HTTPException(
            status_code=400,
            detail=tg.tak_bisa_status("quote", quote["status"], "dijadikan pesanan", quote["quote_number"]),
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
        raise HTTPException(status_code=400, detail="Penawaran tidak punya barang untuk dijadikan pesanan.")

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

    await catat_riwayat(
        conn, ctx["tenant_id"], "quotes", quote["id"], quote.get("quote_number"), "QUOTE_CONVERTED",
        ctx.get("user_id"), f"Penawaran {quote.get('quote_number') or ''} dijadikan pesanan {so_number}",
        {"sales_order_id": str(so_id), "order_number": so_number}, source="api:quotes",
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


@router.post("/{quote_id}/to-order", response_model=QuoteResponse)
async def convert_to_sales_order(
    request: Request, quote_id: str, body: ConvertToOrderRequest = None
):
    """Convert quote to sales order (lihat _konversi_penawaran)."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                return await _konversi_penawaran(conn, ctx, quote_id, body)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error converting quote to sales order: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to convert quote to sales order"
        )


class _Batalkan(Exception):
    """Pembatal transaksi pratinjau (membawa hasilnya keluar)."""

    def __init__(self, isi):
        self.isi = isi


@router.post("/{quote_id}/to-order/preview")
async def preview_convert_to_sales_order(
    request: Request, quote_id: str, body: ConvertToOrderRequest = None
):
    """Pratinjau to-order: menjalankan _konversi_penawaran YANG SAMA di dalam transaksi lalu MEMBATALKANNYA -> nol
    tulis (nomor SO dari tabel penghitung ikut kembali), angka PERSIS yang akan disimpan. `payload` = badan POST
    to-order apa adanya. Penawaran yang sudah jadi pesanan -> already_converted + SO-nya."""
    ctx = get_user_context(request)
    pool = await get_pool()
    payload = body.model_dump(mode="json", exclude_none=True) if body else {}
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                r = await _konversi_penawaran(conn, ctx, quote_id, body)
                so_id = uuid_module.UUID(r.data["sales_order_id"])
                so = await conn.fetchrow(
                    """SELECT order_number, order_date, expected_ship_date, customer_id, customer_name, subtotal,
                              discount_amount, tax_amount, total_amount, notes, dp_percent, dp_amount, payment_terms,
                              payment_bank_name, payment_account_number, payment_account_holder
                       FROM sales_orders WHERE id = $1 AND tenant_id = $2""", so_id, ctx["tenant_id"])
                baris = await conn.fetch(
                    """SELECT description, quantity, unit, unit_price, discount_percent, tax_rate, tax_amount,
                              line_total, dpp FROM sales_order_items WHERE sales_order_id = $1 ORDER BY sort_order""",
                    so_id)
                dep = await conn.fetchval(
                    """SELECT count(*) FROM customer_deposits WHERE tenant_id = $1 AND quote_id = $2
                         AND status <> 'void'""", ctx["tenant_id"], uuid_module.UUID(quote_id))
                raise _Batalkan((r, so, baris, dep))
    except _Batalkan as b:
        r, so, baris, dep = b.isi
    f = lambda v: float(v) if v is not None else None  # noqa: E731
    if r.data.get("already_converted"):
        return {"success": True, "data": {"already_converted": True, "sales_order_id": r.data["sales_order_id"],
                                          "order_number": r.data["order_number"], "payload": payload}}
    return {"success": True, "data": {
        "already_converted": False,
        "payload": payload,
        "sales_order": {
            "order_number_preview": so["order_number"],
            "order_date": so["order_date"].isoformat() if so["order_date"] else None,
            "expected_ship_date": so["expected_ship_date"].isoformat() if so["expected_ship_date"] else None,
            "customer_id": str(so["customer_id"]) if so["customer_id"] else None,
            "customer_name": so["customer_name"],
            "subtotal": f(so["subtotal"]), "discount_amount": f(so["discount_amount"]),
            "tax_amount": f(so["tax_amount"]), "total_amount": f(so["total_amount"]),
            "notes": so["notes"],
            "dp_percent": f(so["dp_percent"]), "dp_amount": f(so["dp_amount"]), "payment_terms": so["payment_terms"],
            "payment_bank_name": so["payment_bank_name"], "payment_account_number": so["payment_account_number"],
            "payment_account_holder": so["payment_account_holder"],
            "items": [{"description": x["description"], "quantity": f(x["quantity"]), "unit": x["unit"],
                       "unit_price": f(x["unit_price"]), "discount_percent": f(x["discount_percent"] or 0),
                       "tax_rate": f(x["tax_rate"] or 0), "tax_amount": f(x["tax_amount"]),
                       "line_total": f(x["line_total"]), "dpp": f(x["dpp"])} for x in baris],
        },
        "deposits_to_link": int(dep or 0),
    }}

# =============================================================================
# GENERATE PDF
# =============================================================================
from io import BytesIO
from fastapi.responses import StreamingResponse
from ..services.pdf_service import get_pdf_service
import base64
from pathlib import Path as _Path


async def muat_pdf_penawaran(conn, ctx, quote_id: str) -> dict:
    """Konteks PDF penawaran (P3 SO-dokumen: SATU sumber). Dipindah VERBATIM dari get_quote_pdf
    (+ has_cents). -> {quote_data, tenant_info, quote}."""
    pdf_service = get_pdf_service()
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
        raise HTTPException(status_code=404, detail="Penawaran tidak ditemukan.")

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
    from ..services import faktur_cetak as _fc
    _rek_cetak = await _fc.muat_rekening(conn, ctx["tenant_id"])
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
        # 6 Okt 2026: surat Penawaran -- up., penanda tangan/kontak (SNAPSHOT dokumen), terbilang total
        **_surat_keluaran(quote),
        "payment_bank_name": quote.get("payment_bank_name"),
        "payment_account_number": quote.get("payment_account_number"),
        "payment_account_holder": quote.get("payment_account_holder"),
        "rekening_pemilik_cetak": _fc.pemilik_dari(
            _rek_cetak, quote.get("payment_bank_name"), quote.get("payment_account_number"),
            quote.get("payment_account_holder")),
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
    # 3h: dua desimal hanya bila Penawaran ini bersen (lihat filter `rupiah`).
    quote_data["has_cents"] = pdf_service.money_has_cents(
        quote_data["subtotal"], quote_data["discount_amount"], quote_data["tax_amount"],
        quote_data["total_amount"], quote_data["dp_amount"], quote_data["dp_remaining"],
        *[v for it in quote_data["items"] for v in (it["unit_price"], it["tax_amount"], it["line_total"])],
    )
    return {"quote_data": quote_data, "tenant_info": tenant_info, "quote": quote}


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
            _m = await muat_pdf_penawaran(conn, ctx, quote_id)
        quote_data, tenant_info, quote = _m["quote_data"], _m["tenant_info"], _m["quote"]

        # Generate PDF
        pdf_service = get_pdf_service()
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
