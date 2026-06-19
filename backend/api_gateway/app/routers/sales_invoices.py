"""
Sales Invoices Router - Faktur Penjualan Management

CRUD endpoints for managing sales invoices with accounting kernel integration.
Handles draft -> posted -> paid lifecycle with AR and journal entry creation.
"""

from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, HTTPException, Request, Query, UploadFile, File
from typing import Optional, Literal
from uuid import UUID
import logging
import asyncpg

from ..schemas.sales_invoices import (
    CreateInvoiceRequest,
    UpdateInvoiceRequest,
    PostInvoiceRequest,
    VoidInvoiceRequest,
    InvoicePaymentCreate,
    InvoiceResponse,
    InvoiceListResponse,
    InvoiceSummaryResponse,
    InvoiceCalculationResponse,
)
from ..services.role_resolver import (
    AccountRole,
    resolve_account_id_by_role,
)
from ..services.role_precondition import assert_required_roles_for_path
from ..utils.idempotency import get_idempotency_key

# Fase C1.1: required role mappings for sales_invoices posting path.
# VAT_OUTPUT is interim-mapped to 2-10300 (Hutang Pajak); see
# docs/MAPPING-ROLE-AKUN-LOCKED.md. Fase D will split into VAT_OUTPUT vs WHT_*.
SALES_INVOICE_REQUIRED_ROLES = [
    AccountRole.AR_TRADE,
    AccountRole.REVENUE_SALES_GOODS,
    AccountRole.COGS_SALES,
    AccountRole.INVENTORY_MERCHANDISE,
    AccountRole.VAT_OUTPUT,
    AccountRole.REVENUE_DEFERRED,
]

# One-time precondition check flag. Audit runs once per process at first
# posting-path call. Rationale: this is an architectural invariant (every
# tenant must be mapped) — repeating per request adds a tenant-table SELECT
# to a hot path. After first successful check the audit is skipped; a
# tenant added later without mapping would still fail loud at
# resolve_account_id_by_role(...) via AccountRoleUnmappedError.
_precondition_checked_tenants: set = set()


async def _ensure_role_preconditions(pool, tenant_id):
    """Run the role-mapping precondition for the ACTING tenant.

    FIX_ROLE_PRECOND_PER_TENANT (2026-06-16): scope to the tenant performing the
    post. Previously this audited ALL tenants and cached one global flag, so a
    single misconfigured tenant (e.g. a leftover test tenant lacking role
    mappings) failed-loud and blocked posting for EVERY tenant. Now we only audit
    the acting tenant and cache the pass per-tenant.

    Fails loud (PreconditionFailedError) if THIS tenant lacks any required role.
    """
    if tenant_id in _precondition_checked_tenants:
        return
    await assert_required_roles_for_path(
        pool, "sales_invoices", SALES_INVOICE_REQUIRED_ROLES, tenant_id=tenant_id
    )
    _precondition_checked_tenants.add(tenant_id)


logger = logging.getLogger(__name__)
router = APIRouter()

# Connection pool (initialized on first request)


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

    try:
        user_uuid = UUID(user_id) if user_id else None
    except (ValueError, TypeError):
        user_uuid = None
    return {"tenant_id": tenant_id, "user_id": user_uuid}


async def check_period_is_open(conn, tenant_id: str, transaction_date) -> None:
    """Check if the accounting period for the transaction date is open."""
    period = await conn.fetchrow(
        """
        SELECT id, period_name, status FROM fiscal_periods
        WHERE tenant_id = $1 AND $2 BETWEEN start_date AND end_date
        ORDER BY start_date DESC LIMIT 1
        """,
        tenant_id,
        transaction_date,
    )

    if period and period["status"] in ("CLOSED", "LOCKED"):
        period_name = period["period_name"]
        period_status = period["status"].lower()
        raise HTTPException(
            status_code=403,
            detail=f"Cannot post to {period_status} period ({period_name})",
        )


def _d(v) -> Decimal:
    """Convert any numeric to Decimal for PSAK/IFRS precision."""
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal("0")


def _r2(v: Decimal) -> float:
    """Round Decimal to 2 decimal places, return float for JSON/DB."""
    return float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_item_totals(item: dict) -> dict:
    """Calculate line item totals with Decimal precision (PSAK/IFRS)."""
    qty = _d(item["quantity"])
    price = _d(item["unit_price"])
    subtotal = qty * price

    discount = _d(item.get("discount_amount", 0))
    if item.get("discount_percent", 0) > 0:
        discount = subtotal * _d(item["discount_percent"]) / Decimal("100")

    after_discount = subtotal - discount
    tax_amount = Decimal("0")
    if item.get("tax_rate", 0) > 0:
        tax_amount = after_discount * _d(item["tax_rate"]) / Decimal("100")

    total = after_discount + tax_amount

    return {
        **item,
        "subtotal": _r2(subtotal),
        "discount_amount": _r2(discount),
        "tax_amount": _r2(tax_amount),
        "total": _r2(total),
    }


# =============================================================================
# HEALTH CHECK
# =============================================================================
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "sales-invoices"}


