"""
Credit Notes Router - Sales Returns and AR Adjustments

Endpoints for managing credit notes (nota kredit).
Credit notes reduce Accounts Receivable and can be applied to invoices or refunded.

Flow:
1. Create draft credit note
2. Post to accounting (creates AR reduction journal)
3. Apply to invoice(s) OR issue refund
4. Void if needed (only if unapplied)

Endpoints:
- GET    /credit-notes              - List credit notes
- GET    /credit-notes/summary      - Summary statistics
- GET    /credit-notes/{id}         - Get credit note detail
- POST   /credit-notes              - Create draft credit note
- PATCH  /credit-notes/{id}         - Update draft credit note
- DELETE /credit-notes/{id}         - Delete draft credit note
- POST   /credit-notes/{id}/post    - Post to accounting
- POST   /credit-notes/{id}/apply   - Apply to invoice(s)
- POST   /credit-notes/{id}/refund  - Issue cash refund
- POST   /credit-notes/{id}/void    - Void credit note
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional, Literal
from uuid import UUID
from ..utils.tanggal_tenant import tanggal_dokumen
from ..services.pihak_helpers import (
    pelanggan_kanonik_tenant,
    faktur_tenant_untuk_pelanggan,
    pastikan_cn_muat_faktur,
    segarkan_cache_piutang_faktur,
    rupiah,
)
import logging
import asyncpg
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from ..schemas.credit_notes import (
    CreateCreditNoteRequest,
    UpdateCreditNoteRequest,
    ApplyCreditNoteRequest,
    UnapplyCreditNoteRequest,
    RefundCreditNoteRequest,
    VoidCreditNoteRequest,
    CreditNoteResponse,
    CreditNoteDetailResponse,
    CreditNoteListResponse,
    CreditNoteSummaryResponse,
)
from ..services.resolve_account import resolve_account_id
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
    resolve_account_id_by_role_if_pkp,
)
from ..services.role_precondition import assert_required_roles_for_path
from ..services import cn_tertunda

logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool

# Account codes (resolved dynamically via Law 27)
AR_ACCOUNT_CODE = "1-10400"  # Piutang Usaha
SALES_RETURN_ACCOUNT_CODE = "4-10300"  # Retur Penjualan
# Fase D2-wrap C4: VAT keluaran (PPN Output) di credit note (retur penjualan)
# di-resolve via role VAT_OUTPUT dengan PKP guard. Const legacy
# TAX_PAYABLE_ACCOUNT_CODE = "2-10300" dihapus karena 2-10300 sekarang generic
# placeholder pasca D1 V155 repoint (VAT_OUTPUT pindah ke 2-10600 dedicated).

# Required role mappings for credit_notes posting path.
# VAT_OUTPUT is PKP-gated (tenant non-PKP tidak bisa post PPN > 0).
CREDIT_NOTES_REQUIRED_ROLES = [
    AccountRole.VAT_OUTPUT,
]

# One-time precondition check flag.
_precondition_checked_tenants: set = set()


async def _ensure_role_preconditions(pool, tenant_id=None):
    """Run role-mapping precondition for credit_notes.

    Scopes the audit to the ACTING tenant when tenant_id is supplied (cached
    per-tenant); tenant_id=None preserves the legacy all-tenants behavior.
    Fails loud (PreconditionFailedError) if the audited tenant lacks any
    required role mapping.
    """
    if tenant_id is None:
        await assert_required_roles_for_path(
            pool, "credit_notes", CREDIT_NOTES_REQUIRED_ROLES
        )
        return
    if tenant_id in _precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "credit_notes", CREDIT_NOTES_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _precondition_checked_tenants.add(tenant_id)


async def get_pool() -> asyncpg.Pool:
    """Get singleton connection pool (Law 32)."""
    from ..services.db_pool import get_db_pool

    return await get_db_pool()


def get_user_context(request: Request) -> dict:
    """Extract and validate user context from request."""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = request.state.user
    tenant_id = user.get("tenant_id")
    user_id = user.get("user_id")

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid user context")

    return {"tenant_id": tenant_id, "user_id": UUID(user_id) if user_id else None}


def _q2(v) -> Decimal:
    """Bulatkan nominal ke 2 desimal (Decimal) untuk DB numeric(18,2); presisi dijaga sepanjang pipa."""
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
        "subtotal": _q2(subtotal),
        "discount_amount": _q2(discount),
        "tax_amount": _q2(tax_amount),
        "total": _q2(total),
    }


async def _cn_line_code(conn, tenant_id, ln, cache):
    """3(c): kode PPN baris, DETERMINISTIK dan berarah 'output'. Urutan: tax_code_id eksplisit ->
    tax_code (teks) milik tenant -> kode output aktif bertarif sama (is_default DESC, code)."""
    if ln.get("tax_code_id"):
        return str(ln["tax_code_id"])
    rate = Decimal(str(ln.get("tax_rate") or 0))
    if rate <= 0:
        return None
    key = (ln.get("tax_code") or "", str(rate))
    if key in cache:
        return cache[key]
    tcid = None
    if ln.get("tax_code"):
        tcid = await conn.fetchval(
            "SELECT id FROM tax_codes WHERE tenant_id = $1 AND code = $2 AND tax_type = 'ppn' AND direction = 'output' LIMIT 1",
            tenant_id, ln["tax_code"],
        )
    if tcid is None:
        tcid = await conn.fetchval(
            """SELECT id FROM tax_codes WHERE tenant_id = $1 AND tax_type = 'ppn' AND direction = 'output'
               AND rate = $2 AND is_active ORDER BY is_default DESC, code LIMIT 1""",
            tenant_id, rate,
        )
    cache[key] = str(tcid) if tcid else None
    return cache[key]


async def _cn_doc(conn, tenant_id, items, discount_percent, discount_amount, header_tax_rate) -> dict:
    """3(c) -- nota kredit lewat kalkulator bersama (services/sales_doc_calc), SAMA dengan faktur:
    diskon baris MENGURANGI total (dulu subtotal header = SIGMA BRUTO baris, diskon baris hilang),
    diskon dokumen dialokasikan SEBELUM PPN, faktor DPP per kode. tax_rate header (lama: 'pajak
    menyeluruh') diterapkan ke baris yang tak punya tarif sendiri."""
    from ..services.sales_doc_calc import compute_document, DocumentDiscountError
    from ..services.tax_factor import attach_dpp_factors
    lines = [dict(it) for it in items]
    hr = Decimal(str(header_tax_rate or 0))
    cache = {}
    for ln in lines:
        if hr > 0 and Decimal(str(ln.get("tax_rate") or 0)) <= 0:
            ln["tax_rate"] = hr
        ln["tax_code_id"] = await _cn_line_code(conn, tenant_id, ln, cache)
    await attach_dpp_factors(conn, tenant_id, lines, "tax_code_id")
    try:
        return compute_document(lines, doc_discount_amount=discount_amount or 0,
                                doc_discount_percent=discount_percent or 0)
    except DocumentDiscountError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def get_invoice_remaining_from_journal(conn, tenant_id: str, invoice_id) -> Decimal:
    """Compute invoice remaining from journal lines on AR account (Law 16).

    For sales invoices:
    - Invoice posting: DEBIT AR (1-10400) -> increases receivable
    - Payment received: CREDIT AR -> decreases receivable
    - Credit note applied: CREDIT AR -> decreases receivable
    - Customer deposit applied: CREDIT AR -> decreases receivable

    Outstanding = SUM(debit) - SUM(credit) on AR for this invoice's journal chain
    """
    result = await conn.fetchval(
        """
        SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.status = 'POSTED'
            AND coa.account_code = '1-10400'  -- Law 27: read filter, resolved via JOIN
            AND je.tenant_id = $1
            AND (
                -- Original invoice journal
                (je.source_type = 'INVOICE' AND je.source_id = $2)
                -- Payment journals linked via allocations
                OR (je.source_type IN ('RECEIVE_PAYMENT', 'PAYMENT_RECEIVED') AND EXISTS (
                    SELECT 1 FROM receive_payment_allocations rpa
                    WHERE rpa.invoice_id = $2 AND rpa.payment_id = je.source_id
                ))
                -- PAYMENT_RECEIVED orphan journals (description-based fallback)
                OR (je.source_type = 'PAYMENT_RECEIVED'
                    AND je.description LIKE '%%' || (SELECT invoice_number FROM sales_invoices WHERE id = $2) || '%%'
                    AND NOT EXISTS(
                        SELECT 1 FROM receive_payment_allocations rpa2
                        WHERE rpa2.payment_id = je.source_id AND rpa2.tenant_id = $1
                    ))
                -- Credit note journals linked via allocations
                OR (je.source_type = 'CREDIT_NOTE' AND EXISTS (
                    SELECT 1 FROM credit_note_applications cna
                    WHERE cna.invoice_id = $2 AND cna.credit_note_id = je.source_id AND cna.status = 'active'
                ))
                -- Customer deposit application journals linked via allocations
                OR (je.source_type = 'DEPOSIT_APPLICATION' AND EXISTS (
                    SELECT 1 FROM customer_deposit_applications cda
                    WHERE cda.invoice_id = $2 AND cda.deposit_id = je.source_id
                    -- FIX_P1_DEPOSIT 2026-06-16 OPTION B: drop reversed (un-applied)
                    -- deposit applications so invoice outstanding is restored.
                    AND is_effective_journal(je.id)
                ))
                -- Invoice reversal (partial void)
                OR (je.source_type = 'INVOICE_REVERSAL' AND je.source_id = $2)
                -- Inline payments from sales_invoices.py
                OR (je.id IN (
                    SELECT sip.journal_id FROM sales_invoice_payments sip
                    WHERE sip.invoice_id = $2
                ))
            )
    """,
        tenant_id,
        invoice_id,
    )
    return result if result is not None else Decimal(0)


# =============================================================================
# LIST CREDIT NOTES
# =============================================================================


@router.get("", response_model=CreditNoteListResponse)
async def list_credit_notes(
    request: Request,
    status: Optional[
        Literal["all", "draft", "posted", "partial", "applied", "void"]
    ] = Query("all"),
    customer_id: Optional[str] = Query(None),
    search: Optional[str] = Query(
        None, description="Search by number or customer name"
    ),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal[
        "credit_note_date", "credit_note_number", "total_amount", "created_at"
    ] = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
):
    """List credit notes with filters and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Build query conditions
            conditions = ["tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if status and status != "all":
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if customer_id:
                conditions.append(f"customer_id = ${param_idx}")
                params.append(customer_id)  # BATCH1: credit_notes.customer_id is VARCHAR -> bind str, not UUID(...)
                param_idx += 1

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(credit_note_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = credit_notes.tenant_id AND c.search_text ILIKE ${param_idx}))"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(credit_note_number ILIKE ${param_idx} OR customer_name ILIKE ${param_idx} OR search_text ILIKE ${param_idx} OR customer_id::text IN (SELECT c.id::text FROM customers c WHERE c.tenant_id = credit_notes.tenant_id AND c.search_text ILIKE ${param_idx}))"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            if date_from:
                conditions.append(f"credit_note_date >= ${param_idx}")
                params.append(date_from)
                param_idx += 1

            if date_to:
                conditions.append(f"credit_note_date <= ${param_idx}")
                params.append(date_to)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Sort
            valid_sorts = {
                "credit_note_date": "credit_note_date",
                "credit_note_number": "credit_note_number",
                "total_amount": "total_amount",
                "created_at": "created_at",
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count total
            count_query = f"SELECT COUNT(*) FROM credit_notes WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items
            query = f"""
                SELECT id, credit_note_number, customer_id, customer_name,
                       credit_note_date, total_amount, amount_applied, amount_refunded,
                       status, reason, created_at
                FROM credit_notes
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "credit_note_number": row["credit_note_number"],
                    "customer_id": str(row["customer_id"])
                    if row["customer_id"]
                    else None,
                    "customer_name": row["customer_name"],
                    "credit_note_date": row["credit_note_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_applied": row["amount_applied"] or 0,
                    "amount_refunded": row["amount_refunded"] or 0,
                    "remaining_amount": 0
                    if row["status"] == "void"
                    else (
                        row["total_amount"]
                        - (row["amount_applied"] or 0)
                        - (row["amount_refunded"] or 0)
                    ),
                    "status": row["status"],
                    "reason": row["reason"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing credit notes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list credit notes")


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary", response_model=CreditNoteSummaryResponse)
async def get_credit_notes_summary(request: Request):
    """Get summary statistics for credit notes."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Law 16: Counts from table, amounts from journal (truth)
            query = """
                WITH cn_journal AS (
                    SELECT cn.id,
                           COALESCE(SUM(CASE WHEN coa.account_type = 'RECEIVABLE' AND jl.credit > 0 THEN jl.credit ELSE 0 END), 0) as journal_value
                    FROM credit_notes cn
                    JOIN journal_entries je ON je.source_id = cn.id AND je.source_type = 'CREDIT_NOTE'
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE cn.tenant_id = $1 AND cn.status != 'void'
                      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                    GROUP BY cn.id
                )
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE cn.status = 'draft') as draft_count,
                    COUNT(*) FILTER (WHERE cn.status = 'posted') as posted_count,
                    COUNT(*) FILTER (WHERE cn.status = 'partial') as partial_count,
                    COUNT(*) FILTER (WHERE cn.status = 'applied') as applied_count,
                    COALESCE(SUM(cnj.journal_value), 0) as total_value,
                    COALESCE(SUM(cn.amount_applied), 0) as total_applied,
                    COALESCE(SUM(cn.amount_refunded), 0) as total_refunded,
                    COALESCE(SUM(cnj.journal_value - COALESCE(cn.amount_applied, 0) - COALESCE(cn.amount_refunded, 0))
                        FILTER (WHERE cn.status IN ('posted', 'partial')), 0) as available_balance
                FROM credit_notes cn
                LEFT JOIN cn_journal cnj ON cnj.id = cn.id
                WHERE cn.tenant_id = $1 AND cn.status != 'void'
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "total": row["total"] or 0,
                    "draft_count": row["draft_count"] or 0,
                    "posted_count": row["posted_count"] or 0,
                    "partial_count": row["partial_count"] or 0,
                    "applied_count": row["applied_count"] or 0,
                    "total_value": float(row["total_value"] or 0),
                    "total_applied": float(row["total_applied"] or 0),
                    "total_refunded": float(row["total_refunded"] or 0),
                    "available_balance": float(row["available_balance"] or 0),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting credit notes summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# GET CREDIT NOTE DETAIL
# =============================================================================


@router.get("/{credit_note_id}", response_model=CreditNoteDetailResponse)
async def get_credit_note(request: Request, credit_note_id: UUID):
    """Get detailed information for a credit note."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get credit note
            cn = await conn.fetchrow(
                """
                SELECT * FROM credit_notes
                WHERE id = $1 AND tenant_id = $2
            """,
                credit_note_id,
                ctx["tenant_id"],
            )

            if not cn:
                raise HTTPException(status_code=404, detail="Credit note not found")

            # Get items
            items = await conn.fetch(
                """
                SELECT * FROM credit_note_items
                WHERE credit_note_id = $1
                ORDER BY line_number
            """,
                credit_note_id,
            )

            # Get applications with invoice numbers
            applications = await conn.fetch(
                """
                SELECT a.*, i.invoice_number
                FROM credit_note_applications a
                LEFT JOIN sales_invoices i ON a.invoice_id = i.id
                WHERE a.credit_note_id = $1
                ORDER BY a.application_date
            """,
                credit_note_id,
            )

            # Get refunds
            refunds = await conn.fetch(
                """
                SELECT * FROM credit_note_refunds
                WHERE credit_note_id = $1
                ORDER BY refund_date
            """,
                credit_note_id,
            )

            # Build response
            remaining = (
                0
                if cn["status"] == "void"
                else (
                    cn["total_amount"]
                    - (cn["amount_applied"] or 0)
                    - (cn["amount_refunded"] or 0)
                )
            )

            return {
                "success": True,
                "data": {
                    "id": str(cn["id"]),
                    "credit_note_number": cn["credit_note_number"],
                    "customer_id": str(cn["customer_id"])
                    if cn["customer_id"]
                    else None,
                    "customer_name": cn["customer_name"],
                    "original_invoice_id": str(cn["original_invoice_id"])
                    if cn["original_invoice_id"]
                    else None,
                    "original_invoice_number": cn["original_invoice_number"],
                    "subtotal": cn["subtotal"],
                    "discount_percent": float(cn["discount_percent"] or 0),
                    "discount_amount": cn["discount_amount"] or 0,
                    "tax_rate": float(cn["tax_rate"] or 0),
                    "tax_amount": cn["tax_amount"] or 0,
                    "total_amount": cn["total_amount"],
                    "amount_applied": cn["amount_applied"] or 0,
                    "amount_refunded": cn["amount_refunded"] or 0,
                    "remaining_amount": remaining,
                    "status": cn["status"],
                    "credit_note_date": cn["credit_note_date"].isoformat(),
                    "reason": cn["reason"],
                    "reason_detail": cn["reason_detail"],
                    "ref_no": cn["ref_no"],
                    "notes": cn["notes"],
                    "ar_id": str(cn["ar_id"]) if cn["ar_id"] else None,
                    "journal_id": str(cn["journal_id"]) if cn["journal_id"] else None,
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item["unit"],
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"] or 0,
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"] or 0,
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                        }
                        for item in items
                    ],
                    "applications": [
                        {
                            "id": str(app["id"]),
                            "invoice_id": str(app["invoice_id"]),
                            "invoice_number": app["invoice_number"],
                            "amount_applied": app["amount_applied"],
                            "application_date": app["application_date"].isoformat(),
                            "created_at": app["created_at"].isoformat(),
                        }
                        for app in applications
                    ],
                    "refunds": [
                        {
                            "id": str(ref["id"]),
                            "amount": ref["amount"],
                            "refund_date": ref["refund_date"].isoformat(),
                            "payment_method": ref["payment_method"],
                            "account_id": str(ref["account_id"]),
                            "reference": ref["reference"],
                            "created_at": ref["created_at"].isoformat(),
                        }
                        for ref in refunds
                    ],
                    "posted_at": cn["posted_at"].isoformat()
                    if cn["posted_at"]
                    else None,
                    "posted_by": str(cn["posted_by"]) if cn["posted_by"] else None,
                    "voided_at": cn["voided_at"].isoformat()
                    if cn["voided_at"]
                    else None,
                    "voided_reason": cn["voided_reason"],
                    "created_at": cn["created_at"].isoformat(),
                    "updated_at": cn["updated_at"].isoformat(),
                    "created_by": str(cn["created_by"]) if cn["created_by"] else None,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get credit note")


# =============================================================================
# CREATE CREDIT NOTE (DRAFT)
# =============================================================================


@router.post("", response_model=CreditNoteResponse, status_code=201)
async def create_credit_note(request: Request, body: CreateCreditNoteRequest):
    """
    Create a new credit note in draft status.

    Draft credit notes can be edited before posting.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Pelanggan yang diisi harus ada di tenant ini; ditulis sebagai UUID kanonik (13 Sep 2026:
                # dulu body mentah -> CN-2608-0001 menyimpan NAMA "Toko Melati" sebagai customer_id).
                pelanggan_cn = await pelanggan_kanonik_tenant(conn, ctx["tenant_id"], body.customer_id)

                # Generate credit note number
                cn_number = await conn.fetchval(
                    "SELECT generate_credit_note_number($1, 'CN')", ctx["tenant_id"]
                )

                # 3(c): kalkulator bersama (lihat _cn_doc).
                _doc = await _cn_doc(
                    conn, ctx["tenant_id"], [item.model_dump() for item in body.items],
                    body.discount_percent, body.discount_amount, body.tax_rate,
                )
                calculated_items = _doc["items"]
                subtotal = _doc["gross_subtotal"]
                overall_discount = _doc["doc_discount"]
                overall_tax = _doc["tax_amount"]
                total_amount = _doc["total_amount"]

                # Get original invoice number if provided
                original_invoice_number = None
                faktur_asal = None
                if body.original_invoice_id:
                    # Unit B (a): dulu tanpa tenant & tanpa pelanggan -> atribusi lintas pihak saat posting.
                    faktur_asal = await faktur_tenant_untuk_pelanggan(
                        conn, ctx["tenant_id"], body.original_invoice_id, pelanggan_cn
                    )
                    original_invoice_number = faktur_asal["invoice_number"]

                # Insert credit note
                cn_id = await conn.fetchval(
                    """
                    INSERT INTO credit_notes (
                        tenant_id, credit_note_number, customer_id, customer_name,
                        original_invoice_id, original_invoice_number,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, credit_note_date, reason, reason_detail,
                        ref_no, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                              'draft', $13, $14, $15, $16, $17, $18)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    cn_number,
                    pelanggan_cn,
                    body.customer_name,
                    faktur_asal["id"] if faktur_asal else None,
                    original_invoice_number,
                    subtotal,
                    body.discount_percent,
                    overall_discount,
                    body.tax_rate,
                    overall_tax,
                    total_amount,
                    body.credit_note_date,
                    body.reason,
                    body.reason_detail,
                    body.ref_no,
                    body.notes,
                    ctx["user_id"],
                )

                # Insert items
                for idx, item in enumerate(calculated_items, 1):
                    await conn.execute(
                        """
                        INSERT INTO credit_note_items (
                            credit_note_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number,
                            tax_code_id, dpp, dpp_harga_jual
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                    """,
                        cn_id,
                        UUID(item["item_id"]) if item.get("item_id") else None,
                        item.get("item_code"),
                        item["description"],
                        item["quantity"],
                        item.get("unit"),
                        item["unit_price"],
                        item.get("discount_percent", 0),
                        item.get("discount_amount", 0),
                        item.get("tax_code"),
                        item.get("tax_rate", 0),
                        item.get("tax_amount", 0),
                        item["subtotal"],
                        item["total"],
                        idx,
                        UUID(item["tax_code_id"]) if item.get("tax_code_id") else None,
                        item["dpp"],
                        item["dpp_harga_jual"],
                    )

                logger.info(f"Credit note created: {cn_id}, number={cn_number}")

                return {
                    "success": True,
                    "message": "Credit note created successfully",
                    "data": {
                        "id": str(cn_id),
                        "credit_note_number": cn_number,
                        "total_amount": total_amount,
                        "status": "draft",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating credit note: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create credit note")


# =============================================================================
# UPDATE CREDIT NOTE (DRAFT ONLY)
# =============================================================================


@router.patch("/{credit_note_id}", response_model=CreditNoteResponse)
async def update_credit_note(
    request: Request, credit_note_id: UUID, body: UpdateCreditNoteRequest
):
    """
    Update a draft credit note.

    Only draft credit notes can be updated.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        # Optimistic concurrency (opt-in If-Match): reject a stale write.
        from ..services.optimistic_concurrency import assert_if_match_row
        await assert_if_match_row(request, "credit_notes", credit_note_id)

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Check status
                cn = await conn.fetchrow(
                    """
                    SELECT id, status FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] != "draft":
                    raise HTTPException(
                        status_code=400, detail="Only draft credit notes can be updated"
                    )

                # Build update data
                update_data = body.model_dump(exclude_unset=True)

                if not update_data:
                    return {
                        "success": True,
                        "message": "No changes provided",
                        "data": {"id": str(credit_note_id)},
                    }

                # 3(c): hitung ulang bila BARIS atau DISKON/TARIF berubah. Dulu: suntingan baris tanpa
                # mengirim ulang diskon MENOLKAN diskon (default 0, bukan nilai tersimpan), dan
                # suntingan diskon-saja tak menghitung ulang total.
                _recalc = bool(update_data.get("items")) or bool(
                    {"discount_percent", "discount_amount", "tax_rate"} & set(update_data)
                )
                if _recalc and not update_data.get("items"):
                    _rows = await conn.fetch(
                        "SELECT * FROM credit_note_items WHERE credit_note_id = $1 ORDER BY line_number",
                        credit_note_id,
                    )
                    update_data["items"] = [
                        {k: r[k] for k in ("item_id", "item_code", "description", "quantity", "unit",
                                           "unit_price", "discount_percent", "discount_amount",
                                           "tax_code", "tax_code_id", "tax_rate")}
                        for r in _rows
                    ]
                if _recalc:
                    _cur = await conn.fetchrow(
                        "SELECT discount_percent, discount_amount, tax_rate FROM credit_notes WHERE id = $1",
                        credit_note_id,
                    )
                    await conn.execute(
                        "DELETE FROM credit_note_items WHERE credit_note_id = $1",
                        credit_note_id,
                    )
                    _items = [
                        (i.model_dump() if hasattr(i, "model_dump") else dict(i))
                        for i in (body.items if body.items else update_data["items"])
                    ]
                    for i in _items:
                        if i.get("item_id") is not None:
                            i["item_id"] = str(i["item_id"])
                    discount_percent = update_data.get("discount_percent", _cur["discount_percent"] or 0)
                    discount_amount = update_data.get("discount_amount", _cur["discount_amount"] or 0)
                    tax_rate = update_data.get("tax_rate", _cur["tax_rate"] or 0)
                    _doc = await _cn_doc(conn, ctx["tenant_id"], _items, discount_percent,
                                         discount_amount if not discount_percent else 0, tax_rate)
                    calculated_items = _doc["items"]
                    subtotal = _doc["gross_subtotal"]
                    overall_discount = _doc["doc_discount"]
                    overall_tax = _doc["tax_amount"]
                    total_amount = _doc["total_amount"]

                    # Update totals
                    await conn.execute(
                        """
                        UPDATE credit_notes
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """,
                        credit_note_id,
                        subtotal,
                        overall_discount,
                        overall_tax,
                        total_amount,
                    )

                    # Insert new items
                    for idx, item in enumerate(calculated_items, 1):
                        await conn.execute(
                            """
                            INSERT INTO credit_note_items (
                                credit_note_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number,
                                tax_code_id, dpp, dpp_harga_jual
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                        """,
                            credit_note_id,
                            UUID(item["item_id"]) if item.get("item_id") else None,
                            item.get("item_code"),
                            item["description"],
                            item["quantity"],
                            item.get("unit"),
                            item["unit_price"],
                            item.get("discount_percent", 0),
                            item.get("discount_amount", 0),
                            item.get("tax_code"),
                            item.get("tax_rate", 0),
                            item.get("tax_amount", 0),
                            item["subtotal"],
                            item["total"],
                            idx,
                            UUID(item["tax_code_id"]) if item.get("tax_code_id") else None,
                            item["dpp"],
                            item["dpp_harga_jual"],
                        )

                    update_data.pop("items", None)

                # Update other fields
                if update_data:
                    excluded = {"items"}
                    updates = []
                    # Unit B (a): kaitan faktur divalidasi terhadap pelanggan EFEKTIF sesudah suntingan.
                    faktur_patch = None
                    if "original_invoice_id" in update_data or "customer_id" in update_data:
                        lama = await conn.fetchrow(
                            "SELECT customer_id::text AS c, original_invoice_id AS f FROM credit_notes WHERE id = $1",
                            credit_note_id,
                        )
                        pel_efektif = (
                            await pelanggan_kanonik_tenant(conn, ctx["tenant_id"], update_data["customer_id"])
                            if "customer_id" in update_data else lama["c"]
                        )
                        f_efektif = update_data["original_invoice_id"] if "original_invoice_id" in update_data else lama["f"]
                        if f_efektif:
                            faktur_patch = await faktur_tenant_untuk_pelanggan(
                                conn, ctx["tenant_id"], f_efektif, pel_efektif
                            )
                        if "original_invoice_id" in update_data:
                            update_data["original_invoice_number"] = faktur_patch["invoice_number"] if faktur_patch else None
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field in excluded:
                            continue
                        updates.append(f"{field} = ${param_idx}")
                        if field == "customer_id":
                            # 13 Sep 2026: dulu UUID(value) dikirim ke kolom VARCHAR -> asyncpg
                            # "expected str, got UUID" (dan nama -> ValueError): mengubah pelanggan
                            # draf nota kredit selalu 500. Kini divalidasi & kanonik.
                            params.append(await pelanggan_kanonik_tenant(conn, ctx["tenant_id"], value))
                        elif field == "original_invoice_id":
                            params.append(faktur_patch["id"] if faktur_patch else None)
                        else:
                            params.append(value)
                        param_idx += 1

                    if updates:
                        params.extend([credit_note_id, ctx["tenant_id"]])
                        query = f"""
                            UPDATE credit_notes
                            SET {', '.join(updates)}, updated_at = NOW()
                            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                        """
                        await conn.execute(query, *params)

                logger.info(f"Credit note updated: {credit_note_id}")

                return {
                    "success": True,
                    "message": "Credit note updated successfully",
                    "data": {"id": str(credit_note_id)},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update credit note")


# =============================================================================
# DELETE CREDIT NOTE (DRAFT ONLY)
# =============================================================================


@router.delete("/{credit_note_id}", response_model=CreditNoteResponse)
async def delete_credit_note(request: Request, credit_note_id: UUID):
    """
    Delete a draft credit note.

    Only draft credit notes can be deleted. Use void for posted credit notes.
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check status
            cn = await conn.fetchrow(
                """
                SELECT id, status, credit_note_number FROM credit_notes
                WHERE id = $1 AND tenant_id = $2
            """,
                credit_note_id,
                ctx["tenant_id"],
            )

            if not cn:
                raise HTTPException(status_code=404, detail="Credit note not found")

            if cn["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Only draft credit notes can be deleted. Use void for posted.",
                )

            # Delete (cascade will delete items)
            await conn.execute("DELETE FROM credit_notes WHERE id = $1", credit_note_id)

            logger.info(f"Credit note deleted: {credit_note_id}")

            return {
                "success": True,
                "message": "Credit note deleted successfully",
                "data": {
                    "id": str(credit_note_id),
                    "credit_note_number": cn["credit_note_number"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete credit note")


# =============================================================================
# POST CREDIT NOTE TO ACCOUNTING
# =============================================================================


# #37: satu-satunya alasan nota kredit yang mengembalikan barang ke stok.
ALASAN_RESTOCK = frozenset({"return"})


def _cn_memulihkan_stok(reason) -> bool:
    return reason in ALASAN_RESTOCK


# CN HPP asli (25 Sep 2026, lanjutan #37 C/D). Retur yang MENUNJUK faktur asal
# dipulihkan pada biaya dan gudang KELUARNYA barang itu dari faktur tsb, bukan
# WAC hari ini + gudang pertama tenant. Dulu: HPP pembalik = WAC saat NK
# diposting (bisa jauh dari HPP yang dibebankan saat faktur dikirim), dan
# retur atas faktur yang barangnya BELUM PERNAH keluar tetap menambah stok +
# mengkredit HPP yang tak pernah didebit (stok hantu). Tanpa faktur asal ->
# perilaku lama (WAC + gudang pertama).
PENANDA_CN_HPP_ASLI = "cn-hpp-asli-dari-keluar-faktur"
_SUMBER_KELUAR_FAKTUR = ("SALES_INVOICE", "INVOICE_FULFILLMENT")


async def _keluar_faktur_untuk_retur(conn, tenant_id, invoice_id, product_id, credit_note_id):
    """(sisa_qty, unit_cost, warehouse_id) keluarnya barang dari faktur asal.

    sisa = keluar bersih (keluar - pembalik pengiriman) - yang SUDAH dipulihkan
    NK lain (non-void) atas faktur yang sama. unit_cost = rata-rata tertimbang
    biaya keluar. warehouse = gudang dengan kuantitas keluar terbesar.
    None bila barang ini tak pernah keluar lewat faktur itu.
    """
    rows = await conn.fetch(
        """
        SELECT warehouse_id, quantity_in, quantity_out, unit_cost
        FROM inventory_ledger
        WHERE tenant_id = $1 AND source_id = $2 AND product_id = $3
          AND source_type = ANY($4::text[])
        """,
        tenant_id,
        invoice_id,
        product_id,
        list(_SUMBER_KELUAR_FAKTUR),
    )
    keluar_qty = Decimal("0")
    keluar_biaya = Decimal("0")
    balik_qty = Decimal("0")
    per_gudang = {}
    for r in rows:
        q_out = Decimal(str(r["quantity_out"] or 0))
        q_in = Decimal(str(r["quantity_in"] or 0))
        if q_out > 0:
            keluar_qty += q_out
            keluar_biaya += q_out * Decimal(str(r["unit_cost"] or 0))
            per_gudang[r["warehouse_id"]] = per_gudang.get(r["warehouse_id"], Decimal("0")) + q_out
        if q_in > 0:
            balik_qty += q_in
    if keluar_qty <= 0:
        return None
    sudah_retur = await conn.fetchval(
        """
        SELECT COALESCE(SUM(il.quantity_in), 0)
        FROM inventory_ledger il
        JOIN credit_notes c ON c.id = il.source_id
        WHERE il.tenant_id = $1 AND il.product_id = $2 AND il.source_type = 'CREDIT_NOTE'
          AND c.tenant_id = $1 AND c.original_invoice_id = $3
          AND c.status <> 'void' AND c.id <> $4
        """,
        tenant_id,
        product_id,
        invoice_id,
        credit_note_id,
    )
    sisa = keluar_qty - balik_qty - Decimal(str(sudah_retur or 0))
    unit_cost = (keluar_biaya / keluar_qty).quantize(Decimal("0.01"))
    gudang = max(per_gudang.items(), key=lambda kv: kv[1])[0]
    return sisa, unit_cost, gudang


@router.post("/{credit_note_id}/post", response_model=CreditNoteResponse)
async def post_credit_note(request: Request, credit_note_id: UUID):
    """
    Post credit note to accounting.

    Creates journal entry:
    - Dr. Sales Returns (Retur Penjualan)
    - Dr. VAT Payable (if tax)
    - Cr. Accounts Receivable

    Changes status from 'draft' to 'posted'.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE:{credit_note_id}",
                )

                # Get credit note
                cn = await conn.fetchrow(
                    """
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] != "draft":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot post credit note with status '{cn['status']}'",
                    )

                # Unit B: kaitan faktur dari draf diperiksa ulang saat posting (draf lama belum tervalidasi).
                faktur_asal = None
                if cn["original_invoice_id"]:
                    faktur_asal = await faktur_tenant_untuk_pelanggan(
                        conn, ctx["tenant_id"], cn["original_invoice_id"], cn["customer_id"]
                    )
                    await pastikan_cn_muat_faktur(conn, ctx["tenant_id"], faktur_asal, cn["total_amount"])

                # Law 5: Period lock check
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    cn["credit_note_date"],
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Get account IDs
                # Law 27: Resolve account IDs dynamically
                ar_account_id = await resolve_account_id(
                    conn, ctx["tenant_id"], AR_ACCOUNT_CODE
                )
                sales_return_account_id = await resolve_account_id(
                    conn, ctx["tenant_id"], SALES_RETURN_ACCOUNT_CODE
                )

                if not ar_account_id or not sales_return_account_id:
                    raise HTTPException(
                        status_code=500, detail="Required accounts not found in CoA"
                    )

                # Generate journal number
                import uuid as uuid_module

                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                journal_number = await conn.fetchval(
                    """
                    SELECT get_next_journal_number($1, 'CN')
                """,
                    ctx["tenant_id"],
                )

                if not journal_number:
                    # Fallback if function doesn't exist
                    journal_number = f"CN-{cn['credit_note_number']}"

                # Calculate amounts
                total_amount = cn["total_amount"]
                tax_amount = cn["tax_amount"] or 0
                subtotal = total_amount - tax_amount

                # V312 (26 Sep 2026): porsi NK atas kewajiban yang BELUM dipenuhi (pendapatan masih di Dimuka) ->
                # Dr Dimuka + allocated_amount turun; sisanya Dr Retur. Dulu seluruhnya Dr Retur -> Dimuka terdampar
                # bila barang tak pernah dikirim (Check 16 buta). Lihat services/cn_tertunda.py.
                porsi_tertunda, baris_faktur_nk = await cn_tertunda.hitung_untuk_nk(
                    conn, ctx["tenant_id"], cn, subtotal
                )
                u_tertunda = sum(porsi_tertunda.values(), Decimal("0"))
                dimuka_account_id = None
                if u_tertunda > 0:
                    dimuka_account_id = await resolve_account_id_by_role(
                        conn, ctx["tenant_id"], AccountRole.REVENUE_DEFERRED
                    )
                    if not dimuka_account_id:
                        raise HTTPException(
                            status_code=500, detail="Akun Pendapatan Diterima Dimuka (REVENUE_DEFERRED) tak ditemukan"
                        )

                # Create journal entry
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    cn["credit_note_date"],
                    f"Credit Note {cn['credit_note_number']} - {cn['customer_name']}",
                    credit_note_id,
                    str(trace_id),
                    total_amount,
                    ctx["user_id"],
                )

                # Journal lines
                line_number = 1

                # V312: Dr. Pendapatan Diterima Dimuka (porsi kewajiban yang belum dipenuhi)
                if u_tertunda > 0:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        line_number,
                        dimuka_account_id,
                        u_tertunda,
                        f"Pengurangan Pendapatan Diterima Dimuka - {cn['credit_note_number']}",
                    )
                    line_number += 1

                # Dr. Sales Returns (subtotal - porsi tertunda)
                if subtotal - u_tertunda > 0:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (
                            id, journal_id, line_number, account_id, debit, credit, memo
                        ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                        uuid_module.uuid4(),
                        journal_id,
                        line_number,
                        sales_return_account_id,
                        subtotal - u_tertunda,
                        f"Retur Penjualan - {cn['credit_note_number']}",
                    )
                    line_number += 1

                # Dr. VAT Payable (if tax)
                # Fase D2-wrap C4: PKP-guarded VAT_OUTPUT role resolution.
                # Tenant non-PKP tidak boleh post PPN > 0 (fail-loud 422).
                tax_jl_id = None
                if tax_amount > 0:
                    tax_account_id = await resolve_account_id_by_role_if_pkp(
                        conn, ctx["tenant_id"], "VAT_OUTPUT"
                    )

                    if tax_account_id is None:
                        raise HTTPException(
                            status_code=422,
                            detail="Tenant non-PKP tidak dapat post PPN > 0 pada credit note",
                        )

                    if tax_account_id:
                        tax_jl_id = uuid_module.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                            tax_jl_id,
                            journal_id,
                            line_number,
                            tax_account_id,
                            tax_amount,
                            f"PPN Retur - {cn['credit_note_number']}",
                        )
                        line_number += 1

                # Cr. Accounts Receivable
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    ar_account_id,
                    total_amount,
                    f"Pengurangan Piutang - {cn['credit_note_number']}",
                )

                # Law 20: Promote DRAFT -> POSTED after all lines inserted
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # V312: allocated_amount baris faktur turun sebesar porsi + catat porsi (untuk void cermin)
                if porsi_tertunda:
                    await cn_tertunda.catat_porsi(
                        conn, ctx["tenant_id"], cn, journal_id, porsi_tertunda, baris_faktur_nk
                    )

                # ── Inventory restock for returned goods + COGS companion journal ──
                # Fase 4 fix (V164): emit companion journal Dr INVENTORY / Cr COGS @ WAC×qty
                # mirroring `record_inventory_outbound` (Sales side). Direct reversal pattern,
                # NOT wash. Atomic within same transaction as main CN journal.
                from ..services.inventory_helpers import record_inventory_inbound

                cn_items = await conn.fetch(
                    "SELECT * FROM credit_note_items WHERE credit_note_id = $1",
                    credit_note_id,
                )

                inv_restock_results = []  # [(ledger_id, total_cost)]
                companion_total_cost = Decimal("0")
                # Per-product accounts (resolved lazily once per product)
                _acct_cache = {}

                # #37: barang KEMBALI ke stok (+ jurnal Dr Persediaan / Cr HPP) HANYA untuk
                # retur. Koreksi harga / diskon / rusak / lainnya = nilai saja. Dulu SETIAP
                # baris barang ber-track_inventory di-restock apa pun alasannya -> CN koreksi
                # harga yang memilih barang katalog menambah stok fiktif dan mengurangi HPP.
                # Rusak: putusan pemilik 25 Sep -- tanpa restock; kerugian lewat penyesuaian stok.
                if cn["original_invoice_id"] and _cn_memulihkan_stok(cn["reason"]):
                    # Serialkan NK retur atas faktur yang sama: batas "sisa terkirim"
                    # dihitung di bawah dan tak boleh dilewati dua posting bersamaan.
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"CN_RETUR_FAKTUR:{ctx['tenant_id']}:{cn['original_invoice_id']}",
                    )
                for item in (cn_items if _cn_memulihkan_stok(cn["reason"]) else []):
                    if not item["item_id"]:
                        continue
                    # Check if product tracks inventory
                    product = await conn.fetchrow(
                        "SELECT id, item_code, nama_produk, track_inventory FROM products WHERE id = $1",
                        item["item_id"],
                    )
                    if not product or not product["track_inventory"]:
                        continue

                    qty_dec = Decimal(str(item["quantity"]))
                    if cn["original_invoice_id"]:
                        # HPP + gudang ASLI dari keluarnya barang lewat faktur asal.
                        asal = await _keluar_faktur_untuk_retur(
                            conn, ctx["tenant_id"], cn["original_invoice_id"],
                            product["id"], credit_note_id,
                        )
                        nama_brg = product["nama_produk"] or product["item_code"]
                        if asal is None:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"Barang {nama_brg} belum pernah dikirim lewat faktur "
                                    f"{cn['original_invoice_number'] or ''}".rstrip()
                                    + ", jadi tidak bisa diretur ke stok. Pilih alasan selain "
                                    "'Retur barang' bila ini koreksi nilai."
                                ),
                            )
                        sisa, unit_cost_val, wh_id = asal
                        if qty_dec > sisa:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"Retur {nama_brg} {qty_dec.normalize():f} melebihi yang "
                                    f"terkirim dan belum diretur ({max(sisa, Decimal('0')).normalize():f})."
                                ),
                            )
                    else:
                        # Tanpa faktur asal: WAC hari ini + gudang pertama tenant (lama).
                        avg_cost = await conn.fetchval(
                            "SELECT get_weighted_average_cost($1, $2)",
                            ctx["tenant_id"],
                            product["id"],
                        )
                        unit_cost_val = Decimal(str(avg_cost)) if avg_cost else Decimal("0")
                        wh_id = await conn.fetchval(
                            "SELECT id FROM warehouses WHERE tenant_id = $1 ORDER BY created_at LIMIT 1",
                            ctx["tenant_id"],
                        )
                        if not wh_id:
                            continue
                    line_cost = qty_dec * unit_cost_val

                    inb_result = await record_inventory_inbound(
                        conn=conn,
                        tenant_id=ctx["tenant_id"],
                        product_id=product["id"],
                        product_code=product["item_code"],
                        product_name=product["nama_produk"],
                        warehouse_id=wh_id,
                        quantity=float(item["quantity"]),
                        unit_cost=float(unit_cost_val),
                        source_type="CREDIT_NOTE",
                        source_id=credit_note_id,
                        source_number=cn["credit_note_number"],
                        user_id=ctx["user_id"],
                        notes=f"Restock from Credit Note {cn['credit_note_number']}",
                        movement_date=cn["credit_note_date"],
                        movement_type="SALES_RETURN",  # #37 (dulu default 'PURCHASE')
                    )

                    if line_cost > 0:
                        # Cache per-product Inventory + COGS account resolution
                        if product["id"] not in _acct_cache:
                            prod_accts = await conn.fetchrow(
                                "SELECT cogs_account_id, inventory_account_id FROM products WHERE id = $1",
                                product["id"],
                            )
                            cogs_acct = (
                                prod_accts["cogs_account_id"] if prod_accts else None
                            )
                            inv_acct = (
                                prod_accts["inventory_account_id"]
                                if prod_accts
                                else None
                            )
                            if not cogs_acct:
                                logger.warning(
                                    "Product %s (tenant %s) has NULL cogs_account_id; "
                                    "substituting tenant COGS_SALES role default",
                                    product["id"],
                                    ctx["tenant_id"],
                                )
                                cogs_acct = await resolve_account_id_by_role(
                                    conn, ctx["tenant_id"], AccountRole.COGS_SALES
                                )
                            if not inv_acct:
                                logger.warning(
                                    "Product %s (tenant %s) has NULL inventory_account_id; "
                                    "substituting tenant INVENTORY_MERCHANDISE role default",
                                    product["id"],
                                    ctx["tenant_id"],
                                )
                                inv_acct = await resolve_account_id_by_role(
                                    conn,
                                    ctx["tenant_id"],
                                    AccountRole.INVENTORY_MERCHANDISE,
                                )
                            _acct_cache[product["id"]] = (inv_acct, cogs_acct)

                        inv_restock_results.append(
                            {
                                "ledger_id": inb_result["ledger_id"],
                                "product_id": product["id"],
                                "line_cost": line_cost,
                                "memo": f"Retur HPP - {cn['credit_note_number']} - {product['nama_produk']}",
                            }
                        )
                        companion_total_cost += line_cost

                # Emit companion journal (Dr Inventory / Cr COGS) if any tracked items
                if companion_total_cost > 0 and inv_restock_results:
                    companion_journal_id = uuid_module.uuid4()
                    companion_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'COGS-CN')",
                            ctx["tenant_id"],
                        )
                        or f"COGS-CN-{cn['credit_note_number']}"
                    )

                    # Header — DRAFT first (Law 20)
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE_COGS', $6,
                                  'DRAFT', $7, $7, $8)
                        """,
                        companion_journal_id,
                        ctx["tenant_id"],
                        companion_number,
                        cn["credit_note_date"],
                        f"COGS reversal {cn['credit_note_number']} - {cn['customer_name']}",
                        credit_note_id,
                        companion_total_cost,
                        ctx["user_id"],
                    )

                    # Lines: per-item Dr INVENTORY / aggregate Cr COGS
                    ln = 1
                    # Aggregate COGS lines per product (single Cr per product is also fine,
                    # but a single Cr COGS for the total is cleanest).
                    # Inventory side: one Dr line per ledger entry for traceability.
                    for r in inv_restock_results:
                        inv_acct_id, _cogs_acct_id = _acct_cache[r["product_id"]]
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, 0, $6)
                            """,
                            uuid_module.uuid4(),
                            companion_journal_id,
                            ln,
                            inv_acct_id,
                            r["line_cost"],
                            r["memo"],
                        )
                        ln += 1

                    # Single aggregate Cr COGS line (per-tenant COGS account; cache shows
                    # all products mapped to same COGS_SALES role in normal tenants).
                    # If products have heterogeneous COGS accounts, emit one Cr per account.
                    cogs_buckets: dict = {}
                    for r in inv_restock_results:
                        _inv, cogs_acct_id = _acct_cache[r["product_id"]]
                        cogs_buckets[cogs_acct_id] = (
                            cogs_buckets.get(cogs_acct_id, Decimal("0"))
                            + r["line_cost"]
                        )

                    for cogs_acct_id, total in cogs_buckets.items():
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, 0, $5, $6)
                            """,
                            uuid_module.uuid4(),
                            companion_journal_id,
                            ln,
                            cogs_acct_id,
                            total,
                            f"Retur HPP - {cn['credit_note_number']}",
                        )
                        ln += 1

                    # Law 20: Promote DRAFT -> POSTED
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        companion_journal_id,
                    )

                    # Link inventory_ledger rows just created to the companion journal
                    for r in inv_restock_results:
                        await conn.execute(
                            "UPDATE inventory_ledger SET journal_id = $1 WHERE id = $2",
                            companion_journal_id,
                            r["ledger_id"],
                        )

                # Wave 3: Write document_tax_lines (PPN reversal on CN)
                if tax_amount > 0 and tax_jl_id:
                    # 3(c): SATU baris DTL per kode PPN baris (arah 'output'), dasar = dpp TERSIMPAN.
                    # Dulu: "kode PPN aktif MANA SAJA, LIMIT 1, tanpa ORDER BY" (bisa kode MASUKAN) dan
                    # dasar = subtotal header (bruto).
                    _groups = await conn.fetch(
                        """SELECT cni.tax_code_id, SUM(COALESCE(cni.dpp, cni.subtotal - COALESCE(cni.discount_amount, 0))) AS base,
                                  SUM(cni.tax_amount) AS tax
                           FROM credit_note_items cni
                           WHERE cni.credit_note_id = $1 AND COALESCE(cni.tax_amount, 0) > 0
                           GROUP BY cni.tax_code_id""",
                        credit_note_id,
                    )
                    for _g in _groups:
                        ppn_tc_id = _g["tax_code_id"] or await conn.fetchval(
                            """SELECT id FROM tax_codes WHERE tenant_id = $1 AND tax_type = 'ppn'
                               AND direction = 'output' AND is_active ORDER BY is_default DESC, code LIMIT 1""",
                            ctx["tenant_id"],
                        )
                        if not ppn_tc_id:
                            continue
                        tc_coa = await conn.fetchval(
                            "SELECT coa_id FROM tax_codes WHERE id = $1",
                            ppn_tc_id,
                        )
                        dpp_val = float(_g["base"])
                        await conn.execute(
                            """
                            INSERT INTO document_tax_lines (
                                id, tenant_id, document_type, document_id,
                                tax_code_id, direction, base_amount, tax_amount,
                                coa_id, journal_line_id
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                            """,
                            uuid_module.uuid4(),
                            ctx["tenant_id"],
                            "CREDIT_NOTE",
                            credit_note_id,
                            ppn_tc_id,
                            "output",
                            dpp_val,
                            float(_g["tax"]),
                            tc_coa,
                            tax_jl_id,
                        )

                # Update credit note status
                await conn.execute(
                    """
                    UPDATE credit_notes
                    SET status = 'posted', journal_id = $2,
                        posted_at = NOW(), posted_by = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    credit_note_id,
                    journal_id,
                    ctx["user_id"],
                )

                if faktur_asal:
                    await segarkan_cache_piutang_faktur(conn, ctx["tenant_id"], faktur_asal["id"])

                logger.info(
                    f"Credit note posted: {credit_note_id}, journal={journal_id}"
                )

                return {
                    "success": True,
                    "message": "Credit note posted to accounting",
                    "data": {
                        "id": str(credit_note_id),
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "status": "posted",
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post credit note")


# =============================================================================
# APPLY CREDIT NOTE TO INVOICE(S)
# =============================================================================


@router.post("/{credit_note_id}/apply", response_model=CreditNoteResponse)
async def apply_credit_note(
    request: Request, credit_note_id: UUID, body: ApplyCreditNoteRequest
):
    """
    Apply credit note to one or more invoices.

    Reduces the invoice's outstanding balance.
    Credit note must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE_APPLY:{credit_note_id}",
                )

                # UNIT B (14 Sep 2026) — satu nota kredit, satu faktur, SELURUH nilainya.
                # Atribusi piutang = credit_notes.original_invoice_id (cabang 2 compute_ar_outstanding membaca
                # SELURUH kredit jurnal CN), maka penerapan sebagian tak bisa direpresentasikan. Nol jurnal baru:
                # piutang sudah dikredit saat CN dibukukan; apply hanya MENGATRIBUSI. Cache dihitung ulang dari
                # compute_ar_outstanding. Lihat backend/docs/TIKET-nota-kredit-apply-mati-20260913.md.
                if len(body.applications) != 1:
                    raise HTTPException(status_code=400, detail="Nota kredit hanya bisa diterapkan ke satu faktur.")
                app = body.applications[0]

                cn = await conn.fetchrow(
                    "SELECT * FROM credit_notes WHERE id = $1 AND tenant_id = $2",
                    credit_note_id,
                    ctx["tenant_id"],
                )
                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")
                if cn["original_invoice_id"] is not None or (cn["amount_applied"] or 0) > 0:
                    raise HTTPException(status_code=400, detail="Nota kredit ini sudah terkait ke faktur.")
                if cn["status"] != "posted" or cn["journal_id"] is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Hanya nota kredit yang sudah dibukukan yang bisa diterapkan ke faktur.",
                    )
                if (cn["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Nota kredit yang dananya sudah dikembalikan tidak bisa diterapkan ke faktur.",
                    )
                total_cn = Decimal(str(cn["total_amount"]))
                if Decimal(str(app.amount)) != total_cn:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Nota kredit harus diterapkan seluruhnya ({rupiah(total_cn)}) ke satu faktur.",
                    )

                invoice = await faktur_tenant_untuk_pelanggan(conn, ctx["tenant_id"], app.invoice_id, cn["customer_id"])
                await pastikan_cn_muat_faktur(conn, ctx["tenant_id"], invoice, total_cn)

                import uuid as uuid_module

                app_id = uuid_module.uuid4()
                application_date = body.application_date or await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
                await conn.execute(
                    """
                    INSERT INTO credit_note_applications (
                        id, tenant_id, credit_note_id, invoice_id, invoice_number,
                        amount_applied, application_date, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                    app_id,
                    ctx["tenant_id"],
                    credit_note_id,
                    invoice["id"],
                    invoice["invoice_number"],
                    total_cn,
                    application_date,
                    ctx["user_id"],
                )

                # V250: aplikasi aktif ditulis DULU — pagar DB hanya mengizinkan kaitan NULL->faktur bila aplikasi
                # aktifnya sudah ada. Gagal compare-and-set -> HTTPException -> transaksi batal (aplikasi ikut batal).
                terikat = await conn.execute(
                    """
                    UPDATE credit_notes
                    SET original_invoice_id = $1, original_invoice_number = $2, updated_at = NOW()
                    WHERE id = $3 AND tenant_id = $4 AND original_invoice_id IS NULL
                """,
                    invoice["id"],
                    invoice["invoice_number"],
                    credit_note_id,
                    ctx["tenant_id"],
                )
                if terikat != "UPDATE 1":
                    raise HTTPException(status_code=400, detail="Nota kredit ini sudah terkait ke faktur.")

                await segarkan_cache_piutang_faktur(conn, ctx["tenant_id"], invoice["id"])

                applications_created = [
                    {"application_id": str(app_id), "invoice_id": str(invoice["id"]), "amount": float(total_cn)}
                ]

                # Credit note status will be updated by trigger
                logger.info(
                    f"Credit note applied: {credit_note_id}, applications={len(applications_created)}"
                )

                return {
                    "success": True,
                    "message": f"Credit note applied to {len(applications_created)} invoice(s)",
                    "data": {
                        "id": str(credit_note_id),
                        "applications": applications_created,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error applying credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to apply credit note")


# =============================================================================
# UNAPPLY CREDIT NOTE (batalkan penerapan ke faktur) — 14 Sep 2026, unit (2)
# =============================================================================


@router.post("/{credit_note_id}/unapply", response_model=CreditNoteResponse)
async def unapply_credit_note(
    request: Request, credit_note_id: UUID, body: UnapplyCreditNoteRequest
):
    """
    Batalkan penerapan nota kredit ke faktur (kebalikan unit B).

    TANPA jurnal: apply juga tanpa jurnal (piutang sudah dikredit saat CN dibukukan; apply hanya mengatribusi).
    original_invoice_id kembali NULL lewat compare-and-set dari faktur yang tepat; baris aplikasi menjadi 'reversed'
    (riwayat, tak dihapus); cache faktur dihitung ulang dari compute_ar_outstanding; audit tercatat.
    Putusan pemilik: alasan WAJIB; periode tanggal penerapan TUTUP -> tolak.
    Sesudahnya void faktur dan void CN terbuka dengan sendirinya (penjaga keduanya membaca kaitan/aplikasi aktif).
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")
        alasan = (body.reason or "").strip()
        if not alasan:
            raise HTTPException(status_code=400, detail="Alasan pembatalan penerapan wajib diisi.")

        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Kunci SAMA dengan apply: apply dan unapply satu CN ter-serialkan
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE_APPLY:{credit_note_id}",
                )
                cn = await conn.fetchrow(
                    "SELECT * FROM credit_notes WHERE id = $1 AND tenant_id = $2",
                    credit_note_id,
                    ctx["tenant_id"],
                )
                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                aktif = await conn.fetch(
                    """
                    SELECT id, invoice_id, invoice_number, amount_applied, application_date
                    FROM credit_note_applications
                    WHERE credit_note_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )
                if not aktif or cn["original_invoice_id"] is None:
                    raise HTTPException(status_code=400, detail="Nota kredit ini belum diterapkan ke faktur mana pun.")
                if len(aktif) > 1:
                    raise HTTPException(
                        status_code=409,
                        detail="Nota kredit ini punya lebih dari satu penerapan aktif; hubungi dukungan.",
                    )
                app = aktif[0]

                # Law 5 (putusan pemilik): tanggal penerapan di periode TUTUP -> tolak (pola void_credit_note)
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    app["application_date"],
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi penerapan ({app['application_date']:%d-%m-%Y}) sudah {period_row['status']}; penerapan nota kredit tidak bisa dibatalkan.",
                    )

                balik = await conn.execute(
                    """
                    UPDATE credit_note_applications
                    SET status = 'reversed', reversed_at = NOW(), reversed_by = $2, reversal_reason = $3
                    WHERE id = $1 AND status = 'active'
                """,
                    app["id"],
                    ctx["user_id"],
                    alasan,
                )
                if balik != "UPDATE 1":
                    raise HTTPException(status_code=409, detail="Penerapan nota kredit berubah bersamaan; muat ulang lalu coba lagi.")

                # V250: aplikasi dibatalkan DULU — pagar DB hanya mengizinkan kaitan faktur->NULL bila aplikasinya
                # dibatalkan di transaksi yang sama.
                lepas = await conn.execute(
                    """
                    UPDATE credit_notes
                    SET original_invoice_id = NULL, original_invoice_number = NULL, updated_at = NOW()
                    WHERE id = $1 AND tenant_id = $2 AND original_invoice_id = $3
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                    app["invoice_id"],
                )
                if lepas != "UPDATE 1":
                    raise HTTPException(status_code=409, detail="Penerapan nota kredit berubah bersamaan; muat ulang lalu coba lagi.")

                await segarkan_cache_piutang_faktur(conn, ctx["tenant_id"], app["invoice_id"])

                await conn.execute(
                    """INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
                       VALUES (gen_random_uuid()::text, 'CREDIT_NOTE_UNAPPLIED', 'credit_note', $1, $2, $3, 'api:credit_notes.unapply',
                               jsonb_build_object('invoice_id', $4::text, 'invoice_number', $5::text, 'amount', $6::text,
                                                  'application_id', $7::text, 'reason', $8::text, 'user_id', $9::text), true, now())""",
                    credit_note_id,
                    cn["credit_note_number"],
                    ctx["tenant_id"],
                    str(app["invoice_id"]),
                    app["invoice_number"],
                    str(app["amount_applied"]),
                    str(app["id"]),
                    alasan,
                    str(ctx["user_id"]),
                )

                logger.info(f"Credit note unapplied: {credit_note_id} from invoice {app['invoice_id']}")
                return {
                    "success": True,
                    "message": "Penerapan nota kredit dibatalkan",
                    "data": {
                        "id": str(credit_note_id),
                        "invoice_id": str(app["invoice_id"]),
                        "invoice_number": app["invoice_number"],
                        "amount": float(app["amount_applied"]),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unapplying credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to unapply credit note")


# =============================================================================
# REFUND CREDIT NOTE
# =============================================================================


@router.post("/{credit_note_id}/refund", response_model=CreditNoteResponse)
async def refund_credit_note(
    request: Request, credit_note_id: UUID, body: RefundCreditNoteRequest
):
    """
    Issue a cash refund from credit note.

    Creates journal entry:
    - Dr. Accounts Receivable
    - Cr. Cash/Bank

    Credit note must be in 'posted' or 'partial' status.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE_REFUND:{credit_note_id}",
                )

                # Get credit note
                cn = await conn.fetchrow(
                    """
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] not in ("posted", "partial"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot refund credit note with status '{cn['status']}'",
                    )

                # Check remaining
                remaining = (
                    cn["total_amount"]
                    - (cn["amount_applied"] or 0)
                    - (cn["amount_refunded"] or 0)
                )

                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Refund amount ({body.amount}) exceeds remaining balance ({remaining})",
                    )

                # Validate account — resolve from bank_account_id if provided
                if body.bank_account_id:
                    bank_acc = await conn.fetchrow(
                        "SELECT coa_id FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                        UUID(body.bank_account_id),
                        ctx["tenant_id"],
                    )
                    if not bank_acc:
                        raise HTTPException(
                            status_code=400, detail="Bank account not found"
                        )
                    resolved_account_id = bank_acc["coa_id"]
                elif body.account_id:
                    resolved_account_id = UUID(body.account_id)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Either account_id or bank_account_id is required",
                    )

                account = await conn.fetchrow(
                    """
                    SELECT id, account_code as code, name FROM chart_of_accounts
                    WHERE id = $1 AND tenant_id = $2
                """,
                    resolved_account_id,
                    ctx["tenant_id"],
                )

                if not account:
                    raise HTTPException(
                        status_code=400, detail="Payment account not found"
                    )

                import uuid as uuid_module

                refund_id = uuid_module.uuid4()
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                # Law 5: Period lock check
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    body.refund_date,
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # Create refund journal
                journal_number = (
                    await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'RF')", ctx["tenant_id"]
                    )
                    or f"RF-{cn['credit_note_number']}"
                )

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'CREDIT_NOTE_REFUND', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.refund_date,
                    f"Refund {cn['credit_note_number']} - {cn['customer_name']}",
                    credit_note_id,
                    str(trace_id),
                    body.amount,
                    ctx["user_id"],
                )

                # Get AR account
                # Law 27: Resolve AR account dynamically
                ar_account_id = await resolve_account_id(
                    conn, ctx["tenant_id"], AR_ACCOUNT_CODE
                )

                # Dr. AR (reverse the credit)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    ar_account_id,
                    body.amount,
                    f"Refund Piutang - {cn['credit_note_number']}",
                )

                # Cr. Cash/Bank
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    UUID(body.account_id),
                    body.amount,
                    f"Pembayaran Refund - {cn['credit_note_number']}",
                )

                # Law 20: Promote DRAFT -> POSTED after all lines inserted
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # Create refund record
                await conn.execute(
                    """
                    INSERT INTO credit_note_refunds (
                        id, tenant_id, credit_note_id, amount, refund_date,
                        payment_method, account_id, bank_account_id,
                        reference, notes, journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                    refund_id,
                    ctx["tenant_id"],
                    credit_note_id,
                    body.amount,
                    body.refund_date,
                    body.payment_method,
                    UUID(body.account_id),
                    UUID(body.bank_account_id) if body.bank_account_id else None,
                    body.reference,
                    body.notes,
                    journal_id,
                    ctx["user_id"],
                )

                # Status will be updated by trigger
                logger.info(
                    f"Credit note refunded: {credit_note_id}, amount={body.amount}"
                )

                return {
                    "success": True,
                    "message": "Refund issued successfully",
                    "data": {
                        "id": str(credit_note_id),
                        "refund_id": str(refund_id),
                        "journal_id": str(journal_id),
                        "amount": body.amount,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error refunding credit note {credit_note_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to issue refund")


# =============================================================================
# VOID CREDIT NOTE
# =============================================================================


@router.post("/{credit_note_id}/void", response_model=CreditNoteResponse)
async def void_credit_note(
    request: Request, credit_note_id: UUID, body: VoidCreditNoteRequest
):
    """
    Void a credit note.

    Creates reversal journal entry.
    Credit note must have no applications or refunds.
    """
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        pool = await get_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"CREDIT_NOTE_VOID:{credit_note_id}",
                )

                # Get credit note
                cn = await conn.fetchrow(
                    """
                    SELECT * FROM credit_notes
                    WHERE id = $1 AND tenant_id = $2
                """,
                    credit_note_id,
                    ctx["tenant_id"],
                )

                if not cn:
                    raise HTTPException(status_code=404, detail="Credit note not found")

                if cn["status"] == "void":
                    raise HTTPException(
                        status_code=400, detail="Credit note already voided"
                    )

                if cn["status"] == "draft":
                    # Just delete draft
                    await conn.execute(
                        "DELETE FROM credit_notes WHERE id = $1", credit_note_id
                    )
                    return {
                        "success": True,
                        "message": "Draft credit note deleted",
                        "data": {"id": str(credit_note_id)},
                    }

                # Check for applications or refunds
                if (cn["amount_applied"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void credit note with applications. Reverse applications first.",
                    )

                if (cn["amount_refunded"] or 0) > 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot void credit note with refunds. Reverse refunds first.",
                    )

                    # Law 5: Period lock check (tanggal jurnal pembalik = hari ini, tanggal bisnis)
                hari_ini = await tanggal_dokumen(conn, ctx["tenant_id"])  # t10-tanggal-bisnis
                period_row = await conn.fetchrow(
                    "SELECT status FROM fiscal_periods WHERE tenant_id = $1 AND start_date <= $2 AND end_date >= $2",
                    ctx["tenant_id"],
                    hari_ini,
                )
                if period_row and period_row["status"] != "OPEN":
                    raise HTTPException(
                        status_code=400,
                        detail=f"Periode akuntansi sudah {period_row['status']}",
                    )

                # V312: porsi tertunda NK ini bisa dikembalikan? (409 bila barang terkirim sesudah NK)
                await cn_tertunda.pulihkan_saat_void(conn, ctx["tenant_id"], cn, periksa_saja=True)

                # Create reversal journal if original was posted
                if cn["journal_id"]:
                    import uuid as uuid_module

                    reversal_journal_id = uuid_module.uuid4()

                    # Get original journal lines
                    original_lines = await conn.fetch(
                        """
                        SELECT * FROM journal_lines WHERE journal_id = $1
                    """,
                        cn["journal_id"],
                    )

                    journal_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'RV')", ctx["tenant_id"]
                        )
                        or f"RV-{cn['credit_note_number']}"
                    )

                    # Create reversal header
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $9, $4, 'CREDIT_NOTE', $5, $6, 'DRAFT', $7, $7, $8)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        journal_number,
                        f"Void {cn['credit_note_number']} - {cn['customer_name']}",
                        credit_note_id,
                        cn["journal_id"],
                        cn["total_amount"],
                        ctx["user_id"],
                        hari_ini,  # t10-tanggal-bisnis
                    )

                    # Create reversed lines (swap debit/credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid_module.uuid4(),
                            reversal_journal_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # Swap: original credit becomes debit
                            line["debit"],  # Swap: original debit becomes credit
                            f"Reversal - {line['memo'] or ''}",
                        )

                        # Law 20: Promote DRAFT -> POSTED after all lines inserted
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        reversal_journal_id,
                    )

                    # V312: jurnal pembalik sudah membalik baris Dimuka; allocated_amount dikembalikan (Law 31 G5)
                    await cn_tertunda.pulihkan_saat_void(
                        conn, ctx["tenant_id"], cn, reversal_journal_id=reversal_journal_id
                    )

                    # Mark original journal as reversed
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()  -- Law 2: status asli TETAP POSTED (lihat backend/docs/TEMUAN-rantai-nomor-ganda-20260912.md)
                        WHERE id = $1
                    """,
                        cn["journal_id"],
                        reversal_journal_id,
                    )

                # Unit B: jurnal CN dibalik -> cabang 2 compute_ar_outstanding melepasnya; cache faktur ikut dihitung ulang.
                if cn["original_invoice_id"]:
                    await segarkan_cache_piutang_faktur(conn, ctx["tenant_id"], cn["original_invoice_id"])

                # ── Fase 4 (V164): Reverse COGS companion journal + inventory_ledger ──
                companion = await conn.fetchrow(
                    """
                    SELECT id, total_debit FROM journal_entries
                    WHERE tenant_id = $1
                      AND source_type = 'CREDIT_NOTE_COGS'
                      AND source_id = $2
                      AND status = 'POSTED'
                      AND reversed_by_id IS NULL
                    LIMIT 1
                    """,
                    ctx["tenant_id"],
                    credit_note_id,
                )
                if companion:
                    import uuid as uuid_module2

                    companion_rev_id = uuid_module2.uuid4()
                    companion_rev_number = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'RV-COGS-CN')",
                            ctx["tenant_id"],
                        )
                        or f"RV-COGS-CN-{cn['credit_note_number']}"
                    )
                    companion_orig_lines = await conn.fetch(
                        "SELECT * FROM journal_lines WHERE journal_id = $1",
                        companion["id"],
                    )
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, reversal_of_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $9, $4,
                                  'CREDIT_NOTE_COGS', $5, $6, 'DRAFT', $7, $7, $8)
                        """,
                        companion_rev_id,
                        ctx["tenant_id"],
                        companion_rev_number,
                        f"Void COGS companion {cn['credit_note_number']}",
                        credit_note_id,
                        companion["id"],
                        companion["total_debit"],
                        ctx["user_id"],
                        hari_ini,  # t10-tanggal-bisnis
                    )
                    for idx, line in enumerate(companion_orig_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (
                                id, journal_id, line_number, account_id, debit, credit, memo
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                            """,
                            uuid_module2.uuid4(),
                            companion_rev_id,
                            idx,
                            line["account_id"],
                            line["credit"],  # swap
                            line["debit"],
                            f"Reversal - {line['memo'] or ''}",
                        )
                    # Law 20: DRAFT -> POSTED
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        companion_rev_id,
                    )
                    # Mark original companion VOID (Law 14 mirror — single reversal)
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()  -- Law 2: status asli TETAP POSTED (lihat backend/docs/TEMUAN-rantai-nomor-ganda-20260912.md)
                        WHERE id = $1
                        """,
                        companion["id"],
                        companion_rev_id,
                    )

                    # Reverse inventory_ledger entries (CREDIT_NOTE source) -> CREDIT_NOTE_VOID
                    from ..services.inventory_helpers import record_inventory_reversal

                    await record_inventory_reversal(
                        conn=conn,
                        tenant_id=ctx["tenant_id"],
                        source_type="CREDIT_NOTE",
                        source_id=credit_note_id,
                        reversal_journal_id=companion_rev_id,
                        created_by=ctx["user_id"],
                        notes_prefix="VOID_CN",
                    )

                # Update credit note status
                await conn.execute(
                    """
                    UPDATE credit_notes
                    SET status = 'void', voided_at = NOW(),
                        voided_by = $2, voided_reason = $3, updated_at = NOW()
                    WHERE id = $1
                """,
                    credit_note_id,
                    ctx["user_id"],
                    body.reason,
                )

                logger.info(f"Credit note voided: {credit_note_id}")

                return {
                    "success": True,
                    "message": "Credit note voided successfully",
                    "data": {"id": str(credit_note_id), "status": "void"},
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding credit note {credit_note_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void credit note")


@router.post("/{credit_note_id}/create-tax-invoice")
async def create_tax_invoice_from_cn(request: Request, credit_note_id: str):
    """Create faktur pajak retur keluaran dari credit note."""
    ctx = get_user_context(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        tid = ctx["tenant_id"]
        await conn.execute(f"SET LOCAL app.tenant_id = '{tid}'")
        cn = await conn.fetchrow(
            "SELECT id, status, tax_invoice_id FROM credit_notes "
            "WHERE id = $1 AND tenant_id = $2",
            credit_note_id,
            ctx["tenant_id"],
        )
        if not cn:
            raise HTTPException(404, "Credit note tidak ditemukan")
        if cn["status"] == "draft":
            raise HTTPException(400, "Credit note belum di-post")
        if cn["tax_invoice_id"]:
            raise HTTPException(400, "Credit note sudah punya faktur pajak")

    # Delegate to main tax-invoices create endpoint via internal HTTP
    import httpx

    auth_header = request.headers.get("authorization", "")
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            "http://localhost:8000/api/tax-invoices",
            json={"source_type": "credit_note", "source_ids": [credit_note_id]},
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            timeout=30.0,
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    return resp.json()