# =============================================================================
# SUMMARY
# =============================================================================
@router.get("/summary", response_model=InvoiceSummaryResponse)
async def get_invoice_summary(request: Request):
    """Get invoice summary statistics."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Pure Ledger: Summary via compute_ar_outstanding() DB function
            query = """
                WITH ar_fn AS (
                    SELECT invoice_id, outstanding
                    FROM compute_ar_outstanding($1)
                )
                SELECT
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1) as total_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'draft') as draft_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'posted') as posted_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'partial') as partial_count,
                    (SELECT COUNT(*) FROM sales_invoices WHERE tenant_id = $1 AND status = 'paid') as paid_count,
                    (SELECT COUNT(*) FROM sales_invoices si3
                     LEFT JOIN ar_fn aw ON aw.invoice_id = si3.id
                     WHERE si3.tenant_id = $1
                       AND (si3.status = 'overdue' OR (si3.status IN ('posted', 'partial') AND si3.due_date < CURRENT_DATE))
                       AND aw.outstanding > 0
                    ) as overdue_count,
                    COALESCE((SELECT SUM(outstanding) FROM ar_fn), 0) as total_outstanding,
                    COALESCE((
                        SELECT SUM(aw.outstanding)
                        FROM ar_fn aw
                        JOIN sales_invoices si3 ON si3.id = aw.invoice_id
                        WHERE si3.status = 'overdue'
                           OR (si3.status IN ('posted', 'partial') AND si3.due_date < CURRENT_DATE)
                    ), 0) as total_overdue
            """
            row = await conn.fetchrow(query, ctx["tenant_id"])

            return {
                "success": True,
                "data": {
                    "status": "draft",
                    "total_count": row["total_count"],
                    "draft_count": row["draft_count"],
                    "posted_count": row["posted_count"],
                    "partial_count": row["partial_count"],
                    "paid_count": row["paid_count"],
                    "overdue_count": row["overdue_count"],
                    "total_outstanding": float(row["total_outstanding"]),
                    "total_overdue": float(row["total_overdue"]),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get summary")


# =============================================================================
# OUTSTANDING SUMMARY (for desktop dashboard)
# =============================================================================
@router.get("/outstanding-summary")
async def get_outstanding_summary(request: Request):
    """Get AR outstanding summary — total, overdue vs current, customer count."""
    try:
        ctx = get_user_context(request)
        tenant_id = ctx["tenant_id"]
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            query = """
                WITH ar AS (
                    SELECT invoice_id, customer_id, outstanding, due_date
                    FROM compute_ar_outstanding($1)
                )
                SELECT
                    COALESCE(SUM(outstanding), 0) AS total_outstanding,
                    COALESCE(SUM(CASE WHEN due_date < CURRENT_DATE THEN outstanding ELSE 0 END), 0) AS overdue_amount,
                    COALESCE(SUM(CASE WHEN due_date >= CURRENT_DATE THEN outstanding ELSE 0 END), 0) AS current_amount,
                    COUNT(CASE WHEN due_date < CURRENT_DATE THEN 1 END) AS overdue_count,
                    COUNT(CASE WHEN due_date >= CURRENT_DATE THEN 1 END) AS current_count,
                    COUNT(DISTINCT customer_id) AS customer_count
                FROM ar
            """
            row = await conn.fetchrow(query, tenant_id)

            # Fix A: per-customer aggregation for deterministic AR rollup intent.
            # Iron Law 1: numbers come from compute_ar_outstanding() (journal-derived).
            # Iron Law 25: keep Decimal precision via str() serialization.
            by_customer_query = """
                WITH ar AS (
                    SELECT customer_id, customer_name, invoice_id, outstanding
                    FROM compute_ar_outstanding($1)
                )
                SELECT
                    ar.customer_id,
                    -- Group by customer_id only; resolve display name from
                    -- master (customers.nama) first, then fall back to any
                    -- snapshot name on the invoice. Avoids duplicate rows when
                    -- legacy invoice snapshots disagree with current master.
                    COALESCE(MAX(c.nama), MAX(ar.customer_name), '(Tanpa Pelanggan)') AS name,
                    COUNT(ar.invoice_id) AS invoice_count,
                    COALESCE(SUM(ar.outstanding), 0) AS total_outstanding
                FROM ar
                LEFT JOIN customers c
                       ON c.id = ar.customer_id AND c.tenant_id = $1
                GROUP BY ar.customer_id
                HAVING COALESCE(SUM(ar.outstanding), 0) > 0
                ORDER BY total_outstanding DESC
            """
            by_customer_rows = await conn.fetch(by_customer_query, tenant_id)
            by_customer = [
                {
                    "customer_id": str(r["customer_id"])
                    if r["customer_id"] is not None
                    else None,
                    "name": r["name"],
                    "count": int(r["invoice_count"]),
                    "total_outstanding": str(r["total_outstanding"]),
                }
                for r in by_customer_rows
            ]

            return {
                "success": True,
                "data": {
                    "total_outstanding": float(row["total_outstanding"]),
                    "overdue_amount": float(row["overdue_amount"]),
                    "current_amount": float(row["current_amount"]),
                    "overdue_count": int(row["overdue_count"]),
                    "current_count": int(row["current_count"]),
                    "customer_count": int(row["customer_count"]),
                    "by_customer": by_customer,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting outstanding summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get outstanding summary")


# =============================================================================
# CALCULATE (Preview without saving)
# =============================================================================
@router.post("/calculate", response_model=InvoiceCalculationResponse)
async def calculate_invoice(request: Request, body: CreateInvoiceRequest):
    """Preview invoice calculation without saving."""
    try:
        ctx = get_user_context(request)  # noqa: F841  # pre-existing: kept for auth side-effect

        # Calculate each item
        calculated_items = []
        subtotal = 0
        total_item_discount = 0
        total_tax = 0

        for i, item in enumerate(body.items):
            calc = calculate_item_totals(item.model_dump())
            calc["line_number"] = i + 1
            calculated_items.append(calc)
            subtotal += calc["subtotal"]
            total_item_discount += calc["discount_amount"]
            total_tax += calc["tax_amount"]

        # Invoice-level discount
        invoice_discount = body.discount_amount
        if body.discount_percent > 0:
            invoice_discount = _r2(
                _d(subtotal) * _d(body.discount_percent) / Decimal("100")
            )

        # Total
        total_amount = subtotal - total_item_discount - invoice_discount + total_tax

        return {
            "success": True,
            "data": {
                "status": "draft",
                "subtotal": subtotal,
                "discount_amount": total_item_discount + invoice_discount,
                "tax_amount": total_tax,
                "total_amount": total_amount,
                "items": calculated_items,
            },
        }

    except Exception as e:
        logger.error(f"Error calculating invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to calculate invoice")


# =============================================================================
# LIST INVOICES
# =============================================================================
@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(
        None, description="Search invoice number or customer"
    ),
    status: Optional[str] = Query(None, description="Filter by status"),
    customer_id: Optional[str] = Query(None, description="Filter by customer"),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    sort_by: Literal["invoice_date", "due_date", "total_amount", "created_at"] = Query(
        "created_at"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    amount_min: Optional[float] = Query(None, description="Minimum amount filter"),
    amount_max: Optional[float] = Query(None, description="Maximum amount filter"),
):
    """List invoices with search, filtering, and pagination."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            conditions = ["si.tenant_id = $1"]
            params = [ctx["tenant_id"]]
            param_idx = 2

            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(si.invoice_number ILIKE ${param_idx} OR si.customer_name ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(si.invoice_number ILIKE ${param_idx} OR si.customer_name ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            # Status filter (with dynamic overdue calculation like bills)
            if status:
                if status == "unpaid":
                    # Exclude draft, void, AND paid — for piutang queries (outstanding > 0 only)
                    conditions.append("si.status IN ('posted', 'partial')")
                elif status == "active":
                    # Exclude draft & void
                    conditions.append("si.status NOT IN ('draft', 'void')")
                elif status == "overdue":
                    # Overdue = posted/partial + past due + has outstanding via DB function
                    conditions.append(
                        "(si.status IN ('posted', 'partial') AND si.due_date < CURRENT_DATE"
                        " AND si.id IN (SELECT invoice_id FROM compute_ar_outstanding($1) WHERE outstanding > 0))"
                    )
                else:
                    conditions.append(f"si.status = ${param_idx}")
                    params.append(status)
                    param_idx += 1

            if customer_id:
                # Bug #1.5: customer_id column is TEXT, not UUID — drop cast
                conditions.append(f"si.customer_id = ${param_idx}")
                params.append(customer_id)
                param_idx += 1

            if start_date:
                conditions.append(f"si.invoice_date >= ${param_idx}::date")
                params.append(start_date)
                param_idx += 1

            if end_date:
                conditions.append(f"si.invoice_date <= ${param_idx}::date")
                params.append(end_date)
                param_idx += 1

            # Amount range filter
            if amount_min is not None:
                conditions.append(f"si.total_amount >= ${param_idx}")
                params.append(amount_min)
                param_idx += 1
            if amount_max is not None:
                conditions.append(f"si.total_amount <= ${param_idx}")
                params.append(amount_max)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            valid_sorts = {
                "invoice_date": "si.invoice_date",
                "due_date": "si.due_date",
                "total_amount": "si.total_amount",
                "created_at": "si.created_at",
            }
            sort_field = valid_sorts.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order == "desc" else "ASC"

            # Count
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM sales_invoices si WHERE {where_clause}", *params
            )

            # Pure Ledger: derive amount_paid via compute_ar_outstanding() DB function
            query = f"""
                SELECT si.id, si.invoice_number, si.customer_id, si.customer_name,
                       si.invoice_date, si.due_date, si.total_amount,
                       -- B1 FIX_AR_LIST_DRAFTVOID (2026-06-19): mirror DETAIL endpoint.
                       -- compute_ar_outstanding excludes draft/void -> LEFT JOIN NULL ->
                       -- COALESCE 0 -> journal_paid=total (false "fully paid"). Gate by status.
                       CASE WHEN si.status IN (draft,void) THEN 0
                            ELSE si.total_amount - COALESCE(ar_fn.outstanding, 0)
                       END as journal_paid,
                       si.status, si.operational_status, si.accounting_status, si.created_at
                FROM sales_invoices si
                LEFT JOIN compute_ar_outstanding($1) ar_fn ON ar_fn.invoice_id = si.id
                WHERE {where_clause}
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])
            rows = await conn.fetch(query, *params)

            items = [
                {
                    "id": str(row["id"]),
                    "invoice_number": row["invoice_number"],
                    "customer_id": str(row["customer_id"])
                    if row["customer_id"]
                    else None,
                    "customer_name": row["customer_name"],
                    "invoice_date": row["invoice_date"].isoformat(),
                    "due_date": row["due_date"].isoformat(),
                    "total_amount": row["total_amount"],
                    "amount_paid": int(row["journal_paid"])
                    if row["journal_paid"] is not None
                    else 0,
                    "status": row["status"],
                    "operational_status": row.get("operational_status") or "DRAFT",
                    "accounting_status": row.get("accounting_status") or "UNPOSTED",
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing invoices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list invoices")


# =============================================================================
# GET INVOICE DETAIL
# =============================================================================
@router.get("/{invoice_id}")
async def get_invoice(request: Request, invoice_id: UUID):
    """Get invoice detail with items and payments."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get invoice
            invoice = await conn.fetchrow(
                """
                SELECT * FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Get items
            items = await conn.fetch(
                """
                SELECT * FROM sales_invoice_items
                WHERE invoice_id = $1
                ORDER BY line_number
            """,
                invoice_id,
            )

            # Get payments from receive_payments (NOT deprecated sales_invoice_payments)
            payments = await conn.fetch(
                """
                SELECT rp.id, rp.payment_number, rp.total_amount AS amount,
                       rp.payment_date, rp.payment_method,
                       rp.bank_account_id, rp.reference_number AS reference,
                       rp.notes, rp.journal_id, rp.created_at, rp.status,
                       rp.posted_at,
                       ba.account_name AS bank_account_name,
                       COALESCE(u_created.name, u_created.fullname, u_created.email) AS created_by_name,
                       COALESCE(u_posted.name, u_posted.fullname, u_posted.email) AS posted_by_name
                FROM receive_payment_allocations rpa
                JOIN receive_payments rp ON rp.id = rpa.payment_id
                LEFT JOIN bank_accounts ba ON ba.id = rp.bank_account_id
                LEFT JOIN "User" u_created ON u_created.id = rp.created_by::text
                LEFT JOIN "User" u_posted ON u_posted.id = rp.posted_by::text
                WHERE rpa.invoice_id = $1
                  AND rp.tenant_id = $2
                  AND rp.status = 'posted'
                ORDER BY rp.payment_date
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            # FIX_AR_HERO_SETTLED (2026-06-15): credit notes / retur that settle this
            # invoice are NOT receive_payments — surface them so the settlement history
            # is complete (else 75k of payments shows on a 125k invoice marked paid,
            # with the 50k retur invisible). Amount = the CN journal's RECEIVABLE credit.
            applied_credits = await conn.fetch(
                """
                SELECT cn.id, cn.credit_note_number, cn.credit_note_date, cn.reason,
                       COALESCE(SUM(jl.credit), 0) AS amount
                FROM credit_notes cn
                JOIN journal_entries je
                  ON je.source_id::uuid = cn.id AND je.source_type = 'CREDIT_NOTE'
                JOIN journal_lines jl ON jl.journal_id = je.id
                JOIN chart_of_accounts coa
                  ON coa.id = jl.account_id AND coa.account_type = 'RECEIVABLE'
                WHERE cn.tenant_id = $2 AND cn.status = 'posted'
                  AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                  AND jl.credit > 0
                  AND (
                      cn.original_invoice_id = $1
                      OR cn.id IN (
                          SELECT credit_note_id FROM credit_note_applications
                          WHERE invoice_id = $1
                      )
                  )
                GROUP BY cn.id, cn.credit_note_number, cn.credit_note_date, cn.reason
                ORDER BY cn.credit_note_date
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            # Pure Ledger: derive amount_paid via compute_ar_outstanding() DB function
            ar_row = await conn.fetchrow(
                """
                SELECT paid_amount, outstanding
                FROM compute_ar_outstanding($1)
                WHERE invoice_id = $2
            """,
                ctx["tenant_id"],
                invoice_id,
            )
            # FIX_AR_HERO_SETTLED (2026-06-15): derive paid/due from ledger truth.
            # No row from compute_ar_outstanding means either fully settled (outstanding
            # 0 — incl. settlement via credit note/retur) OR draft/void. Distinguish by
            # status: only draft/void are unpaid. Do NOT gate "fully paid" on cached
            # status=='paid' — a stale cache (e.g. 'partial') otherwise shows DITERIMA 0
            # / SISA = full on an invoice that the ledger says is settled.
            if ar_row:
                journal_amount_paid = float(
                    invoice["total_amount"] - ar_row["outstanding"]
                )
                _amount_due = float(ar_row["outstanding"])
            elif invoice["status"] in ("draft", "void"):
                journal_amount_paid = 0.0
                _amount_due = float(invoice["total_amount"] or 0)
            else:
                journal_amount_paid = float(invoice["total_amount"])
                _amount_due = 0.0

            return {
                "success": True,
                "data": {
                    "status": invoice["status"],
                    "id": str(invoice["id"]),
                    "invoice_number": invoice["invoice_number"],
                    "customer_id": str(invoice["customer_id"])
                    if invoice["customer_id"]
                    else None,
                    "customer_name": invoice["customer_name"],
                    "invoice_date": invoice["invoice_date"].isoformat(),
                    "due_date": invoice["due_date"].isoformat(),
                    "ref_no": invoice["ref_no"],
                    "notes": invoice["notes"],
                    "subtotal": invoice["subtotal"],
                    "discount_percent": float(invoice["discount_percent"] or 0),
                    "discount_amount": invoice["discount_amount"],
                    "tax_rate": float(invoice["tax_rate"] or 0),
                    "tax_amount": invoice["tax_amount"],
                    "total_amount": invoice["total_amount"],
                    "amount_paid": journal_amount_paid,
                    # FIX_AR_HERO_SETTLED: amount_due + remaining_amount from ledger
                    # truth (_amount_due). remaining_amount is the field the FE hero
                    # reads first for SISA.
                    "amount_due": _amount_due,
                    "remaining_amount": _amount_due,
                    "status": invoice["status"],  # noqa: F601  # pre-existing duplicate key
                    "operational_status": invoice.get("operational_status") or "DRAFT",
                    "accounting_status": invoice.get("accounting_status") or "UNPOSTED",
                    "items": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item.get("unit"),
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"],
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"],
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                            "batch_id": str(item["batch_id"])
                            if item.get("batch_id")
                            else None,
                            "batch_no": item.get("batch_no"),
                            "exp_date": item["exp_date"].isoformat()
                            if item.get("exp_date")
                            else None,
                        }
                        for item in items
                    ],
                    "lines": [
                        {
                            "id": str(item["id"]),
                            "item_id": str(item["item_id"])
                            if item["item_id"]
                            else None,
                            "item_code": item["item_code"],
                            "description": item["description"],
                            "quantity": float(item["quantity"]),
                            "unit": item.get("unit"),
                            "unit_price": item["unit_price"],
                            "discount_percent": float(item["discount_percent"] or 0),
                            "discount_amount": item["discount_amount"],
                            "tax_code": item["tax_code"],
                            "tax_rate": float(item["tax_rate"] or 0),
                            "tax_amount": item["tax_amount"],
                            "subtotal": item["subtotal"],
                            "total": item["total"],
                            "line_number": item["line_number"],
                            "batch_id": str(item["batch_id"])
                            if item.get("batch_id")
                            else None,
                            "batch_no": item.get("batch_no"),
                            "exp_date": item["exp_date"].isoformat()
                            if item.get("exp_date")
                            else None,
                        }
                        for item in items
                    ],
                    "payments": [
                        {
                            "id": str(p["id"]),
                            "payment_number": p.get("payment_number") or "-",
                            "amount": float(p["amount"] or 0),
                            "payment_date": p["payment_date"].isoformat()
                            if p["payment_date"]
                            else None,
                            "payment_method": p.get("payment_method"),
                            "bank_account_id": str(p["bank_account_id"])
                            if p.get("bank_account_id")
                            else None,
                            "bank_account_name": p.get("bank_account_name"),
                            "reference": p.get("reference"),
                            "notes": p.get("notes"),
                            "journal_id": str(p["journal_id"])
                            if p.get("journal_id")
                            else None,
                            "created_at": p["created_at"].isoformat()
                            if p.get("created_at")
                            else None,
                            "status": p.get("status"),
                            "created_by_name": p.get("created_by_name"),
                            "posted_at": p["posted_at"].isoformat()
                            if p.get("posted_at")
                            else None,
                            "posted_by_name": p.get("posted_by_name"),
                        }
                        for p in payments
                    ],
                    # FIX_AR_HERO_SETTLED: credit notes / retur settling this invoice
                    "applied_credits": [
                        {
                            "id": str(c["id"]),
                            "credit_note_number": c.get("credit_note_number") or "-",
                            "amount": float(c["amount"] or 0),
                            "credit_note_date": c["credit_note_date"].isoformat()
                            if c.get("credit_note_date")
                            else None,
                            "reason": c.get("reason"),
                        }
                        for c in applied_credits
                    ],
                    "ar_id": str(invoice["ar_id"]) if invoice["ar_id"] else None,
                    "journal_id": str(invoice["journal_id"])
                    if invoice["journal_id"]
                    else None,
                    "posted_at": invoice["posted_at"].isoformat()
                    if invoice["posted_at"]
                    else None,
                    "posted_by": str(invoice["posted_by"])
                    if invoice["posted_by"]
                    else None,
                    "voided_at": invoice["voided_at"].isoformat()
                    if invoice["voided_at"]
                    else None,
                    "voided_reason": invoice["voided_reason"],
                    "created_at": invoice["created_at"].isoformat(),
                    "updated_at": invoice["updated_at"].isoformat(),
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get invoice")


# =============================================================================
# CREATE INVOICE (Draft)
# =============================================================================


# =============================================================================
# INTERNAL HELPER: Post invoice (for auto_post)


# =============================================================================
# HELPERS: Revenue Recognition & Fulfillment (3-Event Model)
# =============================================================================


def canonical_json(data) -> str:
    """Deterministic JSON for payload hashing (v1.3 spec)."""
    import json as _json
    from decimal import Decimal as _Dec

    def _norm(obj):
        if isinstance(obj, dict):
            return {k: _norm(obj[k]) for k in sorted(obj)}
        elif isinstance(obj, list):
            return [_norm(x) for x in obj]
        elif isinstance(obj, _Dec):
            return format(obj, "f")
        else:
            return obj

    return _json.dumps(_norm(data), separators=(",", ":"), ensure_ascii=False)


def _r2d(v) -> Decimal:
    """Round to 2 decimals, returning Decimal (NOT float). Use instead of _r2 for DB writes."""
    if not isinstance(v, Decimal):
        v = Decimal(str(v)) if v else Decimal("0")
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _resolve_unearned_revenue(conn, tenant_id: str):
    """Resolve Pendapatan Diterima Dimuka via REVENUE_DEFERRED role (Fase C1.1 addendum).

    REVENUE_DEFERRED is promoted to TIER 1 — it is the core contract liability
    of the 3-event PSAK 72 model (V137): billing credits this; revenue debits this.
    Seeded by V152 across all tenants -> 2-10750.
    """
    return await resolve_account_id_by_role(
        conn, tenant_id, AccountRole.REVENUE_DEFERRED
    )


async def _update_invoice_fulfillment_status(conn, invoice_id, tenant_id):
    """Recompute fulfillment_status and revenue_status from line items."""
    stats = await conn.fetchrow(
        """
        SELECT SUM(quantity) AS total_qty, SUM(fulfilled_qty) AS total_fulfilled,
               SUM(allocated_amount) AS total_allocated, SUM(recognized_amount) AS total_recognized
        FROM sales_invoice_items WHERE invoice_id = $1
    """,
        invoice_id,
    )
    total_qty = Decimal(str(stats["total_qty"] or 0))
    total_fulfilled = Decimal(str(stats["total_fulfilled"] or 0))
    total_allocated = Decimal(str(stats["total_allocated"] or 0))
    total_recognized = Decimal(str(stats["total_recognized"] or 0))

    if total_qty == 0:
        f_status = "not_applicable"
    elif total_fulfilled >= total_qty:
        f_status = "fulfilled"
    elif total_fulfilled > 0:
        f_status = "partial"
    else:
        f_status = "pending"

    if total_allocated == 0:
        r_status = "not_applicable"
    elif total_recognized >= total_allocated:
        r_status = "recognized"
    elif total_recognized > 0:
        r_status = "partial"
    else:
        r_status = "deferred"

    await conn.execute(
        """
        UPDATE sales_invoices SET fulfillment_status=$1, revenue_status=$2,
            total_fulfilled_qty=$3, total_recognized_amount=$4
        WHERE id=$5 AND tenant_id=$6
    """,
        f_status,
        r_status,
        str(total_fulfilled),
        str(total_recognized),
        invoice_id,
        tenant_id,
    )


async def _execute_fulfillment(
    conn,
    tenant_id,
    user_id,
    invoice,
    items_to_fulfill,
    warehouse_id,
    fulfillment_date,
    recognize_revenue=True,
    idempotency_key=None,
    notes=None,
    payload_hash=None,
):
    """
    Core shared fulfillment function for 3-Event Revenue Recognition.
    Called by both auto-fulfill (posting) and manual /fulfill endpoint.

    items_to_fulfill: list of {"invoice_item_id": uuid, "quantity": Decimal, "batch_id": uuid|None}
    """
    import uuid
    from datetime import date as dt_date

    invoice_id = invoice["id"]
    invoice_number = invoice["invoice_number"]
    today = dt_date.today()
    year_month_str = today.strftime("%y%m")

    total_cogs = Decimal("0")
    total_revenue = Decimal("0")
    cogs_items = []
    fulfillment_item_ids = []

    # -- Per-item loop ---------------------------------------------------------
    for req in items_to_fulfill:
        inv_item_id = req["invoice_item_id"]
        req_qty = Decimal(str(req["quantity"]))

        # 1. Lock invoice item row
        inv_item = await conn.fetchrow(
            """
            SELECT id, item_id, description, quantity, fulfilled_qty,
                   allocated_amount, recognized_amount
            FROM sales_invoice_items WHERE id=$1 FOR UPDATE
        """,
            inv_item_id,
        )
        if not inv_item:
            raise HTTPException(404, f"Invoice item {inv_item_id} not found")

        product_id = inv_item["item_id"]
        description = inv_item["description"] or ""
        quantity_total = Decimal(str(inv_item["quantity"]))
        fulfilled_so_far = Decimal(str(inv_item["fulfilled_qty"] or 0))
        allocated = Decimal(str(inv_item["allocated_amount"] or 0))
        recognized_so_far = Decimal(str(inv_item["recognized_amount"] or 0))

        # 2. Check remaining qty
        remaining_qty = quantity_total - fulfilled_so_far
        if req_qty > remaining_qty:
            raise HTTPException(
                409, f"Sisa qty {description} hanya {remaining_qty}, diminta {req_qty}"
            )

        # 3. Availability gate ONLY (read-only).
        # warehouse_stock is a DERIVED CACHE owned by the AFTER-INSERT trigger
        # on inventory_ledger (trg_update_warehouse_stock). The ledger insert in
        # step 7 below decrements warehouse_stock via that trigger. We MUST NOT
        # manually UPDATE warehouse_stock here or the stock double-decrements
        # (inventory Rule 1 / dual-ledger; trigger owns warehouse_stock).
        available_stock = await conn.fetchval(
            """
            SELECT COALESCE(quantity, 0)
            FROM warehouse_stock
            WHERE item_id = $1 AND warehouse_id = $2 AND tenant_id = $3
        """,
            product_id,
            warehouse_id,
            tenant_id,
        )
        if available_stock is None or Decimal(str(available_stock)) < req_qty:
            raise HTTPException(409, f"Stok {description} tidak cukup")

        # 4. Get WAC
        avg_cost = await conn.fetchval(
            "SELECT get_weighted_average_cost($1, $2)", tenant_id, product_id
        )
        if not avg_cost or Decimal(str(avg_cost)) == 0:
            raise HTTPException(
                409,
                f"WAC = 0 untuk '{description}'. Catat penerimaan barang terlebih dahulu.",
            )
        avg_cost = Decimal(str(avg_cost))
        line_cogs = _r2d(avg_cost * req_qty)
        total_cogs += line_cogs

        # 5. Revenue rounding safe
        remaining_rev = allocated - recognized_so_far
        if req_qty == remaining_qty:  # last fulfillment -- take remainder
            item_revenue = remaining_rev
        else:
            proportional = allocated * req_qty / quantity_total
            proportional = _r2d(proportional)
            item_revenue = min(proportional, remaining_rev)
        if item_revenue < Decimal("0"):
            item_revenue = Decimal("0")
        total_revenue += item_revenue

        # 6. INSERT fulfillment item (fulfillment_id set after header insert)
        fi_id = uuid.uuid4()
        fulfillment_item_ids.append(
            {
                "id": fi_id,
                "invoice_item_id": inv_item_id,
                "product_id": product_id,
                "quantity": req_qty,
                "unit_cost": avg_cost,
                "total_cost": line_cogs,
                "batch_id": req.get("batch_id"),
                "notes": notes,
            }
        )

        cogs_items.append(
            {
                "item_id": str(product_id),
                "description": description,
                "quantity": req_qty,
                "unit_cost": avg_cost,
                "total_cost": line_cogs,
            }
        )

        # 7. Inventory ledger entry (inline -- not record_inventory_outbound)
        current_balance = await conn.fetchval(
            "SELECT get_inventory_balance($1, $2)", tenant_id, product_id
        )
        new_balance = Decimal(str(current_balance or 0)) - req_qty

        # Get product info for ledger
        product_info = await conn.fetchrow(
            """
            SELECT item_code, nama_produk, track_batches FROM products WHERE id = $1
        """,
            product_id,
        )
        p_code = product_info["item_code"] if product_info else ""
        p_name = product_info["nama_produk"] if product_info else ""

        batch_id = req.get("batch_id")
        # FEFO auto-allocation if needed
        if not batch_id and product_info and product_info.get("track_batches"):
            fefo_batches = await conn.fetch(
                "SELECT * FROM get_available_batches($1, $2, $3, $4, 'FEFO')",
                tenant_id,
                product_id,
                warehouse_id,
                req_qty,
            )
            if fefo_batches:
                total_allocated_batch = sum(
                    Decimal(str(r["quantity_to_use"])) for r in fefo_batches
                )
                if total_allocated_batch >= req_qty:
                    batch_id = fefo_batches[0]["batch_id"]
                else:
                    raise HTTPException(400, f"Stok batch tidak cukup untuk {p_name}")
            else:
                raise HTTPException(400, f"Stok batch tidak cukup untuk {p_name}")

        await conn.execute(
            """
            INSERT INTO inventory_ledger (
                tenant_id, product_id, product_code, product_name,
                movement_type, movement_date,
                source_type, source_id, source_number,
                quantity_in, quantity_out, quantity_balance,
                unit_cost, total_cost, average_cost,
                created_by, notes, warehouse_id, batch_id
            ) VALUES (
                $1, $2, $3, $4,
                'SALE', $5,
                'INVOICE_FULFILLMENT', $6, $7,
                0, $8, $9,
                $10, $11, $10,
                $12, $13, $14, $15
            )
        """,
            tenant_id,
            product_id,
            p_code,
            p_name,
            fulfillment_date,
            invoice_id,
            invoice_number,
            str(req_qty),
            str(new_balance),
            str(avg_cost),
            str(line_cogs),
            user_id,
            f"Fulfillment: {invoice_number}",
            warehouse_id,
            batch_id,
        )

        # Batch deduction (if batch tracking)
        if batch_id:
            bws_row = await conn.fetchrow(
                """
                SELECT available_quantity FROM batch_warehouse_stock
                WHERE batch_id = $1 AND warehouse_id = $2
                FOR UPDATE
            """,
                batch_id,
                warehouse_id,
            )

            if bws_row is None or Decimal(str(bws_row["available_quantity"])) < req_qty:
                available = (
                    Decimal(str(bws_row["available_quantity"])) if bws_row else 0
                )
                raise HTTPException(
                    400,
                    f"Stok batch tidak cukup. Tersedia: {available}, diminta: {req_qty}",
                )

            await conn.execute(
                """
                UPDATE batch_warehouse_stock
                SET quantity = quantity - $3,
                    last_movement_date = NOW(), updated_at = NOW()
                WHERE batch_id = $1 AND warehouse_id = $2
            """,
                batch_id,
                warehouse_id,
                req_qty,
            )

            await conn.execute(
                """
                UPDATE item_batches SET status = 'depleted'
                WHERE id = $1 AND current_quantity <= 0 AND status = 'active'
            """,
                batch_id,
            )

        # 8. Update invoice item fulfilled/recognized
        await conn.execute(
            """
            UPDATE sales_invoice_items
            SET fulfilled_qty = fulfilled_qty + $2,
                recognized_amount = recognized_amount + $3
            WHERE id = $1
        """,
            inv_item_id,
            str(req_qty),
            str(item_revenue),
        )

    # -- After item loop -------------------------------------------------------

    # 1. Generate fulfillment_number via self-healing canonical fn (V176).
    #    journal_date = fulfillment_date so SJ YYMM tracks the document date (BL-05).
    fulfillment_number = await conn.fetchval(
        "SELECT get_next_journal_number($1, $2, $3)",
        tenant_id,
        "SJ",
        fulfillment_date,
    )

    # 2. Idempotency pre-check (safety net for auto-fulfill retries)
    if idempotency_key:
        existing = await conn.fetchrow(
            "SELECT id, payload_hash FROM invoice_fulfillments WHERE tenant_id=$1 AND idempotency_key=$2 AND status='posted'",
            tenant_id,
            idempotency_key,
        )
        if existing:
            if payload_hash and existing["payload_hash"] != payload_hash:
                raise HTTPException(409, "Idempotency key conflict: payload berbeda")
            return {
                "fulfillment_id": str(existing["id"]),
                "message": "Already fulfilled (idempotent)",
            }

    # INSERT invoice_fulfillments header
    fulfillment_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO invoice_fulfillments (
            id, tenant_id, invoice_id, fulfillment_number, fulfillment_date,
            warehouse_id, status, notes, idempotency_key, payload_hash,
            created_by, created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, 'posted', $7, $8, $9, $10, NOW(), NOW())
    """,
        fulfillment_id,
        tenant_id,
        invoice_id,
        fulfillment_number,
        fulfillment_date,
        warehouse_id,
        notes,
        idempotency_key,
        payload_hash,
        user_id,
    )

    # INSERT fulfillment item rows
    for fi in fulfillment_item_ids:
        await conn.execute(
            """
            INSERT INTO invoice_fulfillment_items (
                id, fulfillment_id, invoice_item_id, product_id,
                quantity, unit_cost, total_cost, batch_id, notes
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
            fi["id"],
            fulfillment_id,
            fi["invoice_item_id"],
            fi["product_id"],
            str(fi["quantity"]),
            str(fi["unit_cost"]),
            str(fi["total_cost"]),
            fi["batch_id"],
            fi["notes"],
        )

    # 3. COGS journal -- DRAFT->POSTED (Law 20)
    cogs_journal_id = None
    if total_cogs > Decimal("0"):
        cogs_journal_id = uuid.uuid4()
        cogs_trace_id = str(uuid.uuid4())

        hpp_account_id = await resolve_account_id_by_role(
            conn, tenant_id, AccountRole.COGS_SALES
        )
        inventory_account_id = await resolve_account_id_by_role(
            conn, tenant_id, AccountRole.INVENTORY_MERCHANDISE
        )

        # COGS journal number via self-healing canonical fn (V176);
        # YYMM tracks fulfillment_date (the journal_date).
        cogs_journal_number = await conn.fetchval(
            "SELECT get_next_journal_number($1, $2, $3)",
            tenant_id,
            "COGS",
            fulfillment_date,
        )

        await conn.execute(
            """
            INSERT INTO journal_entries (
                id, tenant_id, journal_number, journal_date,
                description, source_type, source_id, trace_id,
                total_debit, total_credit, status, created_by
            ) VALUES ($1, $2, $3, $4, $5, 'INVOICE_FULFILLMENT', $6, $7, $8, $8, 'DRAFT', $9)
        """,
            cogs_journal_id,
            tenant_id,
            cogs_journal_number,
            fulfillment_date,
            f"HPP Fulfillment {fulfillment_number} - {invoice['customer_name']}",
            invoice_id,
            cogs_trace_id,
            str(total_cogs),
            user_id,
        )

        await conn.execute(
            """
            INSERT INTO journal_lines (
                id, journal_id, account_id, memo, debit, credit, line_number
            ) VALUES
            ($1, $2, $3, 'HPP Barang Dagang', $5, 0, 1),
            ($4, $2, $6, 'Persediaan Barang Dagang', 0, $5, 2)
        """,
            uuid.uuid4(),
            cogs_journal_id,
            hpp_account_id,
            uuid.uuid4(),
            str(total_cogs),
            inventory_account_id,
        )

        # Law 20: DRAFT->POSTED triggers hash chain
        await conn.execute(
            "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
            cogs_journal_id,
        )

    # 4. Revenue journal (if recognize_revenue and total_revenue > 0)
    revenue_journal_id = None
    if recognize_revenue and total_revenue > Decimal("0"):
        revenue_journal_id = uuid.uuid4()
        rev_trace_id = str(uuid.uuid4())

        deferred_rev_account_id = await _resolve_unearned_revenue(conn, tenant_id)
        sales_account_id = await resolve_account_id_by_role(
            conn, tenant_id, AccountRole.REVENUE_SALES_GOODS
        )

        # Revenue-recognition journal number via self-healing canonical fn (V176);
        # YYMM tracks fulfillment_date (the journal_date).
        recog_journal_number = await conn.fetchval(
            "SELECT get_next_journal_number($1, $2, $3)",
            tenant_id,
            "RECOG",
            fulfillment_date,
        )

        await conn.execute(
            """
            INSERT INTO journal_entries (
                id, tenant_id, journal_number, journal_date,
                description, source_type, source_id, trace_id,
                total_debit, total_credit, status, created_by
            ) VALUES ($1, $2, $3, $4, $5, 'INVOICE_REVENUE', $6, $7, $8, $8, 'DRAFT', $9)
        """,
            revenue_journal_id,
            tenant_id,
            recog_journal_number,
            fulfillment_date,
            f"Revenue Recognition {fulfillment_number} - {invoice['customer_name']}",
            invoice_id,
            rev_trace_id,
            str(total_revenue),
            user_id,
        )

        await conn.execute(
            """
            INSERT INTO journal_lines (
                id, journal_id, account_id, memo, debit, credit, line_number
            ) VALUES
            ($1, $2, $3, 'Pendapatan Diterima Dimuka', $5, 0, 1),
            ($4, $2, $6, 'Penjualan', 0, $5, 2)
        """,
            uuid.uuid4(),
            revenue_journal_id,
            deferred_rev_account_id,
            uuid.uuid4(),
            str(total_revenue),
            sales_account_id,
        )

        # Law 20: DRAFT->POSTED
        await conn.execute(
            "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
            revenue_journal_id,
        )

    # 5. UPDATE fulfillment header with journal IDs
    await conn.execute(
        """
        UPDATE invoice_fulfillments
        SET journal_id = $2, revenue_journal_id = $3
        WHERE id = $1
    """,
        fulfillment_id,
        cogs_journal_id,
        revenue_journal_id,
    )

    # 6. Update invoice fulfillment/revenue status
    await _update_invoice_fulfillment_status(conn, invoice_id, tenant_id)

    return {
        "fulfillment_id": str(fulfillment_id),
        "fulfillment_number": fulfillment_number,
        "total_cogs": str(total_cogs),
        "total_revenue": str(total_revenue),
        "cogs_journal_id": str(cogs_journal_id) if cogs_journal_id else None,
        "revenue_journal_id": str(revenue_journal_id) if revenue_journal_id else None,
    }


# =============================================================================
async def _internal_post_invoice(conn, ctx, invoice_id, invoice_number, total_amount):
    """Internal helper to post an invoice within the same transaction.
    3-Event Revenue Recognition (PSAK 72):
    - Billing journal credits Unearned Revenue (2-10700), not Sales (4-10100)
    - Revenue recognized on fulfillment (inventory) or immediately (service)
    """
    import uuid
    from datetime import date as dt_date

    # Law 25: coerce total_amount at boundary. Pydantic schema declares it as
    # float; asyncpg returns numeric(18,2) as Decimal. Mixing causes TypeError
    # in downstream arithmetic (e.g. total_amount - tax_amount at line ~1355).
    total_amount = _d(total_amount)

    # Law 13: Advisory lock - prevent concurrent posting
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1))", f"INVOICE:{invoice_id}"
    )

    # Get invoice data
    invoice = await conn.fetchrow(
        """
        SELECT id, invoice_number, customer_id, customer_name, total_amount,
               tax_amount, subtotal, invoice_date, due_date, warehouse_id,
               recognize_at
        FROM sales_invoices WHERE id = $1 AND tenant_id = $2
        """,
        invoice_id,
        ctx["tenant_id"],
    )

    # Create AR record
    ar_id = await conn.fetchval(
        """
        INSERT INTO accounts_receivable (
            tenant_id, customer_id, customer_name,
            source_type, source_id, invoice_number,
            amount, amount_paid,
            invoice_date, due_date, status
        ) VALUES ($1, $2::uuid, $3, 'INVOICE', $4, $5, $6, 0, $7, $8, 'OPEN')
        RETURNING id
        """,
        ctx["tenant_id"],
        invoice["customer_id"],
        invoice["customer_name"],
        invoice_id,
        invoice_number,
        total_amount,
        invoice["invoice_date"],
        invoice["due_date"],
    )

    # Create journal entry
    journal_id = uuid.uuid4()
    trace_id = str(uuid.uuid4())
    today = dt_date.today()
    year_month_str = today.strftime("%y%m")

    # Billing (INVOICE) journal number via self-healing canonical fn (V176).
    # This was the deadlock site: an inline ON CONFLICT counter that drifted
    # behind the actual JV-{yymm}-NNNN max -> unique violation -> txn rollback.
    # The fn reconciles counter vs actual max (GREATEST) so it self-heals.
    # journal_date = invoice_date so JV YYMM tracks the invoice date (BL-05).
    journal_number = await conn.fetchval(
        "SELECT get_next_journal_number($1, $2, $3)",
        ctx["tenant_id"],
        "JV",
        invoice["invoice_date"],
    )

    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date,
            description, source_type, source_id, trace_id,
            total_debit, total_credit, status, created_by
        ) VALUES ($1, $2, $3, $4, $5, 'INVOICE', $6, $7, $8, $8, 'DRAFT', $9)
        """,
        journal_id,
        ctx["tenant_id"],
        journal_number,
        invoice["invoice_date"],
        f"Faktur Penjualan {invoice_number} - {invoice['customer_name']}",
        invoice_id,
        trace_id,
        total_amount,
        ctx["user_id"],
    )

    # Get AR and Unearned Revenue accounts
    # 3-Event: Credit goes to Pendapatan Diterima Dimuka (2-10700), NOT Penjualan (4-10100)
    ar_account = {
        "id": await resolve_account_id_by_role(
            conn, ctx["tenant_id"], AccountRole.AR_TRADE
        )
    }
    unearned_account = {"id": await _resolve_unearned_revenue(conn, ctx["tenant_id"])}
    # Fase C1.1: VAT_OUTPUT interim → 2-10300 (Hutang Pajak) per LOCKED mapping.
    # Was hardcoded to 2-10600 which only existed for 2/5 tenants (latent bug
    # for anthonius-iwan, ponte-publishing, potus-id on any taxed invoice).
    vat_output_account = {
        "id": await resolve_account_id_by_role(
            conn, ctx["tenant_id"], AccountRole.VAT_OUTPUT
        )
    }

    # Compute subtotal (revenue without tax)
    tax_amount = _d(invoice["tax_amount"] or 0)
    subtotal_amount = total_amount - tax_amount
    line_number = 1

    # Insert journal lines
    # Line 1: Debit AR = total_amount (inclusive of tax)
    if ar_account:
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, $5, 0, $6)
            """,
            uuid.uuid4(),
            journal_id,
            line_number,
            ar_account["id"],
            total_amount,
            f"Piutang - {invoice_number}",
        )
        line_number += 1

    # Line 2: Credit Unearned Revenue = subtotal (WITHOUT tax)
    if unearned_account:
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            """,
            uuid.uuid4(),
            journal_id,
            line_number,
            unearned_account["id"],
            subtotal_amount,
            f"Pendapatan Diterima Dimuka - {invoice_number}",
        )
        line_number += 1

    # Line 3: Credit VAT Output = tax_amount (PPN Keluaran)
    if tax_amount > 0 and vat_output_account:
        await conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
            VALUES ($1, $2, $3, $4, 0, $5, $6)
            """,
            uuid.uuid4(),
            journal_id,
            line_number,
            vat_output_account["id"],
            tax_amount,
            f"PPN Keluaran - {invoice_number}",
        )
        line_number += 1

    # =============================================================
    # Law 20: DRAFT->POSTED triggers hash chain
    await conn.execute(
        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1", journal_id
    )

    # T3: Write document_tax_lines per taxable item
    tax_items_dtl = await conn.fetch(
        """
        SELECT id, tax_code_id, tax_rate, tax_amount, subtotal, discount_amount, dpp
        FROM sales_invoice_items
        WHERE invoice_id = $1 AND COALESCE(tax_amount, 0) > 0
        """,
        invoice_id,
    )
    # Get the PPN Keluaran journal_line_id
    vat_jl_id = None
    if tax_amount > 0 and vat_output_account.get("id"):
        vat_jl_id = await conn.fetchval(
            """
            SELECT id FROM journal_lines
            WHERE journal_id = $1 AND account_id = $2
            LIMIT 1
            """,
            journal_id,
            vat_output_account["id"],
        )
    for ti in tax_items_dtl:
        _tcid = ti["tax_code_id"]
        if not _tcid:
            if float(ti["tax_rate"] or 0) <= 0 or float(ti["tax_amount"] or 0) <= 0:
                continue
            _tcid = await conn.fetchval(
                "SELECT id FROM tax_codes WHERE tenant_id=$1 AND tax_type='ppn' AND rate=$2 AND is_active=true ORDER BY (name ILIKE '%%Keluaran%%') DESC LIMIT 1",
                ctx["tenant_id"],
                ti["tax_rate"],
            )
            if not _tcid:
                continue
        tc_coa = await conn.fetchval(
            "SELECT coa_id FROM tax_codes WHERE id = $1",
            _tcid,
        )
        dpp_val = float(ti["dpp"] or 0) or (
            float(ti["subtotal"] or 0) - float(ti["discount_amount"] or 0)
        )
        await conn.execute(
            """
            INSERT INTO document_tax_lines (
                id, tenant_id, document_type, document_id, line_item_id,
                tax_code_id, direction, base_amount, tax_amount,
                coa_id, journal_line_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            uuid.uuid4(),
            ctx["tenant_id"],
            "SALES_INVOICE",
            invoice_id,
            ti["id"],
            _tcid,
            "output",
            dpp_val,
            float(ti["tax_amount"]),
            tc_coa,
            vat_jl_id,
        )

    # =============================================================
    # PSAK 72 Step 4: Calculate allocated_amount per item
    # =============================================================
    items = await conn.fetch(
        """
        SELECT id, item_id, item_code, description, quantity, unit_price,
               batch_id, batch_no, exp_date, discount_amount
        FROM sales_invoice_items
        WHERE invoice_id = $1
        """,
        invoice_id,
    )

    tax_amount_total = _d(invoice.get("tax_amount", 0) or 0)
    subtotal_after_discount = _d(invoice["total_amount"]) - tax_amount_total

    total_line_subtotals = Decimal("0")
    for itm in items:
        qty = _d(itm["quantity"])
        price = _d(itm["unit_price"])
        line_sub = qty * price
        disc = _d(itm.get("discount_amount", 0))
        line_sub -= disc
        total_line_subtotals += line_sub

    if total_line_subtotals > 0:
        for i, itm in enumerate(items):
            qty = _d(itm["quantity"])
            price = _d(itm["unit_price"])
            line_sub = qty * price - _d(itm.get("discount_amount", 0))
            proportion = line_sub / total_line_subtotals
            alloc = (proportion * subtotal_after_discount).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            items[i] = dict(itm)  # make mutable copy
            items[i]["_allocated"] = alloc
        # Last item absorbs rounding
        sum_others = sum(items[j]["_allocated"] for j in range(len(items) - 1))
        items[-1]["_allocated"] = subtotal_after_discount - sum_others
    else:
        for i, itm in enumerate(items):
            items[i] = dict(itm)
            items[i]["_allocated"] = Decimal("0")

    # Persist allocated_amount
    for itm in items:
        await conn.execute(
            "UPDATE sales_invoice_items SET allocated_amount = $1 WHERE id = $2",
            str(itm["_allocated"]),
            itm["id"],
        )

    # =============================================================
    # 3-Event Path Selection
    # =============================================================
    has_inventory_items = False
    all_have_cost = True
    inventory_items_for_fulfill = []

    for itm in items:
        if not itm.get("item_id"):
            continue
        product = await conn.fetchrow(
            "SELECT id, track_inventory FROM products WHERE tenant_id=$1 AND id=$2",
            ctx["tenant_id"],
            itm["item_id"],
        )
        if not product or not product.get("track_inventory", True):
            continue
        has_inventory_items = True
        avg_cost = await conn.fetchval(
            "SELECT get_weighted_average_cost($1, $2)",
            ctx["tenant_id"],
            itm["item_id"],
        )
        if not avg_cost or avg_cost == 0:
            all_have_cost = False
        else:
            inventory_items_for_fulfill.append(
                {
                    "invoice_item_id": itm["id"],
                    "quantity": itm["quantity"],
                }
            )

    # =============================================================
    # P4 (PSAK-72 revenue-timing policy) — resolve EFFECTIVE policy.
    # Per-invoice recognize_at overrides tenant_config; tenant_config
    # overrides the global default 'invoice' (preserves legacy behavior).
    #   'invoice'  -> recognize at post (auto-fulfill+recognize) — UNCHANGED
    #   'delivery' -> defer; recognize at /fulfill (reuse make-to-order branch)
    # =============================================================
    effective_policy = invoice.get("recognize_at")
    if not effective_policy:
        effective_policy = await conn.fetchval(
            "SELECT revenue_recognition_policy FROM tenant_config WHERE tenant_id=$1",
            ctx["tenant_id"],
        )
    if not effective_policy:
        effective_policy = "invoice"

    # P4 user-visible warnings (e.g. WAC=0 silent-defer). Returned in post result.
    post_warnings = []

    fulfillment_status = "not_applicable"
    revenue_status = "recognized"

    if has_inventory_items and all_have_cost and effective_policy == "delivery":
        # P4 DELIVERY MODE, sell-from-stock: DEFER. Route into the EXISTING
        # make-to-order deferred branch verbatim — billing journal already
        # credited Pendapatan Diterima Dimuka (2-10750/role) above; here we
        # simply do NOT auto-fulfill: no COGS, no INVOICE_REVENUE at post.
        # Recognition (+ COGS) happens at /fulfill (UNTOUCHED path).
        fulfillment_status = "pending"
        revenue_status = "deferred"

    elif has_inventory_items and all_have_cost:
        # SELL-FROM-STOCK: auto-fulfill (zero UX change)
        from hashlib import sha256

        payload_for_hash = canonical_json(
            {
                "warehouse_id": str(invoice.get("warehouse_id", "")),
                "items": sorted(
                    [
                        {"id": str(i["invoice_item_id"]), "qty": str(i["quantity"])}
                        for i in inventory_items_for_fulfill
                    ],
                    key=lambda x: x["id"],
                ),
            }
        )
        p_hash = sha256(payload_for_hash.encode()).hexdigest()

        posting_warehouse_id = invoice.get("warehouse_id")
        if not posting_warehouse_id:
            posting_warehouse_id = await conn.fetchval(
                "SELECT id FROM warehouses WHERE tenant_id=$1 ORDER BY created_at LIMIT 1",
                ctx["tenant_id"],
            )

        await _execute_fulfillment(
            conn,
            ctx["tenant_id"],
            ctx["user_id"],
            dict(invoice),
            inventory_items_for_fulfill,
            posting_warehouse_id,
            invoice["invoice_date"],
            recognize_revenue=True,
            idempotency_key=f"AUTO_FULFILL:{invoice_id}",
            payload_hash=p_hash,
        )
        fulfillment_status = "fulfilled"
        revenue_status = "recognized"

    elif has_inventory_items and not all_have_cost:
        # MAKE-TO-ORDER / degrade-to-defer: billing only, fulfill later.
        # WAC=0 on at least one tracked item -> revenue deferred (cannot post
        # COGS without a cost). P4: surface this to the user (was silent).
        fulfillment_status = "pending"
        revenue_status = "deferred"
        for itm in items:
            if not itm.get("item_id"):
                continue
            avg_cost = await conn.fetchval(
                "SELECT get_weighted_average_cost($1, $2)",
                ctx["tenant_id"],
                itm["item_id"],
            )
            if not avg_cost or avg_cost == 0:
                _desc = itm.get("description") or itm.get("item_code") or "item"
                post_warnings.append(
                    f"Pendapatan ditangguhkan: {_desc} belum punya harga "
                    f"pokok (WAC=0). Catat penerimaan barang dulu, lalu kirim "
                    f"(fulfill) untuk mengakui pendapatan & HPP."
                )

    else:
        # SERVICE: no inventory items, recognize revenue immediately
        total_service_revenue = sum(_d(itm["_allocated"]) for itm in items)
        if total_service_revenue > 0:
            unearned_id2 = await _resolve_unearned_revenue(conn, ctx["tenant_id"])
            revenue_id2 = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.REVENUE_SALES_GOODS
            )
            rev_j_id = uuid.uuid4()
            rev_trace = str(uuid.uuid4())
            # Service revenue-recognition journal number via self-healing
            # canonical fn (V176); YYMM tracks invoice_date (the journal_date).
            rev_jn = await conn.fetchval(
                "SELECT get_next_journal_number($1, $2, $3)",
                ctx["tenant_id"],
                "RECOG",
                invoice["invoice_date"],
            )
            await conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date,
                    description, source_type, source_id, trace_id,
                    total_debit, total_credit, status, created_by
                ) VALUES ($1,$2,$3,$4,$5,'INVOICE_REVENUE',$6,$7,$8,$8,'DRAFT',$9)
                """,
                rev_j_id,
                ctx["tenant_id"],
                rev_jn,
                invoice["invoice_date"],
                f"Revenue Recognition {invoice_number} (Service)",
                invoice_id,
                rev_trace,
                float(total_service_revenue),
                ctx["user_id"],
            )
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1,$2,1,$3,$4,0,$5)
                """,
                uuid.uuid4(),
                rev_j_id,
                unearned_id2,
                float(total_service_revenue),
                f"Dimuka \u2192 Penjualan {invoice_number}",
            )
            await conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                VALUES ($1,$2,2,$3,0,$4,$5)
                """,
                uuid.uuid4(),
                rev_j_id,
                revenue_id2,
                float(total_service_revenue),
                f"Penjualan {invoice_number}",
            )
            await conn.execute(
                "UPDATE journal_entries SET status='POSTED' WHERE id=$1",
                rev_j_id,
            )
            # Update items as fully recognized
            for itm in items:
                await conn.execute(
                    "UPDATE sales_invoice_items SET recognized_amount = allocated_amount WHERE id = $1",
                    itm["id"],
                )
        fulfillment_status = "not_applicable"
        revenue_status = "recognized"

    # Query totals from items
    totals = await conn.fetchrow(
        "SELECT COALESCE(SUM(fulfilled_qty),0) as f, COALESCE(SUM(recognized_amount),0) as r FROM sales_invoice_items WHERE invoice_id=$1",
        invoice_id,
    )

    # Update invoice status (including fulfillment/revenue info)
    await conn.execute(
        """
        UPDATE sales_invoices
        SET status = 'posted', operational_status = 'SENT', accounting_status = 'POSTED',
            ar_id = $1, journal_id = $2, posted_at = NOW(), posted_by = $3,
            fulfillment_status = $5, revenue_status = $6,
            total_fulfilled_qty = $7, total_recognized_amount = $8
        WHERE id = $4
        """,
        ar_id,
        journal_id,
        ctx["user_id"],
        invoice_id,
        fulfillment_status,
        revenue_status,
        float(totals["f"]),
        float(totals["r"]),
    )

    return {
        "journal_id": str(journal_id),
        "ar_id": str(ar_id),
        "journal_number": journal_number,
        "fulfillment_status": fulfillment_status,
        "revenue_status": revenue_status,
        "warnings": post_warnings,
    }


@router.post("", response_model=InvoiceResponse, status_code=201)
async def create_invoice(request: Request, body: CreateInvoiceRequest):
    """Create a new sales invoice as draft."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        # Fase C1.1: fail-loud if any tenant lacks required role mapping
        # (auto-post path inside create posts to AR/REV/COGS/INV/VAT).
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Generate invoice number
                invoice_number = await conn.fetchval(
                    "SELECT generate_sales_invoice_number($1, 'INV')", ctx["tenant_id"]
                )

                # Calculate totals
                subtotal = 0
                total_item_discount = 0
                total_tax = 0
                calculated_items = []

                for i, item in enumerate(body.items):
                    calc = calculate_item_totals(item.model_dump())
                    calc["line_number"] = i + 1
                    calculated_items.append(calc)
                    subtotal += calc["subtotal"]
                    total_item_discount += calc["discount_amount"]
                    total_tax += calc["tax_amount"]

                # Invoice-level discount
                invoice_discount = body.discount_amount
                if body.discount_percent > 0:
                    invoice_discount = _r2(
                        _d(subtotal) * _d(body.discount_percent) / Decimal("100")
                    )

                total_amount = (
                    subtotal - total_item_discount - invoice_discount + total_tax
                )

                # Convert customer_id
                # customer_id is TEXT column, use string directly
                customer_id_str = body.customer_id if body.customer_id else None
                # Validate UUID format if provided
                if customer_id_str:
                    try:
                        UUID(customer_id_str)  # Validate format
                    except ValueError:
                        raise HTTPException(
                            status_code=400, detail="Invalid customer_id format"
                        )

                # BUG-02 fix: Resolve customer_id from customer_name when missing
                if not customer_id_str and body.customer_name:
                    # Exact match first (customers.nama is Bahasa Indonesia column)
                    resolved_id = await conn.fetchval(
                        """SELECT id FROM customers
                           WHERE tenant_id = $1 AND nama = $2
                             AND is_active = true AND deleted_at IS NULL
                           LIMIT 1""",
                        ctx["tenant_id"],
                        body.customer_name,
                    )
                    # Fallback: case-insensitive ILIKE match
                    if not resolved_id:
                        resolved_id = await conn.fetchval(
                            """SELECT id FROM customers
                               WHERE tenant_id = $1 AND nama ILIKE $2
                                 AND is_active = true AND deleted_at IS NULL
                               LIMIT 1""",
                            ctx["tenant_id"],
                            body.customer_name,
                        )
                    if resolved_id:
                        customer_id_str = str(resolved_id)

                # Phase 4 hardening (Iron Law 30 mirror): reject unresolved customer_name.
                # Without this guard, a name without a matching master row produces an
                # invoice with customer_id=NULL — orphan that breaks AR aggregation by
                # customer and silently inflates the customer_name string column.
                # Frontend (CustomerSheet.tsx) is fixed to never POST invoice with
                # unresolved name, but mobile/bot/API integrations may still try.
                if body.customer_name and not customer_id_str:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Pelanggan '{body.customer_name}' tidak ditemukan. "
                            "Buat dulu di modul Pelanggan."
                        ),
                    )

                # Insert invoice
                invoice_id = await conn.fetchval(
                    """
                    INSERT INTO sales_invoices (
                        tenant_id, invoice_number, customer_id, customer_name,
                        invoice_date, due_date, ref_no, notes,
                        subtotal, discount_percent, discount_amount,
                        tax_rate, tax_amount, total_amount,
                        status, created_by, recognize_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'draft', $15, $16)
                    RETURNING id
                """,
                    ctx["tenant_id"],
                    invoice_number,
                    customer_id_str,
                    body.customer_name,
                    body.invoice_date,
                    body.due_date,
                    body.ref_no,
                    body.notes,
                    subtotal,
                    body.discount_percent,
                    invoice_discount,
                    (
                        body.tax_rate
                        if body.tax_rate and float(body.tax_rate) > 0
                        else (
                            max(
                                (float(it.tax_rate or 0) for it in body.items),
                                default=0,
                            )
                        )
                    ),
                    total_tax,
                    total_amount,
                    ctx["user_id"],
                    body.recognize_at,
                )

                # Insert items
                for item in calculated_items:
                    item_uuid = None
                    if item.get("item_id"):
                        try:
                            item_uuid = UUID(item["item_id"])
                        except ValueError:
                            pass

                    # Auto-populate batch_no/exp_date from item_batches if only batch_id provided
                    item_batch_no = item.get("batch_no")
                    item_exp_date = item.get("exp_date")
                    # Parse exp_date string (e.g. "2026-04" or "2026-04-01") to date object
                    if isinstance(item_exp_date, str):
                        try:
                            from datetime import date as _date

                            parts = item_exp_date.split("-")
                            if len(parts) == 2:  # "YYYY-MM" format
                                item_exp_date = _date(int(parts[0]), int(parts[1]), 1)
                            elif len(parts) == 3:  # "YYYY-MM-DD" format
                                item_exp_date = _date(
                                    int(parts[0]), int(parts[1]), int(parts[2])
                                )
                            else:
                                item_exp_date = None
                        except (ValueError, TypeError):
                            item_exp_date = None
                    item_batch_id = item.get("batch_id")
                    if item_batch_id and (not item_batch_no or not item_exp_date):
                        batch_info = await conn.fetchrow(
                            "SELECT batch_number, expiry_date FROM item_batches WHERE id = $1",
                            UUID(item_batch_id)
                            if isinstance(item_batch_id, str)
                            else item_batch_id,
                        )
                        if batch_info:
                            if not item_batch_no:
                                item_batch_no = batch_info["batch_number"]
                            if not item_exp_date:
                                item_exp_date = batch_info["expiry_date"]

                    await conn.execute(
                        """
                        INSERT INTO sales_invoice_items (
                            invoice_id, item_id, item_code, description,
                            quantity, unit, unit_price,
                            discount_percent, discount_amount,
                            tax_code, tax_rate, tax_amount,
                            subtotal, total, line_number,
                            batch_id, batch_no, exp_date,
                            tax_code_id, dpp
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
                    """,
                        invoice_id,
                        item_uuid,
                        item.get("item_code"),
                        item["description"],
                        item["quantity"],
                        item.get("unit"),
                        item["unit_price"],
                        item.get("discount_percent", 0),
                        item["discount_amount"],
                        item.get("tax_code"),
                        item.get("tax_rate", 0),
                        item["tax_amount"],
                        item["subtotal"],
                        item["total"],
                        item["line_number"],
                        UUID(item_batch_id) if item_batch_id else None,
                        item_batch_no,
                        item_exp_date,
                        UUID(item.get("tax_code_id"))
                        if item.get("tax_code_id")
                        else None,
                        item.get("dpp")
                        or (item["subtotal"] - item.get("discount_amount", 0)),
                    )

                logger.info(f"Invoice created: {invoice_id}, number={invoice_number}")

                # If auto_post requested, post the invoice immediately
                if body.auto_post:
                    # Import here to avoid circular import
                    from uuid import UUID as UUID_type

                    # Post the invoice (creates AR, journal, COGS)
                    post_result = await _internal_post_invoice(
                        conn,
                        ctx,
                        UUID_type(str(invoice_id)),
                        invoice_number,
                        total_amount,
                    )

                    return {
                        "success": True,
                        "message": "Invoice created and posted successfully",
                        "data": {
                            "status": "posted",
                            "id": str(invoice_id),
                            "invoice_number": invoice_number,
                            "total_amount": total_amount,
                            "journal_id": post_result.get("journal_id"),
                            "ar_id": post_result.get("ar_id"),
                            "cogs_journal_id": post_result.get("cogs_journal_id"),
                            "total_cogs": post_result.get("total_cogs", 0),
                            "revenue_status": post_result.get("revenue_status"),
                            "fulfillment_status": post_result.get("fulfillment_status"),
                            "warnings": post_result.get("warnings", []),
                        },
                    }

                return {
                    "success": True,
                    "message": "Invoice created successfully",
                    "data": {
                        "status": "draft",
                        "id": str(invoice_id),
                        "invoice_number": invoice_number,
                        "total_amount": float(total_amount),
                        "amount_paid": 0,
                        "customer_id": body.customer_id,
                        "customer_name": body.customer_name,
                        "invoice_date": str(body.invoice_date),
                        "due_date": str(body.due_date),
                        "created_at": None,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating invoice: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create invoice")


# =============================================================================
# UPDATE INVOICE (Draft only)
# =============================================================================
@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    request: Request, invoice_id: UUID, body: UpdateInvoiceRequest
):
    """Update a draft invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Check invoice exists and is draft
            invoice = await conn.fetchrow(
                """
                SELECT id, status FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Guard: Cannot update voided invoices
            if invoice["status"] == "void":
                raise HTTPException(
                    status_code=400, detail="Cannot update voided invoice"
                )

            # Guard: Cannot update non-draft invoices
            if invoice["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot edit posted invoice. Only draft invoices can be edited.",
                )

            async with conn.transaction():
                # If items provided, recalculate
                if body.items is not None:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM sales_invoice_items WHERE invoice_id = $1",
                        invoice_id,
                    )

                    # Calculate and insert new items
                    subtotal = 0
                    total_item_discount = 0
                    total_tax = 0

                    for i, item in enumerate(body.items):
                        calc = calculate_item_totals(item.model_dump())
                        calc["line_number"] = i + 1
                        subtotal += calc["subtotal"]
                        total_item_discount += calc["discount_amount"]
                        total_tax += calc["tax_amount"]

                        item_uuid = None
                        if item.item_id:
                            try:
                                item_uuid = UUID(item.item_id)
                            except ValueError:
                                pass

                        # Auto-populate batch_no/exp_date from item_batches if only batch_id provided
                        item_batch_no = item.batch_no
                        item_exp_date = item.exp_date
                        # Parse exp_date string (e.g. "2026-04" or "2026-04-01") to date object
                        if isinstance(item_exp_date, str):
                            try:
                                from datetime import date as _date

                                parts = item_exp_date.split("-")
                                if len(parts) == 2:
                                    item_exp_date = _date(
                                        int(parts[0]), int(parts[1]), 1
                                    )
                                elif len(parts) == 3:
                                    item_exp_date = _date(
                                        int(parts[0]), int(parts[1]), int(parts[2])
                                    )
                                else:
                                    item_exp_date = None
                            except (ValueError, TypeError):
                                item_exp_date = None
                        item_batch_id = item.batch_id
                        if item_batch_id and (not item_batch_no or not item_exp_date):
                            batch_info = await conn.fetchrow(
                                "SELECT batch_number, expiry_date FROM item_batches WHERE id = $1",
                                UUID(item_batch_id)
                                if isinstance(item_batch_id, str)
                                else item_batch_id,
                            )
                            if batch_info:
                                if not item_batch_no:
                                    item_batch_no = batch_info["batch_number"]
                                if not item_exp_date:
                                    item_exp_date = batch_info["expiry_date"]

                        await conn.execute(
                            """
                            INSERT INTO sales_invoice_items (
                                invoice_id, item_id, item_code, description,
                                quantity, unit, unit_price,
                                discount_percent, discount_amount,
                                tax_code, tax_rate, tax_amount,
                                subtotal, total, line_number,
                                batch_id, batch_no, exp_date,
                                tax_code_id, dpp
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
                        """,
                            invoice_id,
                            item_uuid,
                            item.item_code,
                            item.description,
                            item.quantity,
                            item.unit,
                            item.unit_price,
                            item.discount_percent,
                            calc["discount_amount"],
                            item.tax_code,
                            item.tax_rate,
                            calc["tax_amount"],
                            calc["subtotal"],
                            calc["total"],
                            calc["line_number"],
                            UUID(item_batch_id) if item_batch_id else None,
                            item_batch_no,
                            item_exp_date,
                            UUID(str(item.tax_code_id))
                            if getattr(item, "tax_code_id", None)
                            else None,
                            calc.get("dpp")
                            or (calc["subtotal"] - calc["discount_amount"]),
                        )

                    # Update invoice totals
                    invoice_discount = body.discount_amount or 0
                    if body.discount_percent and body.discount_percent > 0:
                        invoice_discount = _r2(
                            _d(subtotal) * _d(body.discount_percent) / Decimal("100")
                        )

                    total_amount = (
                        subtotal - total_item_discount - invoice_discount + total_tax
                    )

                    await conn.execute(
                        """
                        UPDATE sales_invoices
                        SET subtotal = $2, discount_amount = $3, tax_amount = $4, total_amount = $5
                        WHERE id = $1
                    """,
                        invoice_id,
                        subtotal,
                        invoice_discount,
                        total_tax,
                        total_amount,
                    )

                # Update other fields
                update_data = body.model_dump(exclude_unset=True, exclude={"items"})
                if update_data:
                    updates = []
                    params = []
                    param_idx = 1

                    for field, value in update_data.items():
                        if field == "customer_id" and value:
                            updates.append(f"{field} = ${param_idx}::uuid")
                        else:
                            updates.append(f"{field} = ${param_idx}")
                        params.append(value)
                        param_idx += 1

                    if updates:
                        updates.append("updated_at = NOW()")
                        params.extend([invoice_id, ctx["tenant_id"]])
                        query = f"""
                            UPDATE sales_invoices
                            SET {', '.join(updates)}
                            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                        """
                        await conn.execute(query, *params)

                logger.info(f"Invoice updated: {invoice_id}")

                # Fetch updated invoice for response
                updated = await conn.fetchrow(
                    "SELECT id, invoice_number, total_amount, amount_paid, status, invoice_date, due_date, customer_id, customer_name, created_at FROM sales_invoices WHERE id = $1",
                    invoice_id,
                )
                return {
                    "success": True,
                    "message": "Invoice updated successfully",
                    "data": {
                        "id": str(updated["id"]),
                        "invoice_number": updated["invoice_number"],
                        "total_amount": float(updated["total_amount"] or 0),
                        "amount_paid": float(updated["amount_paid"] or 0),
                        "status": updated["status"] or "draft",
                        "invoice_date": str(updated["invoice_date"])
                        if updated["invoice_date"]
                        else None,
                        "due_date": str(updated["due_date"])
                        if updated["due_date"]
                        else None,
                        "customer_id": str(updated["customer_id"])
                        if updated["customer_id"]
                        else None,
                        "customer_name": updated["customer_name"],
                        "created_at": str(updated["created_at"])
                        if updated["created_at"]
                        else None,
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update invoice")


# =============================================================================
# POST INVOICE (Create AR + Journal Entry + COGS)
# =============================================================================
@router.post("/{invoice_id}/post", response_model=InvoiceResponse)
async def post_invoice(
    request: Request, invoice_id: UUID, body: PostInvoiceRequest = None
):
    """
    Post invoice to accounting (creates AR, journal entry).
    3-Event Revenue Recognition (PSAK 72):
    - Billing journal credits Unearned Revenue
    - Revenue recognized based on fulfillment path
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        # Fase C1.1: fail-loud if any tenant lacks required role mapping.
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            # Check invoice exists and is draft
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, customer_id, customer_name, total_amount,
                       invoice_date, due_date, status, warehouse_id
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] != "draft":
                raise HTTPException(
                    status_code=400, detail="Only draft invoices can be posted"
                )

            # Check if accounting period is open
            await check_period_is_open(conn, ctx["tenant_id"], invoice["invoice_date"])

            async with conn.transaction():
                post_result = await _internal_post_invoice(
                    conn,
                    ctx,
                    invoice_id,
                    invoice["invoice_number"],
                    invoice["total_amount"],
                )

                logger.info(
                    f"Invoice posted: {invoice_id}, AR: {post_result.get('ar_id')}"
                )

                return {
                    "success": True,
                    "message": "Invoice posted successfully",
                    "data": {
                        "id": str(invoice_id),
                        "ar_id": post_result.get("ar_id"),
                        "journal_id": post_result.get("journal_id"),
                        "journal_number": post_result.get("journal_number"),
                        "fulfillment_status": post_result.get("fulfillment_status"),
                        "revenue_status": post_result.get("revenue_status"),
                        "warnings": post_result.get("warnings", []),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error posting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to post invoice")


# =============================================================================
# RECORD PAYMENT
# =============================================================================
@router.post("/{invoice_id}/payments", response_model=InvoiceResponse)
async def record_payment(
    request: Request, invoice_id: UUID, body: InvoicePaymentCreate
):
    """
    Record payment for a sales invoice.
    Creates receive_payments + receive_payment_allocations (ARAP Rule 1).
    Journal source_type='RECEIVE_PAYMENT', source_id=receive_payments.id
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        # Fase C1.1: fail-loud if any tenant lacks required role mapping
        # (payment shortcut posts AR settlement journal).
        await _ensure_role_preconditions(pool, ctx["tenant_id"])

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL app.tenant_id = '{ctx['tenant_id']}'")  # nosec B608

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"INVOICE_PAYMENT:{str(invoice_id)}",
                )

                # Law 14: Idempotency check
                idem_key = get_idempotency_key(
                    request, f"INVOICE_PAYMENT:{invoice_id}:{body.amount}"
                )
                existing_idem = await conn.fetchrow(
                    "SELECT result FROM idempotency_keys WHERE tenant_id = $1 AND key = $2 AND expires_at > NOW()",
                    ctx["tenant_id"],
                    idem_key,
                )
                if existing_idem and existing_idem["result"]:
                    import json as json_mod

                    return json_mod.loads(existing_idem["result"])

                # Fetch invoice (FOR UPDATE)
                invoice = await conn.fetchrow(
                    """
                    SELECT id, invoice_number, customer_id, customer_name,
                           status, total_amount, amount_paid, ar_id
                    FROM sales_invoices
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                """,
                    invoice_id,
                    ctx["tenant_id"],
                )

                if not invoice:
                    raise HTTPException(status_code=404, detail="Invoice not found")
                if invoice["status"] not in ("posted", "partial", "overdue"):
                    raise HTTPException(
                        status_code=400,
                        detail="Invoice must be posted before recording payment",
                    )

                # Law 16: journal-derived remaining (same CTE as original)
                journal_remaining = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE je.status = 'POSTED'
                        AND coa.account_type = 'RECEIVABLE'
                        AND je.tenant_id = $2
                        AND (
                            (je.source_type = 'INVOICE' AND je.source_id = $1)
                            OR (je.source_type IN ('RECEIVE_PAYMENT', 'PAYMENT_RECEIVED') AND EXISTS (
                                SELECT 1 FROM receive_payment_allocations rpa
                                WHERE rpa.invoice_id = $1 AND rpa.payment_id = je.source_id
                            ))
                            OR (je.source_type = 'PAYMENT_RECEIVED'
                                AND je.description LIKE '%%' || (SELECT invoice_number FROM sales_invoices WHERE id = $1) || '%%'
                                AND NOT EXISTS(
                                    SELECT 1 FROM receive_payment_allocations rpa2
                                    WHERE rpa2.payment_id = je.source_id AND rpa2.tenant_id = $2
                                ))
                            OR (je.source_type = 'CREDIT_NOTE' AND EXISTS (
                                SELECT 1 FROM credit_note_applications cna
                                WHERE cna.invoice_id = $1 AND cna.credit_note_id = je.source_id
                            ))
                            OR (je.source_type = 'DEPOSIT_APPLICATION' AND EXISTS (
                                SELECT 1 FROM customer_deposit_applications cda
                                WHERE cda.invoice_id = $1 AND cda.deposit_id = je.source_id
                                -- FIX_P1_DEPOSIT 2026-06-16 OPTION B: drop reversed (un-applied)
                                -- deposit applications so invoice outstanding is restored.
                                AND is_effective_journal(je.id)
                            ))
                            OR (je.source_type = 'INVOICE_REVERSAL' AND je.source_id = $1)
                            OR (je.id IN (
                                SELECT sip.journal_id FROM sales_invoice_payments sip
                                WHERE sip.invoice_id = $1
                            ))
                        )
                """,
                    invoice_id,
                    ctx["tenant_id"],
                )

                remaining = int(journal_remaining or 0)
                if body.amount > remaining:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Payment amount exceeds remaining balance of Rp {remaining:,}",
                    )

                # --- Resolve bank account ---
                # Frontend sends account_id (CoA UUID). May also send bank_account_id.
                # bank_accounts schema: id (UUID PK), coa_id (UUID FK to CoA), account_name (varchar)
                account_uuid = UUID(body.account_id)
                bank_account_uuid = None
                bank_account_name = ""
                bank_coa_id = account_uuid  # fallback: treat as CoA directly

                if body.bank_account_id:
                    bank_row = await conn.fetchrow(
                        "SELECT id, coa_id, account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                        UUID(body.bank_account_id),
                        ctx["tenant_id"],
                    )
                    if bank_row:
                        bank_account_uuid = bank_row["id"]
                        bank_account_name = bank_row["account_name"]
                        bank_coa_id = bank_row["coa_id"]
                else:
                    # Try: account_id is coa_id → reverse lookup bank_account
                    bank_row = await conn.fetchrow(
                        "SELECT id, coa_id, account_name FROM bank_accounts WHERE coa_id = $1 AND tenant_id = $2",
                        account_uuid,
                        ctx["tenant_id"],
                    )
                    if bank_row:
                        bank_account_uuid = bank_row["id"]
                        bank_account_name = bank_row["account_name"]
                        bank_coa_id = bank_row["coa_id"]
                    else:
                        # Try: account_id IS a bank_accounts.id
                        bank_row = await conn.fetchrow(
                            "SELECT id, coa_id, account_name FROM bank_accounts WHERE id = $1 AND tenant_id = $2",
                            account_uuid,
                            ctx["tenant_id"],
                        )
                        if bank_row:
                            bank_account_uuid = bank_row["id"]
                            bank_account_name = bank_row["account_name"]
                            bank_coa_id = bank_row["coa_id"]

                if not bank_account_uuid:
                    raise HTTPException(
                        status_code=400,
                        detail="Could not resolve bank account from account_id",
                    )

                # Resolve AR account (Law 27, Fase C1.1: role-based)
                ar_account_id = await resolve_account_id_by_role(
                    conn, ctx["tenant_id"], AccountRole.AR_TRADE
                )

                # Map payment_method: receive_payments CHECK allows only 'cash' or 'bank_transfer'
                pm = body.payment_method
                if pm not in ("cash", "bank_transfer"):
                    pm = "bank_transfer"

                # Generate payment number via DB function (same as golden pattern)
                payment_number = await conn.fetchval(
                    "SELECT generate_receive_payment_number($1)", ctx["tenant_id"]
                )

                import uuid as uuid_module

                pay_amount = body.amount  # int from InvoicePaymentCreate schema

                # === INSERT receive_payments ===
                # NOT NULL cols: id, tenant_id, payment_number, customer_name, payment_date,
                #   payment_method, bank_account_id, bank_account_name, source_type,
                #   total_amount, allocated_amount, unapplied_amount, discount_amount
                rp_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO receive_payments (
                        id, tenant_id, payment_number, customer_id, customer_name,
                        payment_date, payment_method, bank_account_id, bank_account_name,
                        source_type, total_amount, allocated_amount, unapplied_amount,
                        discount_amount, reference_number, notes, status, created_by
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9,
                        'cash', $10, $11, 0,
                        0, $12, $13, 'draft', $14
                    )
                """,
                    rp_id,
                    ctx["tenant_id"],
                    payment_number,
                    str(invoice["customer_id"]) if invoice["customer_id"] else None,
                    invoice["customer_name"],
                    body.payment_date,
                    pm,
                    bank_account_uuid,
                    bank_account_name,
                    pay_amount,
                    pay_amount,
                    body.reference,
                    body.notes,
                    ctx["user_id"],
                )

                # === INSERT receive_payment_allocations ===
                # NOT NULL cols: tenant_id, payment_id, invoice_id, invoice_number,
                #   invoice_amount, remaining_before, amount_applied, remaining_after
                await conn.execute(
                    """
                    INSERT INTO receive_payment_allocations (
                        tenant_id, payment_id, invoice_id, invoice_number,
                        invoice_amount, remaining_before, amount_applied, remaining_after
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                    ctx["tenant_id"],
                    rp_id,
                    invoice_id,
                    invoice["invoice_number"],
                    float(invoice["total_amount"]),
                    remaining,
                    pay_amount,
                    remaining - pay_amount,
                )

                # === Create journal: Dr. Bank, Cr. AR ===
                # Law 20: DRAFT → lines → POSTED
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                journal_number = await conn.fetchval(
                    "SELECT get_next_journal_number($1, $2)", ctx["tenant_id"], "RCV"
                )
                if not journal_number:
                    journal_number = f"JRN-PAY-{rp_id}"

                inv_number = invoice["invoice_number"]

                # source_type='RECEIVE_PAYMENT', source_id=rp_id (UUID)
                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'RECEIVE_PAYMENT', $6, $7, 'DRAFT', $8, $8, $9)
                """,
                    journal_id,
                    ctx["tenant_id"],
                    journal_number,
                    body.payment_date,
                    f"Penerimaan Pembayaran Faktur {inv_number}",
                    rp_id,
                    str(trace_id),
                    pay_amount,
                    ctx["user_id"],
                )

                # Dr. Bank/Kas (line 1)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 1, $3, $4, 0, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    bank_coa_id,
                    pay_amount,
                    f"Terima Pembayaran - {inv_number}",
                )

                # Cr. Piutang / AR (line 2)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (
                        id, journal_id, line_number, account_id, debit, credit, memo
                    ) VALUES ($1, $2, 2, $3, 0, $4, $5)
                """,
                    uuid_module.uuid4(),
                    journal_id,
                    ar_account_id,
                    pay_amount,
                    f"Pelunasan Piutang - {inv_number}",
                )

                # DRAFT → POSTED (triggers hash chain)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # Link journal to receive_payments + mark posted
                await conn.execute(
                    """
                    UPDATE receive_payments
                    SET journal_id = $1, journal_number = $2, status = 'posted',
                        posted_at = NOW(), posted_by = $3,
                        operational_status = 'CONFIRMED', accounting_status = 'POSTED'
                    WHERE id = $4
                """,
                    journal_id,
                    journal_number,
                    ctx["user_id"],
                    rp_id,
                )

                # Update invoice cache (Law 21: write-side only)
                await conn.execute(
                    """
                    UPDATE sales_invoices
                    SET amount_paid = amount_paid + $1,
                        status = CASE WHEN total_amount <= (amount_paid + $1) THEN 'paid' ELSE 'partial' END,
                        updated_at = NOW()
                    WHERE id = $2 AND tenant_id = $3
                """,
                    pay_amount,
                    invoice_id,
                    ctx["tenant_id"],
                )

                # Update AR cache if exists
                if invoice["ar_id"]:
                    await conn.execute(
                        """
                        UPDATE accounts_receivable
                        SET amount_paid = amount_paid + $2,
                            status = CASE WHEN amount - (amount_paid + $2) <= 0 THEN 'PAID' ELSE 'PARTIAL' END,
                            updated_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["ar_id"],
                        pay_amount,
                    )

                # Bank transaction (BankSync Rule 1: atomic journal + bank_txn)
                if bank_account_uuid:
                    bank_tx_id = uuid_module.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date,
                            transaction_type, amount, running_balance,
                            reference_type, reference_id, description,
                            payee_payer, journal_id, created_by
                        ) VALUES ($1, $2, $3, $4, 'payment_received', $5, 0, 'invoice', $6, $7, $8, $9, $10)
                    """,
                        bank_tx_id,
                        ctx["tenant_id"],
                        bank_account_uuid,
                        body.payment_date,
                        pay_amount,
                        invoice_id,
                        f"Payment received for {inv_number}",
                        body.reference or "Customer Payment",
                        journal_id,
                        ctx["user_id"],
                    )
                    await conn.execute(
                        "UPDATE receive_payments SET bank_transaction_id = $1 WHERE id = $2",
                        bank_tx_id,
                        rp_id,
                    )

                # Law 14: Store idempotency
                import json as json_mod

                result_payload = {
                    "success": True,
                    "message": "Payment recorded successfully",
                    "created_payment_id": str(rp_id),
                    "data": {
                        "status": "posted",
                        "id": str(rp_id),
                        "invoice_id": str(invoice_id),
                        "amount": pay_amount,
                        "journal_id": str(journal_id),
                        "journal_number": journal_number,
                        "payment_number": payment_number,
                    },
                }
                await conn.execute(
                    """INSERT INTO idempotency_keys (key, tenant_id, source_type, result, result_status, expires_at)
                    VALUES ($1, $2, 'RECEIVE_PAYMENT', $3, 'SUCCESS', NOW() + interval '24 hours')
                    ON CONFLICT (tenant_id, key) DO NOTHING""",
                    idem_key,
                    ctx["tenant_id"],
                    json_mod.dumps(result_payload, default=str),
                )

                logger.info(
                    f"Payment recorded via receive_payments: {rp_id} for invoice {invoice_id}, journal={journal_id}"
                )
                return result_payload

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording payment for {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to record payment")


# =============================================================================
# VOID INVOICE
# =============================================================================
@router.post("/{invoice_id}/void", response_model=InvoiceResponse)
async def void_invoice(request: Request, invoice_id: UUID, body: VoidInvoiceRequest):
    """
    Void an invoice following Iron Laws + 3-Event Revenue Recognition:
    - Law 2: Journal Immutability - creates REVERSAL journals, not delete
    - Law 3: Append-Only - inventory restored via new ledger entry
    - Law 4: Double-Entry - all reversals must balance
    - Fulfillment cascade: void fulfillments (reverse chrono) before billing reversal
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Get full invoice data including COGS info
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, customer_id, customer_name, total_amount, invoice_date,
                       status, ar_id, journal_id, cogs_journal_id, total_cogs
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            if invoice["status"] == "void":
                raise HTTPException(status_code=400, detail="Invoice is already voided")

            # Pure Ledger: check if invoice has journal-based payments (Law 16)
            journal_paid = await conn.fetchval(
                """
                SELECT
                    COALESCE((SELECT SUM(rpa.amount_applied)
                        FROM receive_payment_allocations rpa
                        JOIN receive_payments rp ON rp.id = rpa.payment_id
                        WHERE rpa.invoice_id = $1 AND rpa.tenant_id = $2
                          AND rp.status = 'posted' AND rp.journal_id IS NOT NULL), 0)
                    + COALESCE((SELECT SUM(sip_jl.credit)
                        FROM sales_invoice_payments sip
                        JOIN journal_entries sip_je ON sip_je.id = sip.journal_id
                        JOIN journal_lines sip_jl ON sip_jl.journal_id = sip_je.id
                        JOIN chart_of_accounts sip_coa ON sip_coa.id = sip_jl.account_id
                        WHERE sip.invoice_id = $1 AND sip_je.status = 'POSTED'
                          AND sip_coa.account_type = 'RECEIVABLE'), 0)
                    + COALESCE((SELECT SUM(jl5.credit)
                        FROM journal_lines jl5
                        JOIN journal_entries je5 ON je5.id = jl5.journal_id
                        JOIN chart_of_accounts coa5 ON coa5.id = jl5.account_id
                        WHERE je5.source_type = 'PAYMENT_RECEIVED'
                          AND je5.tenant_id = $2 AND je5.status = 'POSTED'
                          AND coa5.account_type = 'RECEIVABLE'
                          AND je5.description LIKE '%%' || (SELECT invoice_number FROM sales_invoices WHERE id = $1) || '%%'
                          AND NOT EXISTS(
                              SELECT 1 FROM receive_payment_allocations rpa5
                              WHERE rpa5.payment_id = je5.source_id AND rpa5.tenant_id = $2
                          )), 0)
            """,
                invoice_id,
                ctx["tenant_id"],
            )
            if (journal_paid or 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot void invoice with payments. Refund first.",
                )

            # ============================================================
            # FIX_P3_BRIDGE 2026-06-16: void-cascade guard for applied deposits
            # ------------------------------------------------------------
            # An applied customer deposit (Dr Uang Muka / Cr Piutang) settles
            # this invoice's AR. Voiding the invoice while that application is
            # still ACTIVE (non-reversed) would strand the application against a
            # gone obligation (symmetric-state lesson #23). Block the void and
            # point the user at the P1 un-apply remediation. Reversed
            # applications (status='reversed') do NOT block.
            active_deposit_apps = await conn.fetch(
                """
                SELECT cda.id AS application_id,
                       cda.deposit_id,
                       cda.amount_applied,
                       cd.deposit_number
                FROM customer_deposit_applications cda
                LEFT JOIN customer_deposits cd ON cd.id = cda.deposit_id
                WHERE cda.invoice_id = $1
                  AND cda.tenant_id = $2
                  AND cda.status = 'active'
                  AND cda.reversed_by_id IS NULL
                ORDER BY cda.created_at
                """,
                invoice_id,
                ctx["tenant_id"],
            )
            if active_deposit_apps:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": (
                            "Tidak bisa void: ada Uang Muka teralokasi ke faktur "
                            "ini. Un-apply deposit dulu."
                        ),
                        "code": "DEPOSIT_APPLIED",
                        "applications": [
                            {
                                "application_id": str(a["application_id"]),
                                "deposit_id": str(a["deposit_id"]),
                                "deposit_number": a["deposit_number"],
                                "amount_applied": int(a["amount_applied"] or 0),
                                "unapply_url": (
                                    f"/api/customer-deposits/{a['deposit_id']}"
                                    f"/applications/{a['application_id']}/reverse"
                                ),
                            }
                            for a in active_deposit_apps
                        ],
                    },
                )

            # ============================================================
            # Fulfillment pre-checks (3-Event Revenue Recognition)
            # ============================================================
            fulfillments = await conn.fetch(
                """
                SELECT id, fulfillment_number, fulfillment_date, journal_id, revenue_journal_id, status
                FROM invoice_fulfillments
                WHERE invoice_id = $1 AND tenant_id = $2 AND status = 'posted'
                ORDER BY created_at DESC
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            # Pre-check: ALL fulfillment periods must be open
            for f in fulfillments:
                f_period = await conn.fetchrow(
                    "SELECT id, period_name, status FROM fiscal_periods WHERE tenant_id=$1 AND $2 BETWEEN start_date AND end_date ORDER BY start_date DESC LIMIT 1",
                    ctx["tenant_id"],
                    f["fulfillment_date"],
                )
                if f_period and f_period["status"] in ("CLOSED", "LOCKED"):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": f"Pengiriman {f['fulfillment_number']} di periode {f_period['period_name']} yang sudah {f_period['status'].lower()}",
                            "suggestion": "credit_note",
                            "action_url": f"/api/credit-notes/from-invoice/{invoice_id}",
                            "prefill": {
                                "customer_id": str(invoice.get("customer_id", "")),
                                "customer_name": invoice.get("customer_name", ""),
                                "invoice_id": str(invoice_id),
                                "invoice_number": invoice.get("invoice_number", ""),
                            },
                        },
                    )

            # Check billing period (with CN suggestion if closed)
            from datetime import date as dt_date

            today = dt_date.today()

            billing_period = await conn.fetchrow(
                "SELECT id, period_name, status FROM fiscal_periods WHERE tenant_id=$1 AND $2 BETWEEN start_date AND end_date ORDER BY start_date DESC LIMIT 1",
                ctx["tenant_id"],
                invoice["invoice_date"],
            )
            if billing_period and billing_period["status"] in ("CLOSED", "LOCKED"):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"Faktur di periode {billing_period['period_name']} yang sudah {billing_period['status'].lower()}",
                        "suggestion": "credit_note",
                        "action_url": f"/api/credit-notes/from-invoice/{invoice_id}",
                        "prefill": {
                            "customer_id": str(invoice.get("customer_id", "")),
                            "customer_name": invoice.get("customer_name", ""),
                            "invoice_id": str(invoice_id),
                            "invoice_number": invoice.get("invoice_number", ""),
                        },
                    },
                )
            # Still check void date period
            await check_period_is_open(conn, ctx["tenant_id"], today)

            async with conn.transaction():
                import uuid

                # Law 13: Advisory lock
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"INVOICE_VOID:{str(invoice_id)}",
                )

                year_month_str = today.strftime("%y%m")
                reversal_journal_id = None
                cogs_reversal_journal_id = None
                last_cogs_rev_id = None

                # ============================================================
                # 0. VOID FULFILLMENTS (reverse chronological)
                # 3-Event Revenue Recognition cascade
                # ============================================================
                for f in fulfillments:
                    # 0a. Reverse revenue journal (if exists)
                    if f["revenue_journal_id"]:
                        rev_rev_id = uuid.uuid4()
                        rev_trace = str(uuid.uuid4())
                        # Self-healing canonical generator (V176): emits REV, bumps REV counter.
                        rev_number = await conn.fetchval(
                            "SELECT get_next_journal_number($1, $2, $3)",
                            ctx["tenant_id"],
                            "REV",
                            today,
                        )

                        orig_rev = await conn.fetchrow(
                            "SELECT total_debit, description FROM journal_entries WHERE id=$1",
                            f["revenue_journal_id"],
                        )
                        rev_amount = orig_rev["total_debit"] if orig_rev else 0

                        await conn.execute(
                            """
                            INSERT INTO journal_entries (
                                id, tenant_id, journal_number, journal_date,
                                description, source_type, source_id, trace_id,
                                total_debit, total_credit,
                                status, created_by, reversal_of_id, reversal_reason
                            ) VALUES ($1,$2,$3,$4,$5,'INVOICE_REVENUE_REVERSAL',$6,$7,$8,$8,'DRAFT',$9,$10,$11)
                        """,
                            rev_rev_id,
                            ctx["tenant_id"],
                            rev_number,
                            today,
                            f"VOID Revenue: {invoice['invoice_number']}",
                            invoice_id,
                            rev_trace,
                            rev_amount,
                            ctx["user_id"],
                            f["revenue_journal_id"],
                            body.reason,
                        )
                        # Reverse lines: flip debit/credit
                        orig_lines = await conn.fetch(
                            "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id=$1 ORDER BY line_number",
                            f["revenue_journal_id"],
                        )
                        for ln_num, ol in enumerate(orig_lines, 1):
                            await conn.execute(
                                "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                                uuid.uuid4(),
                                rev_rev_id,
                                ln_num,
                                ol["account_id"],
                                ol["credit"],
                                ol["debit"],
                                f"VOID: {ol['memo']}",
                            )
                        await conn.execute(
                            "UPDATE journal_entries SET status='POSTED' WHERE id=$1",
                            rev_rev_id,
                        )
                        await conn.execute(
                            "UPDATE journal_entries SET reversed_by_id=$2, reversed_at=NOW() WHERE id=$1",
                            f["revenue_journal_id"],
                            rev_rev_id,
                        )

                    # 0b. Reverse COGS journal (if exists)
                    if f["journal_id"]:
                        last_cogs_rev_id = uuid.uuid4()
                        cogs_trace = str(uuid.uuid4())
                        # Self-healing canonical generator (V176): emits COGS-REV, bumps COGS-REV counter.
                        cogs_rev_number_f = await conn.fetchval(
                            "SELECT get_next_journal_number($1, $2, $3)",
                            ctx["tenant_id"],
                            "COGS-REV",
                            today,
                        )
                        orig_cogs = await conn.fetchrow(
                            "SELECT total_debit FROM journal_entries WHERE id=$1",
                            f["journal_id"],
                        )
                        cogs_amount = orig_cogs["total_debit"] if orig_cogs else 0

                        await conn.execute(
                            """
                            INSERT INTO journal_entries (
                                id, tenant_id, journal_number, journal_date,
                                description, source_type, source_id, trace_id,
                                total_debit, total_credit,
                                status, created_by, reversal_of_id, reversal_reason
                            ) VALUES ($1,$2,$3,$4,$5,'INVOICE_FULFILLMENT_REVERSAL',$6,$7,$8,$8,'DRAFT',$9,$10,$11)
                        """,
                            last_cogs_rev_id,
                            ctx["tenant_id"],
                            cogs_rev_number_f,
                            today,
                            f"VOID COGS: {invoice['invoice_number']}",
                            invoice_id,
                            cogs_trace,
                            cogs_amount,
                            ctx["user_id"],
                            f["journal_id"],
                            body.reason,
                        )
                        orig_cogs_lines = await conn.fetch(
                            "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id=$1 ORDER BY line_number",
                            f["journal_id"],
                        )
                        for ln_num, ol in enumerate(orig_cogs_lines, 1):
                            await conn.execute(
                                "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                                uuid.uuid4(),
                                last_cogs_rev_id,
                                ln_num,
                                ol["account_id"],
                                ol["credit"],
                                ol["debit"],
                                f"VOID: {ol['memo']}",
                            )
                        await conn.execute(
                            "UPDATE journal_entries SET status='POSTED' WHERE id=$1",
                            last_cogs_rev_id,
                        )
                        await conn.execute(
                            "UPDATE journal_entries SET reversed_by_id=$2, reversed_at=NOW() WHERE id=$1",
                            f["journal_id"],
                            last_cogs_rev_id,
                        )

                    # 0c. Mark fulfillment as voided
                    await conn.execute(
                        "UPDATE invoice_fulfillments SET status='voided', voided_at=NOW(), voided_reason=$2 WHERE id=$1",
                        f["id"],
                        body.reason,
                    )

                # Inventory reversal for ALL fulfillments (single call since same source_id)
                if fulfillments:
                    from ..services.inventory_helpers import record_inventory_reversal

                    await record_inventory_reversal(
                        conn,
                        ctx["tenant_id"],
                        source_type="INVOICE_FULFILLMENT",
                        source_id=invoice_id,
                        reversal_journal_id=last_cogs_rev_id or invoice_id,
                        created_by=ctx["user_id"],
                        reversal_date=today,
                        notes_prefix="VOID",
                    )

                # Reset fulfillment tracking on invoice items
                if fulfillments:
                    await conn.execute(
                        "UPDATE sales_invoice_items SET fulfilled_qty=0, recognized_amount=0 WHERE invoice_id=$1",
                        invoice_id,
                    )

                # ============================================================
                # 1. Create REVERSAL Journal for AR/Billing (if posted)
                # Iron Law 2: Journal Immutability - REVERSAL, not delete
                # Uses flip-lines approach: automatically handles Dimuka + PPN
                # ============================================================
                if invoice["journal_id"]:
                    reversal_journal_id = uuid.uuid4()
                    trace_id = str(uuid.uuid4())

                    # Self-healing canonical generator (V176): emits REV, bumps REV counter.
                    rev_journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "REV",
                        today,
                    )

                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            total_debit, total_credit,
                            status, created_by, reversal_of_id, reversal_reason
                        ) VALUES ($1, $2, $3, $4, $5, 'INVOICE_REVERSAL', $6, $7, $8, $8, 'DRAFT', $9, $10, $11)
                    """,
                        reversal_journal_id,
                        ctx["tenant_id"],
                        rev_journal_number,
                        today,
                        f"VOID: Faktur {invoice['invoice_number']} - {invoice['customer_name']}",
                        invoice_id,
                        trace_id,
                        invoice["total_amount"],
                        ctx["user_id"],
                        invoice["journal_id"],
                        body.reason,
                    )

                    # Flip original billing journal lines (handles Dimuka + PPN correctly)
                    orig_billing_lines = await conn.fetch(
                        "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id=$1 ORDER BY line_number",
                        invoice["journal_id"],
                    )
                    for ln_num, ol in enumerate(orig_billing_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid.uuid4(),
                            reversal_journal_id,
                            ln_num,
                            ol["account_id"],
                            ol["credit"],
                            ol["debit"],
                            f"VOID: {ol['memo']}",
                        )

                    # Law 20: DRAFT->POSTED triggers hash chain
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        reversal_journal_id,
                    )

                    # Mark original journal as reversed
                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["journal_id"],
                        reversal_journal_id,
                    )

                    logger.info(f"AR reversal journal created: {reversal_journal_id}")

                # ============================================================
                # 1b. FIX_VOID_REVREC_ORPHAN 2026-06-18: source-agnostic
                # recognition-leg reversal. void_invoice reverses recognition
                # only via invoice_fulfillments.revenue_journal_id (loop above),
                # which is EMPTY for SERVICE invoices (no fulfillment row) ->
                # their INVOICE_REVENUE journal (Dr Unearned / Cr Penjualan)
                # survives orphaned + is_effective -> deferred-rev guard FAIL.
                # Reverse any live INVOICE_REVENUE for this invoice not already
                # reversed (reversed_by_id IS NULL naturally skips fulfilled
                # invoices already handled by loop 0a; Law 26 max-1).
                # ============================================================
                orphan_rev = await conn.fetchrow(
                    """
                    SELECT id, total_debit FROM journal_entries
                    WHERE source_type = 'INVOICE_REVENUE'
                      AND source_id = $1
                      AND tenant_id = $2
                      AND status = 'POSTED'
                      AND reversed_by_id IS NULL
                    """,
                    invoice_id,
                    ctx["tenant_id"],
                )
                if orphan_rev:
                    orphan_rev_rev_id = uuid.uuid4()
                    orphan_rev_trace = str(uuid.uuid4())
                    # Self-healing canonical generator (V176): emits REV, bumps REV counter.
                    orphan_rev_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "REV",
                        today,
                    )
                    orphan_rev_amount = orphan_rev["total_debit"] or 0

                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            total_debit, total_credit,
                            status, created_by, reversal_of_id, reversal_reason
                        ) VALUES ($1,$2,$3,$4,$5,'INVOICE_REVENUE_REVERSAL',$6,$7,$8,$8,'DRAFT',$9,$10,$11)
                    """,
                        orphan_rev_rev_id,
                        ctx["tenant_id"],
                        orphan_rev_number,
                        today,
                        f"VOID Revenue: {invoice['invoice_number']}",
                        invoice_id,
                        orphan_rev_trace,
                        orphan_rev_amount,
                        ctx["user_id"],
                        orphan_rev["id"],
                        body.reason,
                    )
                    # Reverse lines: flip debit/credit
                    orphan_rev_lines = await conn.fetch(
                        "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id=$1 ORDER BY line_number",
                        orphan_rev["id"],
                    )
                    for ln_num, ol in enumerate(orphan_rev_lines, 1):
                        await conn.execute(
                            "INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                            uuid.uuid4(),
                            orphan_rev_rev_id,
                            ln_num,
                            ol["account_id"],
                            ol["credit"],
                            ol["debit"],
                            f"VOID: {ol['memo']}",
                        )
                    # Law 20: DRAFT->POSTED triggers hash chain
                    await conn.execute(
                        "UPDATE journal_entries SET status='POSTED' WHERE id=$1",
                        orphan_rev_rev_id,
                    )
                    # Law 26: mark original reversed (max-1 enforced)
                    await conn.execute(
                        "UPDATE journal_entries SET reversed_by_id=$2, reversed_at=NOW() WHERE id=$1",
                        orphan_rev["id"],
                        orphan_rev_rev_id,
                    )
                    logger.info(
                        f"Orphan INVOICE_REVENUE reversal created: {orphan_rev_rev_id}"
                    )

                # ============================================================
                # 2. Legacy COGS reversal (pre-3-event invoices only)
                # For invoices that have cogs_journal_id but NO fulfillment records
                # ============================================================
                if (
                    not fulfillments
                    and invoice["cogs_journal_id"]
                    and invoice["total_cogs"]
                    and invoice["total_cogs"] > 0
                ):
                    cogs_reversal_journal_id = uuid.uuid4()
                    cogs_trace_id = str(uuid.uuid4())

                    # Self-healing canonical generator (V176): emits COGS-REV, bumps COGS-REV counter.
                    cogs_rev_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        ctx["tenant_id"],
                        "COGS-REV",
                        today,
                    )

                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            total_debit, total_credit,
                            status, created_by, reversal_of_id, reversal_reason
                        ) VALUES ($1, $2, $3, $4, $5, 'SALES_INVOICE_COGS_REVERSAL', $6, $7, $8, $8, 'DRAFT', $9, $10, $11)
                    """,
                        cogs_reversal_journal_id,
                        ctx["tenant_id"],
                        cogs_rev_number,
                        today,
                        f"VOID HPP: {invoice['invoice_number']} - {invoice['customer_name']}",
                        invoice_id,
                        cogs_trace_id,
                        invoice["total_cogs"],
                        ctx["user_id"],
                        invoice["cogs_journal_id"],
                        body.reason,
                    )

                    # Flip original COGS journal lines
                    orig_cogs_lines = await conn.fetch(
                        "SELECT account_id, debit, credit, memo FROM journal_lines WHERE journal_id=$1 ORDER BY line_number",
                        invoice["cogs_journal_id"],
                    )
                    for ln_num, ol in enumerate(orig_cogs_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                            VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                            uuid.uuid4(),
                            cogs_reversal_journal_id,
                            ln_num,
                            ol["account_id"],
                            ol["credit"],
                            ol["debit"],
                            f"VOID: {ol['memo']}",
                        )

                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        cogs_reversal_journal_id,
                    )

                    await conn.execute(
                        """
                        UPDATE journal_entries
                        SET reversed_by_id = $2, reversed_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["cogs_journal_id"],
                        cogs_reversal_journal_id,
                    )

                    logger.info(
                        f"COGS reversal journal created: {cogs_reversal_journal_id}"
                    )

                # ============================================================
                # 3. Restore Inventory (legacy path, pre-3-event)
                # ============================================================
                if not fulfillments:
                    from ..services.inventory_helpers import record_inventory_reversal

                    await record_inventory_reversal(
                        conn,
                        ctx["tenant_id"],
                        source_type="SALES_INVOICE",
                        source_id=invoice_id,
                        reversal_journal_id=cogs_reversal_journal_id
                        or (reversal_journal_id if reversal_journal_id else invoice_id),
                        created_by=ctx["user_id"],
                        reversal_date=today,
                        notes_prefix="VOID",
                    )

                # ============================================================
                # 3.5. Reverse Bank Transactions (BankSync Rule 3)
                # ============================================================
                payment_bank_txns = await conn.fetch(
                    """
                    SELECT bt.id, bt.bank_account_id, bt.amount, bt.transaction_type,
                           bt.description, bt.journal_id
                    FROM bank_transactions bt
                    JOIN sales_invoice_payments sip ON sip.journal_id = bt.journal_id
                    WHERE sip.invoice_id = $1 AND bt.tenant_id = $2
                """,
                    invoice_id,
                    ctx["tenant_id"],
                )

                for bt in payment_bank_txns:
                    reversal_bt_id = uuid.uuid4()
                    reversed_type = (
                        "CREDIT" if bt["transaction_type"] == "DEBIT" else "DEBIT"
                    )
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date,
                            transaction_type, amount, running_balance,
                            reference_type, reference_id, description,
                            journal_id, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, 0, 'invoice_void', $7, $8, $9, $10)
                    """,
                        reversal_bt_id,
                        ctx["tenant_id"],
                        bt["bank_account_id"],
                        today,
                        reversed_type,
                        -bt["amount"],
                        invoice_id,
                        f"VOID: Reversal of {bt['description']}",
                        reversal_journal_id,
                        ctx["user_id"],
                    )
                    logger.info(
                        f"Bank transaction reversed: {bt['id']} -> {reversal_bt_id}"
                    )

                # ============================================================
                # 4. Update AR status to VOID
                # ============================================================
                if invoice["ar_id"]:
                    await conn.execute(
                        """
                        UPDATE accounts_receivable
                        SET status = 'VOID', updated_at = NOW()
                        WHERE id = $1
                    """,
                        invoice["ar_id"],
                    )

                # ============================================================
                # 5. Update invoice status to void (+ fulfillment tracking reset)
                # ============================================================
                await conn.execute(
                    """
                    UPDATE sales_invoices
                    SET status = 'void', operational_status = 'VOID', accounting_status = 'REVERSED',
                        voided_at = NOW(), voided_reason = $2,
                        fulfillment_status = 'not_applicable', revenue_status = 'not_applicable',
                        total_fulfilled_qty = 0, total_recognized_amount = 0,
                        updated_at = NOW()
                    WHERE id = $1
                """,
                    invoice_id,
                    body.reason,
                )

                # Clean up document_tax_lines on void
                await conn.execute(
                    "DELETE FROM document_tax_lines WHERE document_id = $1 AND tenant_id = $2",
                    invoice_id,
                    ctx["tenant_id"],
                )

                logger.info(f"Invoice voided: {invoice_id}, reason: {body.reason}")

                return {
                    "success": True,
                    "message": "Invoice voided successfully with reversal journals",
                    "data": {
                        "status": "draft",
                        "id": str(invoice_id),
                        "reversal_journal_id": str(reversal_journal_id)
                        if reversal_journal_id
                        else None,
                        "cogs_reversal_journal_id": str(cogs_reversal_journal_id)
                        if cogs_reversal_journal_id
                        else None,
                        "fulfillments_voided": len(fulfillments),
                    },
                }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error voiding invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to void invoice")


# =============================================================================
# FIX_P3_BRIDGE 2026-06-16: applicable customer deposits for an invoice
# =============================================================================
@router.get("/{invoice_id}/applicable-deposits")
async def get_applicable_deposits(request: Request, invoice_id: UUID):
    """List customer deposits with available balance applicable to this invoice.

    P3 read endpoint. ZERO new accounting: the apply itself stays the existing
    P1 POST /api/customer-deposits/{id}/apply (separate, idempotent txn).

    available       : journal-derived (Law 1/16) net movement on
                      CUSTOMER_DEPOSIT_LIABILITY per deposit, is_effective only
                      (NOT the amount/amount_applied cache columns).
    invoice_remaining : journal-derived via compute_ar_outstanding().
    suggested_amount  : min(available, invoice_remaining).
    match_type        : 'spine' if the deposit's quote_id/sales_order_id matches
                        the invoice's quote_id/sales_order_id, else 'customer'.
                        Spine matches are sorted first (auto-suggest order).
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            invoice = await conn.fetchrow(
                """
                SELECT id, customer_id, customer_name, quote_id, sales_order_id, status
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
                """,
                invoice_id,
                ctx["tenant_id"],
            )
            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")
            if invoice["customer_id"] is None:
                return {"items": [], "total": 0}

            # invoice_remaining — journal-derived. Use the P1 deposit-aware
            # helper get_invoice_remaining_from_journal (counts INVOICE debit
            # minus receive-payment, credit-note AND DEPOSIT_APPLICATION credits,
            # is_effective only). NOTE (2026-06-19): compute_ar_outstanding() DOES
            # now recognise customer_deposit_applications as AR settlements (Branch 3:
            # joins customer_deposit_applications on source_type='DEPOSIT_APPLICATION'
            # and subtracts the Cr RECEIVABLE credits) — the earlier 'latent
            # over-state' P3 finding is RESOLVED. This helper is kept here for the
            # single-invoice fast path; both agree on partial-apply remaining.
            from .customer_deposits import get_invoice_remaining_from_journal

            invoice_remaining = await get_invoice_remaining_from_journal(
                conn, ctx["tenant_id"], invoice_id
            )
            invoice_remaining = int(invoice_remaining or 0)

            # CUSTOMER_DEPOSIT_LIABILITY CoA — net-movement = available.
            deposit_account_id = await resolve_account_id_by_role(
                conn, ctx["tenant_id"], AccountRole.CUSTOMER_DEPOSIT_LIABILITY
            )

            # Posted, non-void deposits for this customer, with journal-derived
            # available > 0. Net movement (Cr - Dr) on the liability account,
            # is_effective journals only => correct after un-apply/refund by
            # construction. Multiple deposits per order are each listed with
            # their own available (the sum is therefore correct).
            rows = await conn.fetch(
                """
                SELECT
                    cd.id            AS deposit_id,
                    cd.deposit_number,
                    cd.customer_id,
                    cd.deposit_date,
                    cd.quote_id,
                    cd.sales_order_id,
                    COALESCE((
                        SELECT SUM(jl.credit) - SUM(jl.debit)
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        WHERE je.tenant_id = cd.tenant_id
                          AND je.source_id = cd.id
                          AND jl.account_id = $3
                          AND is_effective_journal(je.id)
                    ), 0) AS available
                FROM customer_deposits cd
                WHERE cd.tenant_id = $1
                  AND cd.customer_id = $2
                  AND cd.status NOT IN ('draft', 'void', 'voided')
                """,
                ctx["tenant_id"],
                invoice["customer_id"],
                deposit_account_id,
            )

            inv_quote_id = invoice["quote_id"]
            inv_so_id = invoice["sales_order_id"]
            items = []
            for r in rows:
                available = int(r["available"] or 0)
                if available <= 0:
                    continue
                is_spine = (
                    inv_quote_id is not None and r["quote_id"] == inv_quote_id
                ) or (inv_so_id is not None and r["sales_order_id"] == inv_so_id)
                items.append(
                    {
                        "deposit_id": str(r["deposit_id"]),
                        "deposit_number": r["deposit_number"],
                        "available": available,
                        "suggested_amount": min(available, invoice_remaining),
                        "match_type": "spine" if is_spine else "customer",
                        "quote_id": str(r["quote_id"]) if r["quote_id"] else None,
                        "sales_order_id": (
                            str(r["sales_order_id"]) if r["sales_order_id"] else None
                        ),
                        "customer_id": r["customer_id"],
                        "deposit_date": (
                            r["deposit_date"].isoformat() if r["deposit_date"] else None
                        ),
                    }
                )

            # Spine matches first, then by deposit_date (oldest first).
            items.sort(
                key=lambda x: (
                    0 if x["match_type"] == "spine" else 1,
                    x["deposit_date"] or "",
                )
            )

            return {
                "items": items,
                "total": len(items),
                "invoice_remaining": invoice_remaining,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing applicable deposits: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to list applicable deposits"
        )


# =============================================================================
# DELETE INVOICE (Draft only)
# =============================================================================
@router.delete("/{invoice_id}", response_model=InvoiceResponse)
async def delete_invoice(request: Request, invoice_id: UUID):
    """Delete a draft invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, status
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Guard: Cannot delete voided invoices
            if invoice["status"] == "void":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete voided invoice",
                )

            # Guard: Cannot delete non-draft invoices
            if invoice["status"] != "draft":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete posted invoice. Use void instead.",
                )

            # Delete (cascade will handle items)
            await conn.execute("DELETE FROM sales_invoices WHERE id = $1", invoice_id)

            logger.info(f"Invoice deleted: {invoice_id}")

            return {
                "success": True,
                "message": "Invoice deleted successfully",
                "data": {
                    "status": "draft",
                    "id": str(invoice_id),
                    "invoice_number": invoice["invoice_number"],
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete invoice")


# =============================================================================
# INVOICE HISTORY (Audit Trail)
# =============================================================================
@router.get("/{invoice_id}/history")
async def get_invoice_history(
    request: Request,
    invoice_id: UUID,
    limit: int = Query(50, ge=1, le=200),
):
    """Get audit history for a sales invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify invoice exists and belongs to tenant
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Get history from audit_logs
            # Note: audit_logs table uses camelCase columns and metadata JSONB
            rows = await conn.fetch(
                """
                SELECT id, "createdAt", "eventType", "userId", metadata
                FROM audit_logs
                WHERE metadata->>'entity_type' = 'SALES_INVOICE'
                  AND metadata->>'entity_id' = $1::text
                ORDER BY "createdAt" DESC
                LIMIT $2
            """,
                str(invoice_id),
                limit,
            )

            history = []
            for row in rows:
                metadata = row["metadata"] or {}
                changes = metadata.get("changes")

                history.append(
                    {
                        "id": str(row["id"]),
                        "action": row["eventType"] or metadata.get("action", "UNKNOWN"),
                        "user": {
                            "id": row["userId"] or "",
                            "name": metadata.get("user_name", "System"),
                        },
                        "changes": changes,
                        "created_at": row["createdAt"].isoformat()
                        if row["createdAt"]
                        else None,
                    }
                )

            return {"success": True, "data": history}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice history {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get invoice history")


# =============================================================================
# GENERATE PDF
# =============================================================================
from io import BytesIO  # noqa: E402  # pre-existing mid-file import
from datetime import datetime, timedelta  # noqa: E402  # pre-existing mid-file import
from fastapi.responses import StreamingResponse  # noqa: E402  # pre-existing mid-file import
from ..services.pdf_service import get_pdf_service  # noqa: E402  # pre-existing mid-file import
from ..services.storage_service import get_storage_service  # noqa: E402  # pre-existing mid-file import
import base64  # noqa: E402  # pre-existing mid-file import
from pathlib import Path as _Path  # noqa: E402  # pre-existing mid-file import


@router.get("/{invoice_id}/pdf")
async def get_invoice_pdf(
    request: Request,
    invoice_id: UUID,
    format: Literal["url", "inline"] = Query(
        "url",
        description="Response format: 'url' returns presigned URL, 'inline' returns PDF bytes",
    ),
):
    """
    Generate PDF for a sales invoice.

    **Format options:**
    - url (default): Returns presigned URL for download/share (expires in 1 hour)
    - inline: Returns PDF bytes directly for browser preview

    **Usage:**
    - For download button: use ?format=url and redirect to returned URL
    - For inline preview: use ?format=inline and embed in iframe/viewer
    """
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Fetch invoice with full details
            invoice = await conn.fetchrow(
                """
                SELECT * FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Fetch items
            items = await conn.fetch(
                """
                SELECT * FROM sales_invoice_items
                WHERE invoice_id = $1 ORDER BY line_number
            """,
                invoice_id,
            )

            # Pure Ledger: derive amount_paid via compute_ar_outstanding() DB function
            pdf_ar_row = await conn.fetchrow(
                """
                SELECT paid_amount, outstanding
                FROM compute_ar_outstanding($1)
                WHERE invoice_id = $2
            """,
                ctx["tenant_id"],
                invoice_id,
            )
            # If no row from function: invoice is fully paid (outstanding=0) or draft/void
            if pdf_ar_row:
                pdf_amount_paid = float(
                    invoice["total_amount"] - pdf_ar_row["outstanding"]
                )
            elif invoice["status"] in ("paid",):
                pdf_amount_paid = float(invoice["total_amount"])
            else:
                pdf_amount_paid = 0

            # Fetch tenant info for PDF header
            tenant_row = await conn.fetchrow(
                'SELECT display_name, address, phone, logo_url FROM "Tenant" WHERE id = $1',
                ctx["tenant_id"],
            )
            tenant_info = (
                {
                    "name": tenant_row["display_name"]
                    if tenant_row
                    else ctx["tenant_id"],
                    "address": tenant_row["address"] if tenant_row else None,
                    "phone": tenant_row["phone"] if tenant_row else None,
                    "logo_url": tenant_row["logo_url"] if tenant_row else None,
                }
                if tenant_row
                else {
                    "name": ctx["tenant_id"],
                    "address": None,
                    "phone": None,
                    "logo_url": None,
                }
            )

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
            # Convert to dict for template
            invoice_data = {
                "id": str(invoice["id"]),
                "invoice_number": invoice["invoice_number"],
                "customer_id": str(invoice["customer_id"])
                if invoice["customer_id"]
                else None,
                "customer_name": invoice["customer_name"],
                "invoice_date": invoice["invoice_date"].isoformat()
                if invoice["invoice_date"]
                else None,
                "due_date": invoice["due_date"].isoformat()
                if invoice["due_date"]
                else None,
                "ref_no": invoice["ref_no"],
                "notes": invoice["notes"],
                "subtotal": invoice["subtotal"],
                "discount_percent": float(invoice["discount_percent"] or 0),
                "discount_amount": invoice["discount_amount"],
                "tax_rate": float(invoice["tax_rate"] or 0),
                "tax_amount": invoice["tax_amount"],
                "total_amount": invoice["total_amount"],
                "amount_paid": pdf_amount_paid,
                "amount_due": float(pdf_ar_row["outstanding"])
                if pdf_ar_row
                else (
                    0
                    if invoice["status"] in ("paid",)
                    else float(invoice["total_amount"])
                ),
                "status": invoice["status"],
                "tenant": tenant_info,
                "items": [
                    {
                        "id": str(item["id"]),
                        "item_code": item["item_code"],
                        "description": item["description"],
                        "quantity": float(item["quantity"]),
                        "unit": item["unit"],
                        "unit_price": item["unit_price"],
                        "discount_percent": float(item["discount_percent"] or 0),
                        "discount_amount": item["discount_amount"],
                        "tax_rate": float(item["tax_rate"] or 0),
                        "tax_amount": item["tax_amount"],
                        "subtotal": item["subtotal"],
                        "total": item["total"],
                        "line_number": item["line_number"],
                        "batch_no": item["batch_no"],
                        "exp_date": item["exp_date"],
                        "product_name": item["description"],
                    }
                    for item in items
                ],
            }

        # Generate PDF
        pdf_service = get_pdf_service()
        pdf_bytes = pdf_service.generate_sales_invoice_pdf(invoice_data)

        # Generate filename
        invoice_num = invoice["invoice_number"] or str(invoice_id)[:8]
        filename = f"Faktur-{invoice_num}.pdf"

        if format == "inline":
            return StreamingResponse(
                BytesIO(pdf_bytes),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "no-store",  # FIX_LOGO_CACHEBUST 2026-06-16
                },
            )

        # Upload to storage and return presigned URL
        storage = get_storage_service()
        file_path = f"{ctx['tenant_id']}/invoices/{invoice_id}.pdf"

        url = await storage.upload_bytes(
            content=pdf_bytes,
            file_path=file_path,
            content_type="application/pdf",
            metadata={"invoice_id": str(invoice_id), "invoice_number": invoice_num},
        )

        # Calculate expiry
        expires_at = datetime.utcnow() + timedelta(seconds=storage.config.url_expiry)

        return {
            "success": True,
            "data": {
                "status": "draft",
                "url": url,
                "expires_at": expires_at.isoformat() + "Z",
                "filename": filename,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error generating PDF for invoice {invoice_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to generate PDF")


# =============================================================================
# INVOICE ACTIVITY LOG (Tab Aktivitas)
# =============================================================================
@router.get("/{invoice_id}/activity")
async def get_invoice_activity(
    request: Request,
    invoice_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Get activity log for a sales invoice — matches Bills pattern exactly."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, status, total_amount, created_at, posted_at, voided_at,
                       voided_reason, created_by, posted_by
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )
            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Helper: resolve user name from user_id (table is "User" with capital U)
            async def resolve_actor(user_id):
                if not user_id:
                    return None
                try:
                    user = await conn.fetchrow(
                        'SELECT name, fullname FROM "User" WHERE id = $1',
                        str(user_id),
                    )
                    if user:
                        return user["fullname"] or user["name"] or None
                    return None
                except Exception:
                    return None

            activities = []

            # 1. Created
            created_actor = await resolve_actor(invoice["created_by"])
            activities.append(
                {
                    "id": f"{invoice_id}-created",
                    "type": "created",
                    "description": "Faktur dibuat",
                    "actor_name": created_actor,
                    "timestamp": invoice["created_at"].isoformat()
                    if invoice["created_at"]
                    else None,
                    "amount": float(invoice["total_amount"])
                    if invoice["total_amount"]
                    else None,
                    "details": f"Invoice #{invoice['invoice_number']}",
                }
            )

            # 2. Posted (diterbitkan)
            if invoice["posted_at"]:
                posted_actor = await resolve_actor(invoice["posted_by"])
                activities.append(
                    {
                        "id": f"{invoice_id}-posted",
                        "type": "status_changed",
                        "description": "Faktur diterbitkan",
                        "actor_name": posted_actor,
                        "timestamp": invoice["posted_at"].isoformat(),
                        "old_value": "draft",
                        "new_value": "posted",
                    }
                )

            # 3. Voided (dibatalkan)
            if invoice["voided_at"]:
                activities.append(
                    {
                        "id": f"{invoice_id}-voided",
                        "type": "voided",
                        "description": "Faktur dibatalkan",
                        "actor_name": None,
                        "timestamp": invoice["voided_at"].isoformat(),
                        "details": invoice["voided_reason"] or None,
                    }
                )

            # 4. Payments from receive_payments (not deprecated sales_invoice_payments)
            payments = await conn.fetch(
                """
                SELECT rp.id, rp.total_amount as amount, rp.payment_date, rp.payment_method,
                       rp.created_at, rp.created_by,
                       ba.account_name as bank_account_name
                FROM receive_payments rp
                LEFT JOIN receive_payment_allocations rpa ON rpa.payment_id = rp.id
                LEFT JOIN bank_accounts ba ON ba.id = rp.bank_account_id
                WHERE rpa.invoice_id = $1 AND rp.status = 'posted'
                ORDER BY rp.created_at DESC
            """,
                invoice_id,
            )
            for p in payments:
                p_actor = await resolve_actor(p["created_by"])
                activities.append(
                    {
                        "id": f"payment-{p['id']}",
                        "type": "payment",
                        "description": f"Pembayaran {'tunai' if p['payment_method'] == 'cash' else 'transfer'}",
                        "actor_name": p_actor,
                        "timestamp": p["created_at"].isoformat()
                        if p["created_at"]
                        else None,
                        "amount": float(p["amount"]) if p["amount"] else None,
                        "payment_method": p["payment_method"],
                        "bank_account_name": p["bank_account_name"],
                    }
                )

            # Sort by timestamp descending
            activities.sort(key=lambda x: x["timestamp"] or "", reverse=True)

            total = len(activities)  # noqa: F841  # pre-existing unused local
            paginated = activities[offset : offset + limit]

            return {
                "activities": paginated,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice activity {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get invoice activity")


# =============================================================================
# INVOICE JOURNAL ENTRIES (Tab Jurnal)
# =============================================================================
@router.get("/{invoice_id}/journals")
async def get_invoice_journals(
    request: Request,
    invoice_id: UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get journal entries related to a sales invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()
        offset = (page - 1) * limit

        async with pool.acquire() as conn:
            # Verify invoice exists and belongs to tenant
            invoice = await conn.fetchrow(
                """
                SELECT id, invoice_number, journal_id, cogs_journal_id
                FROM sales_invoices
                WHERE id = $1 AND tenant_id = $2
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            if not invoice:
                raise HTTPException(status_code=404, detail="Invoice not found")

            # Get journal entries that reference this invoice
            entries = await conn.fetch(
                """
                SELECT
                    je.id,
                    je.journal_number,
                    je.journal_date,
                    je.description,
                    je.source_type,
                    je.status,
                    je.total_debit,
                    je.total_credit,
                    je.created_at,
                    je.reversal_of_id,
                    je.reversed_by_id
                FROM journal_entries je
                WHERE je.tenant_id = $1
                    AND (je.source_id = $2 OR je.id = $3 OR je.id = $4)
                    AND je.status IN ('POSTED', 'VOID')
                ORDER BY je.journal_date DESC, je.created_at DESC
                LIMIT $5 OFFSET $6
                """,
                ctx["tenant_id"],
                invoice_id,
                invoice["journal_id"],
                invoice["cogs_journal_id"],
                limit,
                offset,
            )

            # Get total count
            total = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM journal_entries je
                WHERE je.tenant_id = $1
                    AND (je.source_id = $2 OR je.id = $3 OR je.id = $4)
                    AND je.status IN ('POSTED', 'VOID')
                """,
                ctx["tenant_id"],
                invoice_id,
                invoice["journal_id"],
                invoice["cogs_journal_id"],
            )

            # Build response with lines for each entry
            result_entries = []
            for entry in entries:
                # Get lines for this journal entry
                lines = await conn.fetch(
                    """
                    SELECT
                        jl.line_number,
                        a.account_code,
                        a.name as account_name,
                        jl.memo,
                        jl.debit,
                        jl.credit
                    FROM journal_lines jl
                    INNER JOIN chart_of_accounts a ON a.id = jl.account_id
                    WHERE jl.journal_id = $1
                    ORDER BY jl.line_number
                    """,
                    entry["id"],
                )

                result_entries.append(
                    {
                        "id": str(entry["id"]),
                        "journal_number": entry["journal_number"],
                        "date": entry["journal_date"].isoformat(),
                        "description": entry["description"],
                        "source_type": entry["source_type"],
                        "status": entry["status"],
                        "total_debit": float(entry["total_debit"]),
                        "total_credit": float(entry["total_credit"]),
                        "is_reversal": entry["reversal_of_id"] is not None,
                        "is_reversed": entry["reversed_by_id"] is not None,
                        "lines": [
                            {
                                "line_number": line["line_number"],
                                "account_code": line["account_code"],
                                "account_name": line["account_name"],
                                "memo": line["memo"],
                                "debit": float(line["debit"]),
                                "credit": float(line["credit"]),
                            }
                            for line in lines
                        ],
                    }
                )

            return {
                "success": True,
                "data": result_entries,
                "total": total or 0,
                "page": page,
                "limit": limit,
                "has_more": (offset + limit) < (total or 0),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice journals {invoice_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get journal entries")


# =============================================================================
# SALES INVOICE ATTACHMENTS
# =============================================================================


@router.post("/{invoice_id}/attachments", status_code=201)
async def upload_invoice_attachment(
    request: Request,
    invoice_id: UUID,
    file: UploadFile = File(..., description="Image or PDF file (max 5MB)"),
):
    """Upload an attachment to a sales invoice (stored in MinIO)."""
    try:
        ctx = get_user_context(request)
        if not ctx["user_id"]:
            raise HTTPException(status_code=401, detail="User ID required")

        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size exceeds 5MB limit")
        await file.seek(0)

        allowed_types = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"File type {file.content_type} not allowed. Use JPEG, PNG, WebP, or PDF.",
            )

        tenant_id = ctx["tenant_id"]
        user_id = ctx["user_id"]
        pool = await get_pool()

        async with pool.acquire() as conn:
            await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

            inv = await conn.fetchrow(
                "SELECT id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
                invoice_id,
                tenant_id,
            )
            if not inv:
                raise HTTPException(status_code=404, detail="Invoice not found")

            storage = get_storage_service()
            result = await storage.upload_file(
                file=file,
                tenant_id=tenant_id,
                category="invoice-attachments",
            )

            import uuid as uuid_mod

            attachment_id = uuid_mod.uuid4()
            await conn.execute(
                """INSERT INTO sales_invoice_attachments (id, invoice_id, tenant_id, filename, file_path, file_size, mime_type, uploaded_by)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                attachment_id,
                invoice_id,
                tenant_id,
                file.filename,
                result.file_path,
                len(content),
                file.content_type,
                user_id,
            )

            return {
                "success": True,
                "data": {
                    "id": str(attachment_id),
                    "filename": file.filename,
                    "url": result.url,
                    "size": len(content),
                    "mime_type": file.content_type,
                },
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error uploading attachment for invoice {invoice_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to upload attachment")


@router.get("/{invoice_id}/attachments")
async def list_invoice_attachments(
    request: Request,
    invoice_id: UUID,
):
    """List attachments for a sales invoice."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

        inv = await conn.fetchrow(
            "SELECT id FROM sales_invoices WHERE id = $1 AND tenant_id = $2",
            invoice_id,
            tenant_id,
        )
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")

        rows = await conn.fetch(
            'SELECT sa.id, sa.filename, sa.file_path, sa.file_size, sa.mime_type, sa.uploaded_at, sa.uploaded_by, COALESCE(u.name, u.fullname, u.email) AS uploaded_by_name FROM sales_invoice_attachments sa LEFT JOIN "User" u ON u.id = sa.uploaded_by::text WHERE sa.invoice_id = $1 ORDER BY sa.uploaded_at DESC',
            invoice_id,
        )

        storage = get_storage_service()

        attachments = []
        for r in rows:
            try:
                url = (
                    await storage.generate_signed_url(r["file_path"])
                    if r["file_path"]
                    else None
                )
            except Exception:
                url = None
            attachments.append(
                {
                    "id": str(r["id"]),
                    "filename": r["filename"],
                    "url": url,
                    "size": r["file_size"],
                    "mime_type": r["mime_type"],
                    "uploaded_at": r["uploaded_at"].isoformat()
                    if r["uploaded_at"]
                    else None,
                    "uploaded_by_name": r["uploaded_by_name"],
                }
            )

        return {"attachments": attachments}


@router.delete("/{invoice_id}/attachments/{attachment_id}")
async def delete_invoice_attachment(
    request: Request,
    invoice_id: UUID,
    attachment_id: UUID,
):
    """Delete a sales invoice attachment."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

        row = await conn.fetchrow(
            """SELECT sa.id, sa.file_path FROM sales_invoice_attachments sa
               JOIN sales_invoices si ON si.id = sa.invoice_id
               WHERE sa.id = $1 AND sa.invoice_id = $2 AND si.tenant_id = $3""",
            attachment_id,
            invoice_id,
            tenant_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Attachment not found")

        storage = get_storage_service()
        try:
            await storage.delete_file(row["file_path"])
        except Exception:
            pass

        await conn.execute(
            "DELETE FROM sales_invoice_attachments WHERE id = $1", attachment_id
        )

        return {"success": True, "message": "Attachment deleted"}


@router.get("/{invoice_id}/attachments/{attachment_id}/download")
async def download_invoice_attachment(
    request: Request,
    invoice_id: UUID,
    attachment_id: UUID,
):
    """Proxy-download a sales invoice attachment."""
    ctx = get_user_context(request)
    tenant_id = ctx["tenant_id"]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
        row = await conn.fetchrow(
            """SELECT sa.filename, sa.file_path, sa.mime_type
            FROM sales_invoice_attachments sa
            JOIN sales_invoices si ON si.id = sa.invoice_id
            WHERE sa.id = $1 AND sa.invoice_id = $2 AND si.tenant_id = $3""",
            attachment_id,
            invoice_id,
            tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    storage = get_storage_service()
    try:
        obj = storage.client.get_object(
            Bucket=storage.config.bucket, Key=row["file_path"]
        )
        body = obj["Body"]

        def iter_body():
            while chunk := body.read(65536):
                yield chunk
            body.close()

        return StreamingResponse(
            iter_body(),
            media_type=row["mime_type"] or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{row["filename"]}"',
                "Cache-Control": "private, max-age=3600",
            },
        )
    except Exception as e:
        logger.error(
            f"Error downloading attachment {attachment_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to download attachment")


@router.post("/{invoice_id}/fulfill")
async def fulfill_invoice(request: Request, invoice_id: UUID):
    """
    Fulfill (ship) invoice items — creates COGS journal + inventory outbound + revenue recognition.
    Used for make-to-order after stock is available.
    """
    try:
        from hashlib import sha256
        from datetime import date as dt_date

        ctx = get_user_context(request)
        pool = await get_pool()

        # Parse body manually (no Pydantic schema yet)
        body = await request.json()
        warehouse_id_str = body.get("warehouse_id")
        fulfillment_date_str = body.get("fulfillment_date", str(dt_date.today()))
        recognize_revenue = body.get("recognize_revenue", True)
        client_idempotency_key = body.get("idempotency_key")
        notes = body.get("notes")
        req_items = body.get("items", [])

        if not warehouse_id_str:
            raise HTTPException(400, "warehouse_id required")
        if not req_items:
            raise HTTPException(400, "items required")

        warehouse_id = UUID(warehouse_id_str)
        fulfillment_date = dt_date.fromisoformat(fulfillment_date_str)

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Dual-int advisory lock (Law 13)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                    ctx["tenant_id"],
                    f"INVOICE_FULFILL:{str(invoice_id)}",
                )

                # Validate invoice
                invoice = await conn.fetchrow(
                    """SELECT id, invoice_number, customer_name, total_amount, invoice_date,
                             status, fulfillment_status, warehouse_id
                       FROM sales_invoices WHERE id=$1 AND tenant_id=$2""",
                    invoice_id,
                    ctx["tenant_id"],
                )
                if not invoice:
                    raise HTTPException(404, "Invoice not found")
                if invoice["status"] != "posted":
                    raise HTTPException(400, "Hanya faktur posted yang bisa dikirim")
                if invoice["fulfillment_status"] not in ("pending", "partial"):
                    raise HTTPException(
                        400,
                        f"Status pengiriman: {invoice['fulfillment_status']}, tidak bisa dikirim lagi",
                    )

                # Period check
                await check_period_is_open(conn, ctx["tenant_id"], fulfillment_date)

                # Compute payload hash
                payload_for_hash = canonical_json(
                    {
                        "warehouse_id": str(warehouse_id),
                        "fulfillment_date": fulfillment_date_str,
                        "recognize_revenue": recognize_revenue,
                        "items": sorted(
                            [
                                {
                                    "invoice_item_id": str(i["invoice_item_id"]),
                                    "qty": str(i["quantity"]),
                                }
                                for i in req_items
                            ],
                            key=lambda x: x["invoice_item_id"],
                        ),
                    }
                )
                p_hash = sha256(payload_for_hash.encode()).hexdigest()

                # Idempotency check
                idem_key = None
                if client_idempotency_key:
                    idem_key = f"MANUAL_FULFILL:{client_idempotency_key}"
                    existing = await conn.fetchrow(
                        "SELECT id, payload_hash, status FROM invoice_fulfillments WHERE tenant_id=$1 AND idempotency_key=$2 AND status='posted'",
                        ctx["tenant_id"],
                        idem_key,
                    )
                    if existing:
                        if existing["payload_hash"] != p_hash:
                            raise HTTPException(
                                409, "Idempotency key conflict: payload berbeda"
                            )
                        return {
                            "success": True,
                            "data": {
                                "fulfillment_id": str(existing["id"]),
                                "message": "Already fulfilled (idempotent)",
                            },
                        }

                # Build items list
                items_to_fulfill = [
                    {
                        "invoice_item_id": UUID(i["invoice_item_id"]),
                        "quantity": Decimal(str(i["quantity"])),
                    }
                    for i in req_items
                ]

                # Execute shared fulfillment
                result = await _execute_fulfillment(
                    conn,
                    ctx["tenant_id"],
                    ctx["user_id"],
                    dict(invoice),
                    items_to_fulfill,
                    warehouse_id,
                    fulfillment_date,
                    recognize_revenue=recognize_revenue,
                    idempotency_key=idem_key,
                    notes=notes,
                    payload_hash=p_hash,
                )

                return {"success": True, "data": result}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fulfilling invoice {invoice_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to fulfill invoice")


@router.get("/{invoice_id}/fulfillments")
async def get_invoice_fulfillments(request: Request, invoice_id: UUID):
    """Get fulfillment history + per-item summary for an invoice."""
    try:
        ctx = get_user_context(request)
        pool = await get_pool()

        async with pool.acquire() as conn:
            # Verify invoice exists
            invoice = await conn.fetchrow(
                "SELECT id, fulfillment_status, revenue_status, total_fulfilled_qty, total_recognized_amount FROM sales_invoices WHERE id=$1 AND tenant_id=$2",
                invoice_id,
                ctx["tenant_id"],
            )
            if not invoice:
                raise HTTPException(404, "Invoice not found")

            # Get fulfillments
            fulfillments = await conn.fetch(
                """
                SELECT f.id, f.fulfillment_number, f.fulfillment_date, f.warehouse_id,
                       w.name AS warehouse_name, f.journal_id, f.revenue_journal_id,
                       f.status, f.notes, f.created_at, f.voided_at, f.void_reason
                FROM invoice_fulfillments f
                LEFT JOIN warehouses w ON w.id = f.warehouse_id
                WHERE f.invoice_id = $1 AND f.tenant_id = $2
                ORDER BY f.created_at DESC
            """,
                invoice_id,
                ctx["tenant_id"],
            )

            fulfillment_list = []
            for f in fulfillments:
                # Get items per fulfillment
                f_items = await conn.fetch(
                    """
                    SELECT fi.id, fi.invoice_item_id, fi.product_id, fi.product_name,
                           fi.quantity, fi.unit_cost, fi.total_cost, fi.revenue_amount
                    FROM invoice_fulfillment_items fi
                    WHERE fi.fulfillment_id = $1
                """,
                    f["id"],
                )

                fulfillment_list.append(
                    {
                        **dict(f),
                        "id": str(f["id"]),
                        "warehouse_id": str(f["warehouse_id"])
                        if f["warehouse_id"]
                        else None,
                        "journal_id": str(f["journal_id"]) if f["journal_id"] else None,
                        "revenue_journal_id": str(f["revenue_journal_id"])
                        if f["revenue_journal_id"]
                        else None,
                        "items": [
                            {
                                **dict(fi),
                                "id": str(fi["id"]),
                                "invoice_item_id": str(fi["invoice_item_id"]),
                                "product_id": str(fi["product_id"])
                                if fi["product_id"]
                                else None,
                                "quantity": float(fi["quantity"]),
                                "unit_cost": float(fi["unit_cost"])
                                if fi["unit_cost"]
                                else None,
                                "total_cost": float(fi["total_cost"])
                                if fi["total_cost"]
                                else None,
                                "revenue_amount": float(fi["revenue_amount"])
                                if fi["revenue_amount"]
                                else None,
                            }
                            for fi in f_items
                        ],
                    }
                )

            # Per-item summary
            item_summary = await conn.fetch(
                """
                SELECT si.id, si.description, si.quantity, si.fulfilled_qty,
                       si.allocated_amount, si.recognized_amount,
                       (si.quantity - COALESCE(si.fulfilled_qty, 0)) AS remaining_qty,
                       (COALESCE(si.allocated_amount, 0) - COALESCE(si.recognized_amount, 0)) AS deferred_amount
                FROM sales_invoice_items si
                WHERE si.invoice_id = $1
                ORDER BY si.id
            """,
                invoice_id,
            )

            return {
                "success": True,
                "data": {
                    "invoice_id": str(invoice_id),
                    "fulfillment_status": invoice["fulfillment_status"],
                    "revenue_status": invoice["revenue_status"],
                    "total_fulfilled_qty": float(invoice["total_fulfilled_qty"] or 0),
                    "total_recognized_amount": float(
                        invoice["total_recognized_amount"] or 0
                    ),
                    "fulfillments": fulfillment_list,
                    "item_summary": [
                        {
                            "id": str(s["id"]),
                            "description": s["description"],
                            "quantity": float(s["quantity"]),
                            "fulfilled_qty": float(s["fulfilled_qty"] or 0),
                            "remaining_qty": float(s["remaining_qty"] or 0),
                            "allocated_amount": float(s["allocated_amount"] or 0),
                            "recognized_amount": float(s["recognized_amount"] or 0),
                            "deferred_amount": float(s["deferred_amount"] or 0),
                        }
                        for s in item_summary
                    ],
                },
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting fulfillments for {invoice_id}: {e}", exc_info=True)
        raise HTTPException(500, "Failed to get fulfillments")
