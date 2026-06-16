"""
Bills Service - Business logic for Faktur Pembelian module.

This service handles bill CRUD operations and integrates with the
accounting kernel for AP management and journal entries.

V2 Extensions:
- BillCalculator: Pure calculation logic for pharmacy bills
- Multi-level discounts (item, invoice, cash)
- Tax calculation with DPP
- Status flow: draft -> posted -> paid
"""

import logging
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID
from datetime import date, datetime
from .status_helpers import derive_doc_status
from decimal import Decimal

_Dec = Decimal

import asyncpg  # noqa: E402
import uuid as uuid_module  # noqa: E402

from .resolve_account import resolve_account_id  # noqa: E402,F401
from .role_resolver import (  # noqa: E402
    AccountRole,
    resolve_account_id_by_role,
    resolve_account_id_by_role_if_pkp,
)
from .role_precondition import assert_required_roles_for_path  # noqa: E402
from .unit_helpers import convert_to_base_unit  # noqa: E402

# Fase D2.3: required role mappings for bills_service posting paths
# (create_bill -> post_bill -> void_bill, plus pay_bill helper).
# - AP_TRADE: vendor liability (Cr on bill, Dr on payment/void)
# - VAT_INPUT: PKP-conditional PPN input (Dr on bill, Cr on void)
# - WHT_PPH_PAYABLE: PPh withheld at vendor payment (Cr on payment)
# - INVENTORY_MERCHANDISE: default Dr account on bill
# - WIP_SUBCONTRACT: Dr account when bill linked to subcontract
#   (Fase D3.3 — was deferred 1-10650 literal, now role-resolved).
BILLS_SERVICE_REQUIRED_ROLES = [
    AccountRole.AP_TRADE,
    AccountRole.VAT_INPUT,
    AccountRole.WHT_PPH_PAYABLE,
    AccountRole.INVENTORY_MERCHANDISE,
    # Fase D3.3: subcontract bill debit -> WIP_SUBCONTRACT
    # (was hardcoded 1-10650 in subcontract ternary).
    AccountRole.WIP_SUBCONTRACT,
]

# Module-level once-flag for precondition audit.
_bills_service_precondition_checked = False


async def _ensure_bills_service_role_preconditions(pool):
    """Run role-mapping precondition once per process for bills_service.

    Fails loud (PreconditionFailedError) if any tenant lacks any required
    role mapping. After first successful check the audit is skipped.
    """
    global _bills_service_precondition_checked
    if _bills_service_precondition_checked:
        return
    await assert_required_roles_for_path(
        pool, "bills_service", BILLS_SERVICE_REQUIRED_ROLES
    )
    _bills_service_precondition_checked = True


from ..utils.sorting import build_order_by_clause  # noqa: E402
from ..utils.money import cents_to_decimal_string  # noqa: E402

# Law 16: Journal-derived amount_paid CTE (used by list_bills, get_outstanding_summary)
# Computes per-bill paid amount from journal_lines via BOTH payment table paths
BILL_JOURNAL_PAID_CTE = """
    bill_journal_paid AS (
        SELECT
            bill_id,
            COALESCE(SUM(paid), 0) AS journal_paid
        FROM (
            -- Path 1: bill_payment_allocations + bill_payments_v2
            SELECT bpa.bill_id, SUM(jl.debit) AS paid
            FROM bill_payment_allocations bpa
            JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
            JOIN journal_entries je ON je.id = bpv2.journal_id
            JOIN journal_lines jl ON jl.journal_id = je.id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE bpv2.tenant_id = $1
              AND je.status = 'POSTED'
              AND je.reversed_by_id IS NULL
              AND bpv2.journal_id IS NOT NULL
              AND coa.account_type = 'PAYABLE'
              AND jl.debit > 0
            GROUP BY bpa.bill_id
            UNION ALL
            -- Path 3: Adjustments (source_type = ADJUSTMENT, source_id = bill_id)
            SELECT je.source_id::uuid AS bill_id, SUM(jl.debit) AS paid
            FROM journal_entries je
            JOIN journal_lines jl ON jl.journal_id = je.id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND je.reversed_by_id IS NULL
              AND je.source_type = 'ADJUSTMENT'
              AND coa.account_type = 'PAYABLE'
              AND jl.debit > 0
            GROUP BY je.source_id
        ) combined
        GROUP BY bill_id
    )
"""


logger = logging.getLogger(__name__)


# =============================================================================
# BILL CALCULATOR - Pure calculation logic
# =============================================================================


class BillCalculator:
    """
    Pure calculation logic for pharmacy bills.
    Can be used standalone for preview calculations without database access.

    Calculation Flow:
    1. subtotal = sum(item.qty * item.price)
    2. item_discount_total = sum(item.qty * item.price * item.discount_percent / 100)
    3. after_item = subtotal - item_discount_total
    4. invoice_discount_total = after_item * invoice_discount_% / 100 OR invoice_discount_amount
    5. after_invoice = after_item - invoice_discount_total
    6. cash_discount_total = after_invoice * cash_discount_% / 100 OR cash_discount_amount
    7. dpp = dpp_manual OR auto (PMK 131/2024: x11/12 for PPN 12%)
    8. tax_amount = dpp * tax_rate / 100
    9. grand_total = subtotal_setelah_diskon + tax (PPN 12%) OR dpp + tax
    """

    @staticmethod
    def calculate(
        items: List[Dict],
        invoice_discount_percent: Decimal = Decimal("0"),
        invoice_discount_amount: float = 0,
        cash_discount_percent: Decimal = Decimal("0"),
        cash_discount_amount: float = 0,
        tax_rate: int = 11,
        dpp_manual: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Calculate all bill totals.

        Args:
            items: List of items with qty, price, discount_percent
            invoice_discount_percent: Invoice-level discount % (0-100)
            invoice_discount_amount: Invoice-level discount amount (used if % is 0)
            cash_discount_percent: Cash discount % (0-100)
            cash_discount_amount: Cash discount amount (used if % is 0)
            tax_rate: 0, 11, or 12
            dpp_manual: Manual DPP override (None = auto-calculate)

        Returns:
            Dict with subtotal, item_discount_total, invoice_discount_total,
            cash_discount_total, dpp, tax_amount, grand_total
        """
        # All intermediate calculations use Decimal for precision.
        # Rounding to int only at final return (PSAK/IFRS compliant).

        # Step 1 & 2: Calculate subtotal and item discounts
        subtotal = Decimal("0")
        item_discount_total = Decimal("0")

        for item in items:
            qty = Decimal(str(item.get("qty", 0)))
            price = Decimal(str(item.get("price", 0)))
            discount_pct = Decimal(str(item.get("discount_percent", 0)))

            line_subtotal = qty * price
            line_discount = line_subtotal * discount_pct / Decimal("100")

            subtotal += line_subtotal
            item_discount_total += line_discount

        after_item_discount = subtotal - item_discount_total

        # Step 3: Invoice discount (% takes precedence over amount)
        if invoice_discount_percent > 0:
            invoice_discount_total = (
                after_item_discount
                * Decimal(str(invoice_discount_percent))
                / Decimal("100")
            )
        else:
            invoice_discount_total = Decimal(str(invoice_discount_amount))

        after_invoice_discount = after_item_discount - invoice_discount_total

        # Step 4: Cash discount (% takes precedence over amount)
        if cash_discount_percent > 0:
            cash_discount_total = (
                after_invoice_discount
                * Decimal(str(cash_discount_percent))
                / Decimal("100")
            )
        else:
            cash_discount_total = Decimal(str(cash_discount_amount))

        # Step 5: DPP — PMK 131/2024
        subtotal_setelah_diskon = after_invoice_discount - cash_discount_total
        if dpp_manual is not None:
            dpp = Decimal(str(dpp_manual))
        elif tax_rate == 12:
            dpp = subtotal_setelah_diskon * Decimal("11") / Decimal("12")
        else:
            dpp = subtotal_setelah_diskon

        # Step 6: Tax
        tax_amount = dpp * Decimal(str(tax_rate)) / Decimal("100")

        # Step 7: Grand total
        if tax_rate == 12 and dpp_manual is None:
            grand_total = subtotal_setelah_diskon + tax_amount
        else:
            grand_total = dpp + tax_amount

        # 2-decimal precision throughout (PSAK/IFRS compliant)
        TWO = Decimal("0.01")
        return {
            "subtotal": float(subtotal.quantize(TWO)),
            "item_discount_total": float(item_discount_total.quantize(TWO)),
            "invoice_discount_total": float(invoice_discount_total.quantize(TWO)),
            "cash_discount_total": float(cash_discount_total.quantize(TWO)),
            "dpp": float(dpp.quantize(TWO)),
            "tax_amount": float(tax_amount.quantize(TWO)),
            "grand_total": float(grand_total.quantize(TWO)),
        }

    @staticmethod
    def calculate_item_total(
        qty: int, price: int, discount_percent: Decimal
    ) -> Dict[str, float]:
        """
        Calculate single item totals.

        Returns:
            Dict with subtotal, discount_amount, total
        """
        subtotal = Decimal(str(qty)) * Decimal(str(price))
        discount_amount = subtotal * Decimal(str(discount_percent)) / Decimal("100")
        total = subtotal - discount_amount

        TWO = Decimal("0.01")
        return {
            "subtotal": float(subtotal.quantize(TWO)),
            "discount_amount": float(discount_amount.quantize(TWO)),
            "total": float(total.quantize(TWO)),
        }


class BillsService:
    """Service for managing bills (faktur pembelian)."""

    def __init__(self, pool: asyncpg.Pool, accounting_facade=None):
        """
        Initialize BillsService.

        Args:
            pool: asyncpg connection pool
            accounting_facade: AccountingFacade instance for AP integration
        """
        self.pool = pool
        self.accounting = accounting_facade

    # =========================================================================
    # LIST BILLS
    # =========================================================================
    async def list_bills(
        self,
        tenant_id: str,
        skip: int = 0,
        limit: int = 20,
        status: str = "all",
        search: Optional[str] = None,
        sort_fields: List[Tuple[str, str]] = None,
        due_date_from: Optional[date] = None,
        due_date_to: Optional[date] = None,
        vendor_id: Optional[UUID] = None,
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        List bills with filtering, sorting, and pagination.

        Returns:
            {items: [...], total: int, has_more: bool}
        """
        if sort_fields is None:
            sort_fields = [("created_at", "desc")]

        async with self.pool.acquire() as conn:
            # Build WHERE clause
            conditions = ["b.tenant_id = $1"]
            params: List[Any] = [tenant_id]
            param_idx = 2

            # Status filter using computed status (CASE expression)
            # The raw status column may not match computed values like
            # unpaid/partial/overdue, so we use the same CASE logic as SELECT.
            if status != "all":
                computed_status_expr = """
                    CASE
                        WHEN b.status = 'void' THEN 'void'
                        WHEN b.status_v2 = 'draft' THEN 'draft'
                        WHEN COALESCE(bjp.journal_paid, 0) >= b.amount THEN 'paid'
                        WHEN COALESCE(bjp.journal_paid, 0) > 0 AND b.due_date < CURRENT_DATE THEN 'overdue'
                        WHEN COALESCE(bjp.journal_paid, 0) > 0 THEN 'partial'
                        WHEN b.due_date < CURRENT_DATE THEN 'overdue'
                        ELSE 'unpaid'
                    END
                """
                if status == "active":
                    # Exclude draft & void — for hutang/AP queries
                    conditions.append(
                        f"{computed_status_expr} NOT IN ('draft', 'void')"
                    )
                elif status == "unpaid":
                    # 'belum lunas' = semua yang belum dibayar penuh (unpaid + partial + overdue)
                    conditions.append(
                        f"{computed_status_expr} IN ('unpaid', 'partial', 'overdue')"
                    )
                else:
                    conditions.append(f"{computed_status_expr} = ${param_idx}")
                    params.append(status)
                    param_idx += 1

            # Search filter
            if search:
                words = search.strip().split()
                if len(words) == 1:
                    conditions.append(
                        f"(b.invoice_number ILIKE ${param_idx} OR b.vendor_name ILIKE ${param_idx})"
                    )
                    params.append(f"%{words[0]}%")
                    param_idx += 1
                else:
                    word_conds = []
                    for word in words:
                        word_conds.append(
                            f"(b.invoice_number ILIKE ${param_idx} OR b.vendor_name ILIKE ${param_idx})"
                        )
                        params.append(f"%{word}%")
                        param_idx += 1
                    conditions.append(f"({' AND '.join(word_conds)})")

            # Date range filter
            if due_date_from:
                conditions.append(f"b.due_date >= ${param_idx}")
                params.append(due_date_from)
                param_idx += 1

            if due_date_to:
                conditions.append(f"b.due_date <= ${param_idx}")
                params.append(due_date_to)
                param_idx += 1

            # Vendor filter
            if vendor_id:
                conditions.append(f"b.vendor_id = ${param_idx}")
                params.append(vendor_id)
                param_idx += 1

            # Amount range filter
            if amount_min is not None:
                conditions.append(f"COALESCE(b.grand_total, b.amount) >= ${param_idx}")
                params.append(amount_min)
                param_idx += 1
            if amount_max is not None:
                conditions.append(f"COALESCE(b.grand_total, b.amount) <= ${param_idx}")
                params.append(amount_max)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Build compound ORDER BY clause
            field_mapping = {
                "created_at": "b.created_at",
                "date": "b.issue_date",
                "number": "b.invoice_number",
                "supplier": "b.vendor_name",
                "due_date": "b.due_date",
                "amount": "COALESCE(b.grand_total, b.amount)",
                "balance": "(COALESCE(b.amount, 0) - COALESCE(bjp.journal_paid, 0))",
                "updated_at": "b.updated_at",
                # Status ordering: overdue(1) > unpaid(2) > partial(3) > paid(4) > void(6)
                # Law 16: uses journal-derived bjp.journal_paid
                "status": """CASE
                    WHEN b.status = 'void' THEN 6
                    WHEN b.status_v2 = 'draft' THEN 5
                    WHEN COALESCE(bjp.journal_paid, 0) >= b.amount THEN 4
                    WHEN COALESCE(bjp.journal_paid, 0) > 0 AND b.due_date < CURRENT_DATE THEN 1
                    WHEN COALESCE(bjp.journal_paid, 0) > 0 THEN 3
                    WHEN b.due_date < CURRENT_DATE THEN 1
                    ELSE 2
                END""",
                # Legacy aliases
                "vendor_name": "b.vendor_name",
                "invoice_number": "b.invoice_number",
            }

            order_by_clause = build_order_by_clause(sort_fields, field_mapping)

            # Get total count
            count_query = f"""
                WITH {BILL_JOURNAL_PAID_CTE}
                SELECT COUNT(*) FROM bills b
                LEFT JOIN bill_journal_paid bjp ON bjp.bill_id = b.id
                WHERE {where_clause}
            """
            total = await conn.fetchval(count_query, *params)

            # Get items with dynamic status calculation
            # Law 16: Journal-derived amount_paid via CTE
            query = f"""
                WITH {BILL_JOURNAL_PAID_CTE}
                SELECT
                    b.id,
                    b.invoice_number,
                    b.vendor_id,
                    b.vendor_name,
                    b.amount,
                    COALESCE(bjp.journal_paid, 0) as amount_paid,
                    (b.amount - COALESCE(bjp.journal_paid, 0)) as amount_due,
                    CASE
                        WHEN b.status = 'void' THEN 'void'
                        WHEN b.status_v2 = 'draft' THEN 'draft'
                        WHEN COALESCE(bjp.journal_paid, 0) >= b.amount THEN 'paid'
                        WHEN COALESCE(bjp.journal_paid, 0) > 0 AND b.due_date < CURRENT_DATE THEN 'overdue'
                        WHEN COALESCE(bjp.journal_paid, 0) > 0 THEN 'partial'
                        WHEN b.due_date < CURRENT_DATE THEN 'overdue'
                        ELSE 'unpaid'
                    END as status,
                    b.issue_date,
                    b.due_date,
                    b.created_at,
                    b.updated_at,
                    b.operational_status,
                    b.accounting_status
                FROM bills b
                LEFT JOIN bill_journal_paid bjp ON bjp.bill_id = b.id
                WHERE {where_clause}
                ORDER BY {order_by_clause}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = []
            for row in rows:
                # Generate initials from vendor name
                vendor_name = row["vendor_name"] or ""
                words = vendor_name.split()
                if len(words) >= 2:
                    initials = (words[0][0] + words[1][0]).upper()
                elif len(words) == 1 and len(words[0]) >= 2:
                    initials = words[0][:2].upper()
                else:
                    initials = "??"

                items.append(
                    {
                        "id": str(row["id"]),
                        "invoice_number": row["invoice_number"],
                        "vendor": {
                            "id": str(row["vendor_id"]) if row["vendor_id"] else None,
                            "name": row["vendor_name"],
                            "initials": initials,
                        },
                        "amount": int(row["amount"]),
                        "amount_paid": int(row["amount_paid"]),
                        "amount_due": int(row["amount_due"]),
                        "status": row["status"],
                        "issue_date": row["issue_date"].isoformat(),
                        "due_date": row["due_date"].isoformat(),
                        "created_at": row["created_at"].isoformat(),
                        "updated_at": row["updated_at"].isoformat(),
                        "operational_status": row.get("operational_status") or "DRAFT",
                        "doc_status": derive_doc_status(row),
                        "accounting_status": row.get("accounting_status") or "UNPOSTED",
                        "vendor_id": str(row["vendor_id"])
                        if row["vendor_id"]
                        else None,
                        "vendor_name": row["vendor_name"],
                    }
                )

            return {"items": items, "total": total, "has_more": (skip + limit) < total}

    # =========================================================================
    # GET BILL DETAIL
    # =========================================================================
    async def get_bill(self, tenant_id: str, bill_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get bill detail with items, payments, and attachments.

        Returns:
            Bill detail dict or None if not found
        """
        async with self.pool.acquire() as conn:
            # Get bill -- Law 16: journal-derived amount_paid via compute_ap_outstanding()
            bill_query = """
                SELECT
                    b.*,
                    COALESCE(ap_paid.total_paid, 0) AS journal_paid,
                    (b.amount - COALESCE(ap_paid.total_paid, 0)) as amount_due,
                    CASE
                        WHEN b.status = 'void' THEN 'void'
                        WHEN b.status_v2 = 'draft' THEN 'draft'
                        WHEN COALESCE(ap_paid.total_paid, 0) >= b.amount THEN 'paid'
                        WHEN COALESCE(ap_paid.total_paid, 0) > 0 AND b.due_date < CURRENT_DATE THEN 'overdue'
                        WHEN COALESCE(ap_paid.total_paid, 0) > 0 THEN 'partial'
                        WHEN b.due_date < CURRENT_DATE THEN 'overdue'
                        ELSE 'unpaid'
                    END as calculated_status
                FROM bills b
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit), 0) AS total_paid
                    FROM bill_payment_allocations bpa
                    JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
                    JOIN journal_entries je ON je.id = bpv2.journal_id
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE bpa.bill_id = b.id
                      AND je.status = 'POSTED'
                      AND je.reversed_by_id IS NULL
                      AND coa.account_type = 'PAYABLE'
                      AND jl.debit > 0
                ) ap_paid ON true
                WHERE b.id = $1 AND b.tenant_id = $2
            """
            bill = await conn.fetchrow(bill_query, bill_id, tenant_id)

            if not bill:
                return None

            # Get items
            items_query = """
                SELECT
                    bi.*,
                    p.nama_produk as product_name,
                    tc.name as tax_code_name
                FROM bill_items bi
                LEFT JOIN products p ON bi.product_id = p.id
                LEFT JOIN tax_codes tc ON bi.tax_code_id = tc.id
                WHERE bi.bill_id = $1
                ORDER BY bi.line_number
            """
            items = await conn.fetch(items_query, bill_id)

            # Get payments (from bill_payments_v2 via allocations)
            payments_query = """
                SELECT bp.id, bp.payment_number, bp.total_amount, bp.payment_date,
                       bp.payment_method, bp.reference_number, bp.notes, bp.status,
                       bp.created_at, bpa.amount_applied,
                       bp.posted_at, bp.posted_by, bp.journal_id, bp.bank_account_id,
                       ba.account_name AS bank_account_name,
                       COALESCE(u_created.name, u_created.fullname, u_created.email) AS created_by_name,
                       COALESCE(u_posted.name, u_posted.fullname, u_posted.email) AS posted_by_name
                FROM bill_payments_v2 bp
                INNER JOIN bill_payment_allocations bpa ON bpa.payment_id = bp.id
                LEFT JOIN bank_accounts ba ON ba.id = bp.bank_account_id
                LEFT JOIN "User" u_created ON u_created.id = bp.created_by::text
                LEFT JOIN "User" u_posted ON u_posted.id = bp.posted_by::text
                WHERE bpa.bill_id = $1
                ORDER BY bp.payment_date DESC
            """
            payments = await conn.fetch(payments_query, bill_id)

            # Get attachments
            attachments_query = """
                SELECT *
                FROM bill_attachments
                WHERE bill_id = $1
                ORDER BY uploaded_at DESC
            """
            attachments = await conn.fetch(attachments_query, bill_id)

            # Fetch document_tax_lines (Fase 2.2)
            tax_lines_rows = await conn.fetch(
                """
                SELECT
                    dtl.tax_code_id,
                    tc.name AS tax_code_name,
                    tc.rate AS tax_rate,
                    dtl.direction,
                    dtl.base_amount,
                    dtl.tax_amount
                FROM document_tax_lines dtl
                LEFT JOIN tax_codes tc ON tc.id = dtl.tax_code_id
                WHERE dtl.document_id = $1 AND dtl.document_type = 'BILL'
                  AND dtl.tenant_id = $2
                ORDER BY dtl.created_at
                """,
                bill_id,
                tenant_id,
            )
            tax_lines = [
                {
                    "tax_code_id": str(row["tax_code_id"]),
                    "tax_code_name": row["tax_code_name"] or "Unknown",
                    "tax_rate": float(row["tax_rate"] or 0),
                    "direction": row["direction"],
                    "base_amount": str(row["base_amount"]),
                    "tax_amount": str(row["tax_amount"]),
                }
                for row in tax_lines_rows
            ]
            # Build vendor info
            vendor_name = bill["vendor_name"] or ""
            words = vendor_name.split()
            if len(words) >= 2:
                initials = (words[0][0] + words[1][0]).upper()
            elif len(words) == 1 and len(words[0]) >= 2:
                initials = words[0][:2].upper()
            else:
                initials = "??"

            return {
                "id": str(bill["id"]),
                "invoice_number": bill["invoice_number"],
                "vendor": {
                    "id": str(bill["vendor_id"]) if bill["vendor_id"] else None,
                    "name": bill["vendor_name"],
                    "initials": initials,
                },
                "amount": self._money_str(bill["amount"]),
                "amount_paid": self._money_str(
                    bill["journal_paid"]
                ),  # Law 16: journal-derived
                "amount_due": self._money_str(bill["amount_due"]),
                "status": bill["calculated_status"],
                "issue_date": bill["issue_date"].isoformat(),
                "due_date": bill["due_date"].isoformat(),
                "ref_no": bill.get("ref_no"),
                "tax_rate": float(bill["tax_rate"]) if bill.get("tax_rate") else 0,
                "tax_inclusive": bool(bill.get("tax_inclusive", False)),
                "tax_code_id": str(bill["tax_code_id"])
                if bill.get("tax_code_id")
                else None,
                "tax_lines": tax_lines,
                "invoice_discount_percent": float(bill["invoice_discount_percent"])
                if bill.get("invoice_discount_percent")
                else 0,
                "invoice_discount_amount": self._money_str(
                    bill.get("invoice_discount_amount") or 0
                ),
                "cash_discount_percent": float(bill["cash_discount_percent"])
                if bill.get("cash_discount_percent")
                else 0,
                "cash_discount_amount": self._money_str(
                    bill.get("cash_discount_amount") or 0
                ),
                "dpp_manual": int(bill["dpp_manual"])
                if bill.get("dpp_manual")
                else None,
                "posted_at": bill["posted_at"].isoformat()
                if bill.get("posted_at")
                else None,
                "notes": bill["notes"],
                "subtotal": self._money_str(bill.get("subtotal") or 0),
                "item_discount_total": self._money_str(
                    bill.get("item_discount_total") or 0
                ),
                "invoice_discount_total": self._money_str(
                    bill.get("invoice_discount_total") or 0
                ),
                "cash_discount_total": self._money_str(
                    bill.get("cash_discount_total") or 0
                ),
                "dpp": self._money_str(bill.get("dpp") or 0),
                "tax_amount": self._money_str(bill.get("tax_amount") or 0),
                "grand_total": self._money_str(
                    bill.get("grand_total") or bill.get("amount") or 0
                ),
                "calculation": {
                    "subtotal": self._money_str(bill.get("subtotal") or 0),
                    "item_discount_total": self._money_str(
                        bill.get("item_discount_total") or 0
                    ),
                    "invoice_discount_total": self._money_str(
                        bill.get("invoice_discount_total") or 0
                    ),
                    "cash_discount_total": self._money_str(
                        bill.get("cash_discount_total") or 0
                    ),
                    "dpp": self._money_str(bill.get("dpp") or 0),
                    "tax_amount": self._money_str(bill.get("tax_amount") or 0),
                    "grand_total": self._money_str(
                        bill.get("grand_total") or bill.get("amount") or 0
                    ),
                },
                "operational_status": bill.get("operational_status") or "DRAFT",
                "doc_status": derive_doc_status(bill),
                "accounting_status": bill.get("accounting_status") or "UNPOSTED",
                "vendor_id": str(bill["vendor_id"]) if bill["vendor_id"] else None,
                "vendor_name": bill["vendor_name"],
                "items": [
                    {
                        "id": str(item["id"]),
                        "product_id": str(item["product_id"])
                        if item["product_id"]
                        else None,
                        "product_code": item.get("product_code"),
                        "product_name": item.get("product_name")
                        or item.get("description")
                        or "-",
                        "description": item.get("description"),
                        "qty": int(item["quantity"]),
                        "quantity": float(item["quantity"]),
                        "unit": item["unit"],
                        "price": self._money_str(item["unit_price"]),
                        "unit_price": self._money_str(item["unit_price"]),
                        "discount_percent": float(item["discount_percent"])
                        if item.get("discount_percent")
                        else 0,
                        "discount_amount": self._money_str(
                            item.get("discount_amount") or 0
                        ),
                        "total": self._money_str(item.get("subtotal") or 0),
                        "subtotal": self._money_str(item.get("subtotal") or 0),
                        "batch_no": item.get("batch_no"),
                        "exp_date": item.get("exp_date"),
                        "bonus_qty": int(item["bonus_qty"])
                        if item.get("bonus_qty")
                        else 0,
                        "tax_code_id": str(item["tax_code_id"])
                        if item.get("tax_code_id")
                        else None,
                        "tax_code_name": item.get("tax_code_name") or "",
                        "tax_rate": float(item["tax_rate"])
                        if item.get("tax_rate")
                        else 0,
                        "tax_amount": float(item["tax_amount"])
                        if item.get("tax_amount")
                        else 0,
                        "dpp": float(item["dpp"]) if item.get("dpp") else 0,
                    }
                    for item in items
                ],
                "lines": [
                    {
                        "id": str(item["id"]),
                        "product_id": str(item["product_id"])
                        if item["product_id"]
                        else None,
                        "product_name": item.get("product_name"),
                        "description": item["description"],
                        "quantity": float(item["quantity"]),
                        "unit": item["unit"],
                        "unit_price": self._money_str(item["unit_price"]),
                        "subtotal": self._money_str(item["subtotal"]),
                    }
                    for item in items
                ],
                "payments": [
                    {
                        "id": str(payment["id"]),
                        "payment_number": payment["payment_number"] or "",
                        "amount": self._money_str(payment["amount_applied"]),
                        "total_amount": self._money_str(payment["total_amount"]),
                        "payment_date": payment["payment_date"].isoformat(),
                        "payment_method": payment["payment_method"],
                        "reference": payment["reference_number"],
                        "notes": payment["notes"],
                        "status": payment["status"],
                        "created_at": payment["created_at"].isoformat(),
                        "bank_account_id": str(payment["bank_account_id"])
                        if payment.get("bank_account_id")
                        else None,
                        "bank_account_name": payment.get("bank_account_name"),
                        "journal_id": str(payment["journal_id"])
                        if payment.get("journal_id")
                        else None,
                        "created_by_name": payment.get("created_by_name"),
                        "posted_at": payment["posted_at"].isoformat()
                        if payment.get("posted_at")
                        else None,
                        "posted_by_name": payment.get("posted_by_name"),
                    }
                    for payment in payments
                ],
                "attachments": await self._map_attachments_with_urls(attachments),
                "created_at": bill["created_at"].isoformat(),
                "updated_at": bill["updated_at"].isoformat(),
            }

    # =========================================================================
    # CREATE BILL
    # =========================================================================
    async def create_bill(
        self, tenant_id: str, request: Dict[str, Any], user_id: UUID
    ) -> Dict[str, Any]:
        """
        Create a new bill with items.

        This also creates an AP record and journal entry via accounting kernel.

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # P0-Fix #4: Advisory lock for concurrent bill creation (Law 13)
                import uuid as uuid_module

                bill_uuid = uuid_module.uuid4()
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BILL_CREATE:{bill_uuid}",
                )

                # 1. Generate invoice number if not provided
                invoice_number = request.get("invoice_number")
                if not invoice_number:
                    invoice_number = await conn.fetchval(
                        "SELECT generate_bill_number($1, 'BILL')", tenant_id
                    )

                # 2. Get vendor name
                vendor_name = request.get("vendor_name")
                vendor_id = request.get("vendor_id")

                if vendor_id and not vendor_name:
                    # Look up vendor name from suppliers table
                    vendor_row = await conn.fetchrow(
                        "SELECT nama_supplier FROM suppliers WHERE id = $1",
                        str(vendor_id),
                    )
                    if vendor_row:
                        vendor_name = vendor_row["nama_supplier"]

                # Phase 4 hardening (Iron Law 30 mirror): resolve name → id, reject if unresolved.
                # Mirrors sales_invoices BUG-02 fix. Without this, bill is saved with vendor_id=NULL
                # whenever vendor_name is provided without id — orphan that breaks AP aggregation.
                if vendor_name and not vendor_id:
                    resolved_id = await conn.fetchval(
                        "SELECT id FROM suppliers WHERE tenant_id = $1 AND nama_supplier = $2 LIMIT 1",
                        tenant_id,
                        vendor_name,
                    )
                    if not resolved_id:
                        # ILIKE fallback for case-insensitive match
                        resolved_id = await conn.fetchval(
                            "SELECT id FROM suppliers WHERE tenant_id = $1 AND nama_supplier ILIKE $2 LIMIT 1",
                            tenant_id,
                            vendor_name,
                        )
                    if resolved_id:
                        vendor_id = resolved_id
                    else:
                        return {
                            "success": False,
                            "message": (
                                f"Vendor '{vendor_name}' tidak ditemukan. "
                                "Buat dulu di modul Vendor."
                            ),
                            "data": None,
                        }

                if not vendor_name:
                    return {
                        "success": False,
                        "message": "Vendor name is required",
                        "data": None,
                    }

                # 3. Calculate total amount
                items = request.get("items", [])
                total_amount = 0
                for item in items:
                    qty = Decimal(str(item["quantity"]))
                    price = int(item["unit_price"])
                    subtotal = int(qty * price)
                    item["subtotal"] = subtotal
                    total_amount += subtotal

                # 4. Insert bill
                issue_date = request.get("issue_date") or date.today()
                due_date = request["due_date"]

                bill_id = await conn.fetchval(
                    """
                    INSERT INTO bills (
                        tenant_id, invoice_number, vendor_id, vendor_name,
                        amount, issue_date, due_date, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                """,
                    tenant_id,
                    invoice_number,
                    vendor_id,
                    vendor_name,
                    total_amount,
                    issue_date,
                    due_date,
                    request.get("notes"),
                    user_id,
                )

                # 5. Insert items
                for idx, item in enumerate(items, start=1):
                    await conn.execute(
                        """
                        INSERT INTO bill_items (
                            bill_id, product_id, description, quantity,
                            unit, unit_price, subtotal, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                        bill_id,
                        item.get("product_id"),
                        item.get("description"),
                        Decimal(str(item["quantity"])),
                        item.get("unit"),
                        int(item["unit_price"]),
                        int(item["subtotal"]),
                        idx,
                    )

                # 6. Create AP in accounting kernel (REQUIRED - atomic with bill)
                # Golden Rule: Bill must ALWAYS have AP and Journal
                ap_id = None
                journal_id = None

                if self.accounting:
                    ap_result = await self.accounting.create_payable(
                        tenant_id=tenant_id,
                        supplier_name=vendor_name,
                        supplier_id=vendor_id,
                        bill_number=invoice_number,
                        bill_date=issue_date,
                        due_date=due_date,
                        amount=Decimal(total_amount),
                        source_type="BILL",
                        source_id=bill_id,
                    )

                    if not ap_result.get("success"):
                        # Rollback by raising exception - transaction will be rolled back
                        raise ValueError(
                            f"AP creation failed: {ap_result.get('error', 'Unknown error')}. "
                            "Bill creation rolled back."
                        )

                    ap_id = ap_result.get("ap_id")
                    journal_id = ap_result.get("journal_id")

                    # Update bill with AP link + status (Law 31: derived layer sync)
                    await conn.execute(
                        """
                        UPDATE bills
                        SET ap_id = $1, journal_id = $2,
                            status = 'posted', status_v2 = 'posted',
                            operational_status = 'RECEIVED',
                            accounting_status = 'POSTED',
                            posted_at = NOW(), posted_by = $4
                        WHERE id = $3
                    """,
                        ap_id,
                        journal_id,
                        bill_id,
                        user_id,
                    )

                    # UPDATE INVENTORY for inventory-tracked items
                    # Get default warehouse for tenant
                    default_warehouse = await conn.fetchrow(
                        "SELECT id FROM warehouses WHERE tenant_id = $1 AND is_default = true LIMIT 1",
                        tenant_id,
                    )
                    warehouse_id = (
                        default_warehouse["id"] if default_warehouse else None
                    )

                    for item in items:
                        product_id = item.get("product_id")
                        if not product_id:
                            continue

                        # Check if product is inventory-tracked
                        product = await conn.fetchrow(
                            """
                            SELECT id, nama_produk, item_code, track_inventory, item_type, track_batches
                            FROM products WHERE id = $1
                            """,
                            product_id,
                        )

                        if (
                            not product
                            or product["item_type"] != "goods"
                            or not product.get("track_inventory", True)
                        ):
                            continue

                        quantity = Decimal(str(item["quantity"]))
                        unit_cost = Decimal(str(item["unit_price"]))
                        total_cost = quantity * unit_cost

                        # Get current balance
                        balance_row = await conn.fetchrow(
                            """
                            SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as balance
                            FROM inventory_ledger
                            WHERE tenant_id = $1 AND product_id = $2
                            """,
                            tenant_id,
                            product_id,
                        )
                        current_balance = (
                            Decimal(str(balance_row["balance"]))
                            if balance_row
                            else Decimal("0")
                        )
                        new_balance = current_balance + quantity

                        # Calculate weighted average cost
                        avg_cost_row = await conn.fetchrow(
                            """
                            SELECT
                                COALESCE(SUM(quantity_in * unit_cost), 0) as total_value,
                                COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as total_qty
                            FROM inventory_ledger
                            WHERE tenant_id = $1 AND product_id = $2
                            """,
                            tenant_id,
                            product_id,
                        )

                        if avg_cost_row and avg_cost_row["total_qty"] > 0:
                            old_value = Decimal(str(avg_cost_row["total_value"]))
                            old_qty = Decimal(str(avg_cost_row["total_qty"]))
                            new_avg_cost = (old_value + total_cost) / (
                                old_qty + quantity
                            )
                        else:
                            new_avg_cost = unit_cost

                        # Insert inventory_ledger entry
                        await conn.execute(
                            """
                            INSERT INTO inventory_ledger (
                                tenant_id, product_id, product_code, product_name,
                                movement_type, movement_date, source_type, source_id, source_number,
                                quantity_in, quantity_out, quantity_balance,
                                unit_cost, total_cost, average_cost,
                                warehouse_id, journal_id, created_by, notes, batch_id
                            ) VALUES (
                                $1, $2, $3, $4,
                                'PURCHASE', $5, 'BILL', $6, $7,
                                $8, 0, $9,
                                $10, $11, $12,
                                $13, $14, $15, $16, $17
                            )
                            """,
                            tenant_id,
                            product_id,
                            product.get("item_code"),
                            product.get("nama_produk"),
                            issue_date,
                            bill_id,
                            invoice_number,
                            quantity,
                            new_balance,
                            unit_cost,
                            total_cost,
                            new_avg_cost,
                            warehouse_id,
                            journal_id,
                            user_id,
                            f"Purchase from {vendor_name}",
                            None,  # batch_id - legacy create_bill doesn't support batches
                        )

                        logger.info(
                            f"Inventory updated for product {product_id}: +{quantity} @ {unit_cost}"
                        )

                else:
                    # Accounting kernel not available - this is a configuration error
                    logger.error(
                        "Accounting kernel not configured - bills require AP integration"
                    )
                    raise ValueError(
                        "Accounting kernel not available. Bill creation requires AP integration."
                    )

                return {
                    "success": True,
                    "message": "Bill created successfully",
                    "data": {
                        "id": str(bill_id),
                        "invoice_number": invoice_number,
                        "amount": total_amount,
                        "status": "unpaid",
                        "created_at": datetime.now().isoformat(),
                    },
                }

    # =========================================================================
    # UPDATE BILL
    # =========================================================================
    async def update_bill(
        self, tenant_id: str, bill_id: UUID, request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update a bill. Only allowed if no payments have been made.

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Check if bill exists and is unpaid
            bill = await conn.fetchrow(
                """
                SELECT id, amount_paid, status, status_v2
                FROM bills
                WHERE id = $1 AND tenant_id = $2
            """,
                bill_id,
                tenant_id,
            )

            if not bill:
                return {"success": False, "message": "Bill not found", "data": None}

            if bill["amount_paid"] > 0:
                return {
                    "success": False,
                    "message": "Cannot update bill with payments",
                    "data": None,
                }

            if bill["status"] == "void":
                return {
                    "success": False,
                    "message": "Cannot update voided bill",
                    "data": None,
                }

            # Check status_v2 (only draft can be edited)
            if bill.get("status_v2") and bill["status_v2"] != "draft":
                return {
                    "success": False,
                    "message": f"Cannot edit bill with status '{bill['status_v2']}'. Only draft bills can be edited.",
                    "data": None,
                }

            async with conn.transaction():
                # Update bill fields
                updates = []
                params = []
                param_idx = 1

                if "invoice_number" in request and request["invoice_number"]:
                    updates.append(f"invoice_number = ${param_idx}")
                    params.append(request["invoice_number"])
                    param_idx += 1

                if "vendor_name" in request and request["vendor_name"]:
                    updates.append(f"vendor_name = ${param_idx}")
                    params.append(request["vendor_name"])
                    param_idx += 1

                if "due_date" in request and request["due_date"]:
                    updates.append(f"due_date = ${param_idx}")
                    params.append(request["due_date"])
                    param_idx += 1

                if "notes" in request:
                    updates.append(f"notes = ${param_idx}")
                    params.append(request["notes"])
                    param_idx += 1

                # Always update updated_at
                updates.append("updated_at = NOW()")

                if updates:
                    params.extend([bill_id, tenant_id])
                    query = f"""
                        UPDATE bills
                        SET {", ".join(updates)}
                        WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                    """
                    await conn.execute(query, *params)

                # Update items if provided
                if "items" in request and request["items"]:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM bill_items WHERE bill_id = $1", bill_id
                    )

                    # Insert new items
                    total_amount = 0
                    for idx, item in enumerate(request["items"], start=1):
                        qty = Decimal(str(item["quantity"]))
                        price = int(item["unit_price"])
                        subtotal = int(qty * price)
                        total_amount += subtotal

                        await conn.execute(
                            """
                            INSERT INTO bill_items (
                                bill_id, product_id, description, quantity,
                                unit, unit_price, subtotal, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                            bill_id,
                            item.get("product_id"),
                            item.get("description"),
                            qty,
                            item.get("unit"),
                            price,
                            subtotal,
                            idx,
                        )

                    # Update bill amount
                    await conn.execute(
                        """
                        UPDATE bills SET amount = $1, updated_at = NOW()
                        WHERE id = $2
                    """,
                        total_amount,
                        bill_id,
                    )

                return {
                    "success": True,
                    "message": "Bill updated successfully",
                    "data": {
                        "id": str(bill_id),
                        "updated_at": datetime.now().isoformat(),
                    },
                }

    # =========================================================================
    # DELETE BILL
    # =========================================================================
    async def delete_bill(self, tenant_id: str, bill_id: UUID) -> Dict[str, Any]:
        """
        Hard-delete a draft bill. Only allowed if doc_status='draft' AND
        no payments/allocations/dependent records exist.

        Uses derive_doc_status() (status_helpers) as authoritative source —
        the legacy `status` column defaults to 'unpaid' and is unreliable
        for draft detection. Per Iron Law, status_v2 is the lifecycle SoT.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Defensive RLS context (pool monkey-patch already sets this,
                # but explicit set is safe and idempotent).
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )

                bill = await conn.fetchrow(
                    """
                    SELECT id, amount_paid, status, status_v2,
                           accounting_status, operational_status
                    FROM bills
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                    """,
                    bill_id,
                    tenant_id,
                )

                if not bill:
                    return {"success": False, "message": "Bill not found"}

                # Use authoritative derive_doc_status helper instead of
                # brittle direct status_v2 check. Handles legacy `status`
                # column ('void') and status_v2 desync gracefully.
                doc_status = derive_doc_status(dict(bill))

                if doc_status == "void":
                    return {
                        "success": False,
                        "message": "Cannot delete voided bill",
                    }
                if doc_status != "draft":
                    return {
                        "success": False,
                        "message": (
                            f"Cannot delete bill with status '{doc_status}'. "
                            "Only draft bills can be deleted. Use void instead."
                        ),
                    }

                if (bill["amount_paid"] or 0) > 0:
                    return {
                        "success": False,
                        "message": "Cannot delete bill with payments. Void the bill instead.",
                    }

                # Pre-flight: non-cascade FK dependencies. Drafts SHOULD
                # not have any of these, but guard so DELETE returns a
                # clear 400 instead of leaking a 500 to the frontend.
                dep_checks = [
                    ("bill_payment_allocations", "pembayaran tagihan"),
                    ("vendor_deposit_applications", "aplikasi deposit vendor"),
                    ("fixed_assets", "aset tetap"),
                    ("asset_maintenance", "pemeliharaan aset"),
                    ("production_subcontracts", "subkontrak produksi"),
                ]
                for table, label in dep_checks:
                    exists = await conn.fetchval(
                        f"SELECT 1 FROM {table} WHERE bill_id = $1 LIMIT 1",
                        bill_id,
                    )
                    if exists:
                        return {
                            "success": False,
                            "message": (
                                f"Tidak dapat menghapus faktur draft: masih ada "
                                f"referensi {label}. Lepas referensi terlebih dahulu."
                            ),
                        }

                # Delete bill — bill_items + bill_attachments cascade.
                try:
                    await conn.execute(
                        "DELETE FROM bills WHERE id = $1 AND tenant_id = $2",
                        bill_id,
                        tenant_id,
                    )
                except asyncpg.ForeignKeyViolationError as fk_err:
                    return {
                        "success": False,
                        "message": (
                            "Faktur draft tidak dapat dihapus karena masih "
                            f"memiliki referensi terkait: {fk_err.detail or str(fk_err)}"
                        ),
                    }

                return {"success": True, "message": "Bill deleted successfully"}

    # =========================================================================
    # RECORD PAYMENT
    # =========================================================================
    async def record_payment(
        self, tenant_id: str, bill_id: UUID, request: Dict[str, Any], user_id: UUID
    ) -> Dict[str, Any]:
        """
        Record a payment for a bill.

        Single-transaction pattern (Iron Law 23, ARAP Rule 1):
        All operations (journal, bill_payments_v2, bank_txn, cache updates)
        happen in ONE database transaction. No facade/ap_service.

        Account handling:
        - bank_account_id (preferred): Links to bank_accounts, creates bank transaction
        - account_id (legacy): Direct CoA UUID, no bank transaction
        """
        async with self.pool.acquire() as conn:
            await _ensure_bills_service_role_preconditions(self.pool)
            async with conn.transaction():
                # 0. RLS context
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )

                # 1. Advisory lock — BEFORE any reads (Law 13)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BILL_PAYMENT:{str(bill_id)}",
                )

                # 2. Get bill with row lock
                bill = await conn.fetchrow(
                    """
                    SELECT id, amount, amount_paid, status, status_v2, ap_id, vendor_id, vendor_name
                    FROM bills
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                    """,
                    bill_id,
                    tenant_id,
                )

                if not bill:
                    return {"success": False, "message": "Bill not found", "data": None}

                if (
                    bill["status"] in ("void",)
                    or (bill.get("status_v2") or "") == "void"
                ):
                    return {
                        "success": False,
                        "message": "Cannot pay voided bill",
                        "data": None,
                    }

                # 3. Compute remaining from journal (Law 16) — NOT cached amount_paid
                journal_remaining = await conn.fetchval(
                    """
                    WITH bill_obligation AS (
                        SELECT COALESCE(SUM(jl.credit), 0) AS total_credit
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id
                        WHERE je.tenant_id = $1 AND je.status = 'POSTED'
                          AND je.reversed_by_id IS NULL
                          AND coa.account_type = 'PAYABLE' AND jl.credit > 0
                          AND je.source_type = 'BILL' AND je.source_id = $2
                    ),
                    bill_payments_settled AS (
                        SELECT COALESCE(SUM(jl.debit), 0) AS total_debit
                        FROM journal_lines jl
                        JOIN journal_entries je ON je.id = jl.journal_id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id
                        WHERE je.tenant_id = $1 AND je.status = 'POSTED'
                          AND je.reversed_by_id IS NULL
                          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
                          AND (
                              -- Via bill_payment_allocations
                              EXISTS (
                                  SELECT 1 FROM bill_payment_allocations bpa
                                  JOIN bill_payments_v2 bp ON bp.id = bpa.payment_id
                                  WHERE bpa.bill_id = $3 AND bp.journal_id = je.id
                              )

                              -- Via source_id match for PAYMENT_BILL or BILL_PAYMENT
                              -- source_type fallback removed: covered by bill_payments + bill_payment_allocations
                          )
                    )
                    SELECT GREATEST(0, bo.total_credit - bps.total_debit)
                    FROM bill_obligation bo, bill_payments_settled bps
                    """,
                    tenant_id,
                    str(bill_id),
                    bill_id,
                )
                remaining = int(journal_remaining or 0)

                # Fallback: if no journal obligation yet (bill not posted via kernel),
                # use cached amount
                if remaining == 0 and bill["amount_paid"] == 0 and bill["amount"] > 0:
                    remaining = int(bill["amount"] - bill["amount_paid"])

                payment_amount = _Dec(str(request["amount"]))

                if payment_amount <= 0:
                    return {
                        "success": False,
                        "message": "Payment amount must be positive",
                        "data": None,
                    }

                if payment_amount > remaining:
                    return {
                        "success": False,
                        "message": f"Jumlah pembayaran ({payment_amount:,}) melebihi sisa tagihan ({remaining:,})",
                        "data": None,
                    }

                payment_date = request.get("payment_date") or date.today()
                if isinstance(payment_date, str):
                    payment_date = date.fromisoformat(payment_date)

                # Fase 2.3: PPh withholding
                pph_tax_code_id = request.get("pph_tax_code_id")
                pph_amount = _Dec(str(request.get("pph_amount") or 0))

                # 4. Resolve bank account → coa_id (BankSync Rule 2)
                bank_account_id = request.get("bank_account_id")
                coa_id = request.get("account_id")
                bank_coa_id = None
                bank_account = None

                if bank_account_id:
                    if isinstance(bank_account_id, str):
                        bank_account_id = UUID(bank_account_id)

                    bank_account = await conn.fetchrow(
                        """
                        SELECT ba.id, ba.coa_id, ba.account_type, ba.account_name,
                               coa.is_header, coa.name as coa_name
                        FROM bank_accounts ba
                        JOIN chart_of_accounts coa ON coa.id = ba.coa_id
                        WHERE ba.id = $1 AND ba.tenant_id = $2 AND ba.is_active = true
                        """,
                        bank_account_id,
                        tenant_id,
                    )

                    if not bank_account:
                        return {
                            "success": False,
                            "message": "Bank account not found or inactive",
                            "data": None,
                        }

                    if bank_account["is_header"]:
                        return {
                            "success": False,
                            "message": f"Akun '{bank_account['coa_name']}' adalah akun induk. Pilih akun anak (leaf).",
                            "data": None,
                        }

                    bank_coa_id = bank_account["coa_id"]
                    coa_id = bank_coa_id

                elif coa_id:
                    if isinstance(coa_id, str):
                        coa_id = UUID(coa_id)
                    bank_coa_id = coa_id
                else:
                    return {
                        "success": False,
                        "message": "bank_account_id atau account_id wajib diisi",
                        "data": None,
                    }

                # 5. Resolve AP account (Law 27 + Fase D2.3 role)
                ap_account_id = await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.AP_TRADE
                )

                # 6. Generate payment number
                payment_number = (
                    await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'BP')", tenant_id
                    )
                    or f"BP-{date.today().strftime('%y%m')}-AUTO"
                )

                # 7. Create journal DRAFT → lines → POSTED (Law 20)
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()
                payment_id = uuid_module.uuid4()

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'BILL_PAYMENT', $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                    journal_id,
                    tenant_id,
                    payment_number,
                    payment_date,
                    f"Pembayaran ke {bill['vendor_name']}",
                    payment_id,
                    str(trace_id),
                    payment_amount,
                    user_id,
                )

                # Fase 2.3: Resolve PPh CoA
                pph_coa_id = None
                if pph_amount > 0 and pph_tax_code_id:
                    from uuid import UUID as _UUID

                    pph_coa_id = await conn.fetchval(
                        "SELECT coa_id FROM tax_codes WHERE id = $1",
                        _UUID(pph_tax_code_id)
                        if isinstance(pph_tax_code_id, str)
                        else pph_tax_code_id,
                    )
                    if not pph_coa_id:
                        # Fase D2.3: dedicated WHT_PPH_PAYABLE (2-10320 post-D1
                        # V155), NOT polluted 2-10300 fallback.
                        pph_coa_id = await resolve_account_id_by_role(
                            conn, tenant_id, AccountRole.WHT_PPH_PAYABLE
                        )

                line_number = 0

                # Dr. Accounts Payable (reduces liability)
                line_number += 1
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    ap_account_id,
                    payment_amount,
                    f"Pelunasan hutang - {bill['vendor_name']}",
                )

                # Cr. Bank/Cash (money going out) — reduced by PPh
                actual_transfer = payment_amount - pph_amount
                line_number += 1
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    bank_coa_id,
                    actual_transfer,
                    f"Pembayaran dari bank - {payment_number}",
                )

                # Cr. Utang Pajak / PPh Withheld (Fase 2.3)
                if pph_amount > 0 and pph_coa_id:
                    line_number += 1
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        line_number,
                        pph_coa_id,
                        pph_amount,
                        "PPh dipotong dari pembayaran vendor",
                    )

                # DRAFT → POSTED (triggers hash chain)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # 8. INSERT bill_payments_v2 + allocation (ARAP Rule 1, Rule 11)
                await conn.execute(
                    """
                    INSERT INTO bill_payments_v2 (
                        id, tenant_id, payment_number, vendor_id, vendor_name,
                        payment_date, payment_method, bank_account_id, bank_account_name,
                        total_amount, journal_id, status,
                        reference_number, notes, created_by,
                        posted_at, posted_by,
                        operational_status, accounting_status,
                        pph_tax_code_id, pph_amount
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9,
                        $10, $11, 'posted',
                        $12, $13, $14,
                        NOW(), $14,
                        'CONFIRMED', 'POSTED',
                        $15::uuid, $16
                    )
                    """,
                    payment_id,
                    tenant_id,
                    payment_number,
                    bill.get("vendor_id"),
                    bill["vendor_name"],
                    payment_date,
                    request["payment_method"],
                    bank_account_id if bank_account_id else None,
                    bank_account["account_name"] if bank_account else None,
                    payment_amount,
                    journal_id,
                    request.get("reference"),
                    request.get("notes"),
                    user_id,
                    UUID(pph_tax_code_id) if pph_tax_code_id else None,
                    pph_amount,
                )

                # 8b. INSERT bill_payment_allocations
                await conn.execute(
                    """
                    INSERT INTO bill_payment_allocations (
                        id, payment_id, bill_id,
                        remaining_before, amount_applied, remaining_after,
                        created_at
                    ) VALUES (
                        gen_random_uuid(), $1, $2,
                        $3, $4, $5,
                        NOW()
                    )
                    """,
                    payment_id,
                    bill_id,
                    remaining,
                    payment_amount,
                    remaining - payment_amount,
                )

                # 9. INSERT bank_transaction (with journal_id, BankSync Rule 1)
                bank_transaction_id = None
                if bank_account:
                    actual_bank_transfer = payment_amount - pph_amount
                    if bank_account["account_type"] == "credit_card":
                        tx_amount = actual_bank_transfer
                        tx_type = "charge"
                    else:
                        tx_amount = -actual_bank_transfer
                        tx_type = "payment_made"

                    bank_transaction_id = uuid_module.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date,
                            transaction_type, amount, running_balance,
                            reference_type, reference_id, description,
                            payee_payer, journal_id, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, 0, 'bill', $7, $8, $9, $10, $11)
                        """,
                        bank_transaction_id,
                        tenant_id,
                        bank_account["id"],
                        payment_date,
                        tx_type,
                        tx_amount,
                        bill_id,
                        f"Payment for {bill['vendor_name']}",
                        bill["vendor_name"],
                        journal_id,
                        user_id,
                    )

                # 9.5 PPh rider: withholding_tax_records (Fase 2.3)
                if pph_amount > 0 and pph_tax_code_id:
                    from uuid import UUID as _UUID

                    _pph_tc_id = (
                        _UUID(pph_tax_code_id)
                        if isinstance(pph_tax_code_id, str)
                        else pph_tax_code_id
                    )
                    pph_tc = await conn.fetchrow(
                        "SELECT code, rate, tax_type FROM tax_codes WHERE id = $1",
                        _pph_tc_id,
                    )
                    vendor_npwp = None  # vendors table has no npwp column yet
                    pph_rate = _Dec(str(pph_tc["rate"])) if pph_tc else _Dec("2")
                    pph_dpp = (
                        (_Dec(str(pph_amount)) / pph_rate * 100).quantize(_Dec("1"))
                        if pph_rate > 0
                        else _Dec("0")
                    )
                    tax_period = payment_date.strftime("%Y%m")

                    await conn.execute(
                        """
                        INSERT INTO withholding_tax_records (
                            id, tenant_id, direction, tax_code_id,
                            document_type, document_id, payment_id, journal_id,
                            vendor_id, npwp, tax_period,
                            base_amount, tax_amount, status
                        ) VALUES (
                            $1, $2, 'cut', $3,
                            'BILL_PAYMENT', $4, $5, $6,
                            $7, $8, $9,
                            $10, $11, 'recorded'
                        )
                        """,
                        uuid_module.uuid4(),
                        tenant_id,
                        _pph_tc_id,
                        payment_id,
                        payment_id,
                        journal_id,
                        bill.get("vendor_id"),
                        vendor_npwp,
                        tax_period,
                        pph_dpp,
                        pph_amount,
                    )

                # 10. Update bill cache (Law 21 — write-side only)
                new_paid = int(bill["amount_paid"]) + payment_amount
                new_status = "paid" if new_paid >= int(bill["amount"]) else "partial"

                await conn.execute(
                    """
                    UPDATE bills SET amount_paid = $1, status = $2
                    WHERE id = $3 AND tenant_id = $4
                    """,
                    new_paid,
                    new_status,
                    bill_id,
                    tenant_id,
                )

                # 11. Update AP cache (if exists)
                if bill["ap_id"]:
                    await conn.execute(
                        """
                        UPDATE accounts_payable
                        SET amount_paid = amount_paid + $1,
                            status = CASE
                                WHEN amount_paid + $1 >= amount THEN 'PAID'
                                WHEN amount_paid + $1 > 0 THEN 'PARTIAL'
                                ELSE status
                            END,
                            updated_at = NOW()
                        WHERE id = $2 AND tenant_id = $3
                        """,
                        payment_amount,
                        bill["ap_id"],
                        tenant_id,
                    )

                logger.info(
                    f"Bill payment recorded: bill={bill_id}, amount={payment_amount}, "
                    f"journal={journal_id}, bank_txn={bank_transaction_id}"
                )

                return {
                    "success": True,
                    "message": "Payment recorded successfully",
                    "created_payment_id": str(payment_id),
                    "data": {
                        "id": str(payment_id),
                        "bill_id": str(bill_id),
                        "amount": payment_amount,
                        "journal_id": str(journal_id),
                        "bill_status": new_status,
                        "amount_due": int(bill["amount"]) - new_paid,
                        "bank_transaction_id": str(bank_transaction_id)
                        if bank_transaction_id
                        else None,
                    },
                }

    async def mark_paid(
        self, tenant_id: str, bill_id: UUID, request: Dict[str, Any], user_id: UUID
    ) -> Dict[str, Any]:
        """
        Mark a bill as fully paid (pay the remaining balance).

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Get bill
            bill = await conn.fetchrow(
                """
                SELECT id, amount, amount_paid, status
                FROM bills
                WHERE id = $1 AND tenant_id = $2
            """,
                bill_id,
                tenant_id,
            )

            if not bill:
                return {"success": False, "message": "Bill not found", "data": None}

            if bill["status"] == "void":
                return {
                    "success": False,
                    "message": "Cannot pay voided bill",
                    "data": None,
                }

            if bill["status"] == "paid":
                return {
                    "success": False,
                    "message": "Bill is already paid",
                    "data": None,
                }

            amount_due = bill["amount"] - bill["amount_paid"]

            # Create payment for remaining amount
            payment_request = {
                "amount": amount_due,
                "payment_method": request["payment_method"],
                "reference": request.get("reference"),
                "notes": request.get("notes", "Full payment"),
            }

            # Pass through bank_account_id or account_id
            if request.get("bank_account_id"):
                payment_request["bank_account_id"] = request["bank_account_id"]
            if request.get("account_id"):
                payment_request["account_id"] = request["account_id"]

            return await self.record_payment(
                tenant_id, bill_id, payment_request, user_id
            )

    # =========================================================================
    # VOID BILL
    # =========================================================================
    async def void_bill(
        self, tenant_id: str, bill_id: UUID, request: Dict[str, Any], user_id: UUID
    ) -> Dict[str, Any]:
        """
        Void a bill following Iron Laws:
        - Law 2: Journal Immutability — creates REVERSAL journal, not delete
        - Law 3: Append-Only — inventory reversed via new ledger entry
        - Law 4: Double-Entry — reversal must balance
        - Law 13: Advisory lock BILL_VOID:{bill_id} before any reads
        - Law 20: DRAFT->POSTED for hash chain integrity
        - Law 23: Single atomic transaction

        P4-fix: Rewrites D1-D7 defects.
        D1: Advisory lock added
        D2: Single transaction (no facade delegation)
        D3: Journal failure raises exception (no silent swallow)
        D4: WAC formula correct (snapshot, no recalc on outbound)
        D5: inventory_ledger.journal_id -> reversal journal (not original)
        D6: source_type = BILL_VOID (not BILL)
        D7: TOCTOU fix (re-read after lock with FOR UPDATE)
        """
        from .inventory_helpers import record_inventory_reversal

        async with self.pool.acquire() as conn:
            await _ensure_bills_service_role_preconditions(self.pool)
            # Pre-check: fast fail before lock (non-authoritative)
            bill_exists = await conn.fetchrow(
                "SELECT id, status FROM bills WHERE id = $1 AND tenant_id = $2",
                bill_id,
                tenant_id,
            )
            if not bill_exists:
                return {"success": False, "message": "Bill not found", "data": None}
            if bill_exists["status"] == "void":
                return {
                    "success": False,
                    "message": "Bill is already voided",
                    "data": None,
                }

            async with conn.transaction():
                # D1 FIX: Advisory lock FIRST — Law 13
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BILL_VOID:{str(bill_id)}",
                )

                # D7 FIX: Re-read after lock with FOR UPDATE (TOCTOU)
                bill = await conn.fetchrow(
                    """
                    SELECT id, status, status_v2, journal_id, ap_id, amount,
                           invoice_number, issue_date, vendor_name, vendor_id,
                           warehouse_id
                    FROM bills
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                    """,
                    bill_id,
                    tenant_id,
                )

                if not bill:
                    return {"success": False, "message": "Bill not found", "data": None}

                if (
                    bill["status"] in ("void",)
                    or (bill.get("status_v2") or "") == "void"
                ):
                    return {
                        "success": False,
                        "message": "Bill is already voided",
                        "data": None,
                    }

                # Check journal-derived payments (Law 16 — NOT amount_paid cache)
                journal_paid = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(paid), 0) FROM (
                        SELECT SUM(jl.debit) AS paid
                        FROM bill_payment_allocations bpa
                        JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
                        JOIN journal_entries je ON je.id = bpv2.journal_id
                        JOIN journal_lines jl ON jl.journal_id = je.id
                        JOIN chart_of_accounts coa ON coa.id = jl.account_id
                        WHERE bpa.bill_id = $1 AND bpv2.tenant_id = $2
                          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                          AND bpv2.journal_id IS NOT NULL
                          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
                          AND je.source_type NOT IN ('REVERSAL', 'INVOICE_REVERSAL')
                    ) combined
                    """,
                    bill_id,
                    tenant_id,
                )

                if journal_paid and journal_paid > 0:
                    return {
                        "success": False,
                        "message": "Cannot void bill with payments. Refund the payments first.",
                        "data": None,
                    }

                reason = request.get("reason", "Voided")
                reversal_journal_id = None
                today = date.today()

                # ============================================================
                # 1. Create REVERSAL Journal (D2/D3 FIX: direct, not via facade)
                # Law 2: Immutability — reversal, not delete
                # Law 20: DRAFT->POSTED for hash chain
                # ============================================================
                if bill["journal_id"]:
                    # Read original journal lines to create exact mirror
                    original_lines = await conn.fetch(
                        """
                        SELECT account_id, debit, credit, memo, line_number
                        FROM journal_lines
                        WHERE journal_id = $1
                        ORDER BY line_number
                        """,
                        bill["journal_id"],
                    )

                    if not original_lines:
                        raise ValueError(
                            f"Bill {bill_id} has journal_id {bill['journal_id']} "
                            "but no journal lines. Data integrity issue."
                        )

                    total_amount = sum(
                        Decimal(str(line["debit"])) for line in original_lines
                    )

                    # Get next reversal journal number
                    # Self-healing canonical generator (V176): emits REV, bumps REV counter.
                    rev_journal_number = await conn.fetchval(
                        "SELECT get_next_journal_number($1, $2, $3)",
                        tenant_id,
                        "REV",
                        today,
                    )

                    from uuid import uuid4 as _uuid4

                    reversal_journal_id = _uuid4()
                    trace_id = str(_uuid4())

                    # Create reversal journal (DRAFT first — Law 20)
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            total_debit, total_credit,
                            status, created_by, reversal_of_id, reversal_reason
                        ) VALUES (
                            $1, $2, $3, $4, $5, 'REVERSAL', $6, $7,
                            $8, $8, 'DRAFT', $9, $10, $11
                        )
                        """,
                        reversal_journal_id,
                        tenant_id,
                        rev_journal_number,
                        today,
                        f"VOID: Bill {bill['invoice_number']} - {bill['vendor_name']}",
                        bill_id,
                        trace_id,
                        total_amount,
                        user_id,
                        bill["journal_id"],  # reversal_of_id
                        reason,
                    )

                    # Mirror journal lines (swap debit <-> credit)
                    for idx, line in enumerate(original_lines, 1):
                        await conn.execute(
                            """
                            INSERT INTO journal_lines
                                (journal_id, account_id, debit, credit, memo, line_number)
                            VALUES ($1, $2, $3, $4, $5, $6)
                            """,
                            reversal_journal_id,
                            line["account_id"],
                            line["credit"],  # original credit -> reversal debit
                            line["debit"],  # original debit -> reversal credit
                            f"VOID: {line['memo'] or bill['invoice_number']}",
                            idx,
                        )

                    # DRAFT -> POSTED (Law 20: triggers hash chain)
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
                        bill["journal_id"],
                        reversal_journal_id,
                    )

                    logger.info(
                        f"Bill reversal journal created: {reversal_journal_id} "
                        f"(reversal of {bill['journal_id']})"
                    )

                # ============================================================
                # 2. Reverse Inventory Ledger (D4/D5/D6 FIX: shared helper)
                # milkyhoop-inventory Rule 9: Atomic reversal of both layers
                # ============================================================
                reversed_items = await record_inventory_reversal(
                    conn=conn,
                    tenant_id=tenant_id,
                    source_type="BILL",
                    source_id=bill_id,
                    reversal_journal_id=reversal_journal_id or bill_id,
                    created_by=user_id,
                    reversal_date=today,
                    notes_prefix=f"VOID bill {bill['invoice_number']}",
                )

                if reversed_items:
                    logger.info(
                        f"Inventory reversed for void bill {bill_id}: "
                        f"{len(reversed_items)} products"
                    )

                # ============================================================
                # 3. Mirror Bank Transactions (BankSync Rule 3)
                # Bills are accrual — typically no bank_txn on posting journal.
                # Check anyway for edge cases.
                # ============================================================
                if reversal_journal_id and bill["journal_id"]:
                    bank_txns = await conn.fetch(
                        """
                        SELECT id, bank_account_id, amount, transaction_type,
                               description
                        FROM bank_transactions
                        WHERE journal_id = $1 AND tenant_id = $2
                        """,
                        bill["journal_id"],
                        tenant_id,
                    )

                    for bt in bank_txns:
                        from uuid import uuid4 as _uuid4

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
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, 0,
                                'bill_void', $7, $8, $9, $10
                            )
                            """,
                            _uuid4(),
                            tenant_id,
                            bt["bank_account_id"],
                            today,
                            reversed_type,
                            -bt["amount"],
                            bill_id,
                            f"VOID: Reversal of {bt['description']}",
                            reversal_journal_id,
                            user_id,
                        )
                        logger.info(
                            f"Bank transaction reversed for bill void: {bt['id']}"
                        )

                # ============================================================
                # 4. Update AP status (if exists)
                # ============================================================
                if bill.get("ap_id"):
                    await conn.execute(
                        """
                        UPDATE accounts_payable
                        SET status = 'VOID', updated_at = NOW()
                        WHERE id = $1
                        """,
                        bill["ap_id"],
                    )

                # ============================================================
                # 5. Update bill status LAST
                # ============================================================
                await conn.execute(
                    """
                    UPDATE bills
                    SET status = 'void',
                        status_v2 = 'void',
                        operational_status = 'VOID',
                        accounting_status = 'REVERSED',
                        voided_at = NOW(),
                        voided_reason = $1,
                        updated_at = NOW()
                    WHERE id = $2
                    """,
                    reason,
                    bill_id,
                )

                # Clean up document_tax_lines on void
                await conn.execute(
                    "DELETE FROM document_tax_lines WHERE document_id = $1 AND tenant_id = $2",
                    bill_id,
                    tenant_id,
                )

                logger.info(f"Bill voided: {bill_id}, reason: {reason}")

                return {
                    "success": True,
                    "message": "Bill voided successfully",
                    "data": {
                        "id": str(bill_id),
                        "status": "void",
                        "voided_at": datetime.now().isoformat(),
                        "voided_reason": reason,
                        "reversal_journal_id": str(reversal_journal_id)
                        if reversal_journal_id
                        else None,
                    },
                }

    # =========================================================================
    # GET SUMMARY
    # =========================================================================
    async def get_summary(
        self, tenant_id: str, period: str = "current_month"
    ) -> Dict[str, Any]:
        """
        Get bills summary statistics.

        Args:
            period: "current_month", "last_month", "current_year", or "YYYY-MM"

        Returns:
            Summary with breakdown by status
        """
        async with self.pool.acquire() as conn:
            # Determine date range
            today = date.today()

            if period == "current_month":
                start_date = today.replace(day=1)
                if today.month == 12:
                    end_date = today.replace(year=today.year + 1, month=1, day=1)
                else:
                    end_date = today.replace(month=today.month + 1, day=1)
                period_label = today.strftime("%B %Y")
            elif period == "last_month":
                if today.month == 1:
                    start_date = today.replace(year=today.year - 1, month=12, day=1)
                else:
                    start_date = today.replace(month=today.month - 1, day=1)
                end_date = today.replace(day=1)
                period_label = start_date.strftime("%B %Y")
            elif period == "current_year":
                start_date = today.replace(month=1, day=1)
                end_date = today.replace(year=today.year + 1, month=1, day=1)
                period_label = str(today.year)
            else:
                # Assume YYYY-MM format
                try:
                    year, month = map(int, period.split("-"))
                    start_date = date(year, month, 1)
                    if month == 12:
                        end_date = date(year + 1, 1, 1)
                    else:
                        end_date = date(year, month + 1, 1)
                    period_label = start_date.strftime("%B %Y")
                except ValueError:
                    start_date = today.replace(day=1)
                    end_date = today
                    period_label = today.strftime("%B %Y")

            # Get summary statistics -- Law 16: journal-derived amount_paid via CTE
            # NOTE: amount = sisa tagihan yang belum dibayar (remaining), bukan total faktur
            query = f"""
                WITH {BILL_JOURNAL_PAID_CTE}
                SELECT
                    COUNT(*) as total_count,
                    COALESCE(SUM(b.amount), 0) as total_amount,
                    COALESCE(SUM(b.amount - COALESCE(bjp.journal_paid, 0)), 0) as total_remaining,
                    COUNT(DISTINCT b.vendor_name) as vendor_count,
                    -- Paid: sudah lunas, sisa = 0
                    COUNT(*) FILTER (WHERE COALESCE(bjp.journal_paid, 0) >= b.amount AND b.status_v2 NOT IN ('draft', 'void')) as paid_count,
                    0 as paid_remaining,
                    -- Partial: bayar sebagian, sisa = amount - journal_paid
                    COUNT(*) FILTER (WHERE COALESCE(bjp.journal_paid, 0) > 0 AND COALESCE(bjp.journal_paid, 0) < b.amount AND b.status_v2 NOT IN ('draft', 'void')) as partial_count,
                    COALESCE(SUM(b.amount - COALESCE(bjp.journal_paid, 0)) FILTER (WHERE COALESCE(bjp.journal_paid, 0) > 0 AND COALESCE(bjp.journal_paid, 0) < b.amount AND b.status_v2 NOT IN ('draft', 'void')), 0) as partial_remaining,
                    -- Unpaid: belum bayar sama sekali, sisa = amount (full)
                    COUNT(*) FILTER (WHERE COALESCE(bjp.journal_paid, 0) = 0 AND b.due_date >= CURRENT_DATE AND b.status_v2 NOT IN ('draft', 'void')) as unpaid_count,
                    COALESCE(SUM(b.amount) FILTER (WHERE COALESCE(bjp.journal_paid, 0) = 0 AND b.due_date >= CURRENT_DATE AND b.status_v2 NOT IN ('draft', 'void')), 0) as unpaid_remaining,
                    -- Overdue: jatuh tempo dan belum lunas, sisa = amount - journal_paid
                    COUNT(*) FILTER (WHERE COALESCE(bjp.journal_paid, 0) < b.amount AND b.due_date < CURRENT_DATE AND b.status_v2 NOT IN ('draft', 'void')) as overdue_count,
                    COALESCE(SUM(b.amount - COALESCE(bjp.journal_paid, 0)) FILTER (WHERE COALESCE(bjp.journal_paid, 0) < b.amount AND b.due_date < CURRENT_DATE AND b.status_v2 NOT IN ('draft', 'void')), 0) as overdue_remaining
                FROM bills b
                LEFT JOIN bill_journal_paid bjp ON bjp.bill_id = b.id
                WHERE b.tenant_id = $1
                    AND b.issue_date >= $2
                    AND b.issue_date < $3
                    AND b.status_v2 NOT IN ('draft', 'void')
            """

            row = await conn.fetchrow(query, tenant_id, start_date, end_date)

            total_amount = int(row["total_amount"])
            total_remaining = int(row["total_remaining"])

            def calc_percentage(remaining):
                if total_remaining == 0:
                    return 0
                return round((remaining / total_remaining) * 100, 1)

            return {
                "success": True,
                "data": {
                    "period": period,
                    "period_label": period_label,
                    "total_amount": total_amount,
                    "total_remaining": total_remaining,
                    "total_count": row["total_count"],
                    "vendor_count": row["vendor_count"],
                    "breakdown": {
                        "paid": {
                            "count": row["paid_count"],
                            "amount": int(row["paid_remaining"]),
                            "percentage": calc_percentage(row["paid_remaining"]),
                        },
                        "partial": {
                            "count": row["partial_count"],
                            "amount": int(row["partial_remaining"]),
                            "percentage": calc_percentage(row["partial_remaining"]),
                        },
                        "unpaid": {
                            "count": row["unpaid_count"],
                            "amount": int(row["unpaid_remaining"]),
                            "percentage": calc_percentage(row["unpaid_remaining"]),
                        },
                        "overdue": {
                            "count": row["overdue_count"],
                            "amount": int(row["overdue_remaining"]),
                            "percentage": calc_percentage(row["overdue_remaining"]),
                        },
                    },
                },
            }

    # =========================================================================
    # GET OUTSTANDING SUMMARY (No period filter)
    # =========================================================================
    async def get_outstanding_summary(self, tenant_id: str) -> Dict[str, Any]:
        """
        Get outstanding bills summary - ALL unpaid bills regardless of issue date.

        This is the proper accounting view for current outstanding payables.
        Unlike get_summary() which filters by period, this shows the current
        state of all unpaid bills.

        Status definitions (mutually exclusive):
        - overdue:  remaining > 0 AND due_date < TODAY
        - unpaid:   remaining = total (no payment) AND due_date >= TODAY
        - partial:  remaining > 0 AND remaining < total AND due_date >= TODAY

        Returns:
            Summary with breakdown by payment status, counts, and urgency metrics
        """
        async with self.pool.acquire() as conn:
            today = date.today()

            # ARAP Rule 5: Single source of truth via compute_ap_outstanding()
            # No inline CTE — all amounts derived from the same DB function
            # that all other endpoints use (vendors, dashboard, reports)
            query = """
                WITH ap AS (
                    SELECT bill_id, bill_number, due_date, bill_status,
                           bill_total, paid_amount, outstanding
                    FROM compute_ap_outstanding($1)
                )
                SELECT
                    -- Total outstanding
                    COUNT(*) as total_count,
                    COALESCE(SUM(outstanding), 0) as total_outstanding,
                    COUNT(DISTINCT (SELECT vendor_id FROM bills WHERE id = ap.bill_id)) as vendor_count,

                    -- Paid: bills that exist in bills table but NOT in ap (outstanding=0, filtered out by function)
                    (SELECT COUNT(*) FROM bills WHERE tenant_id = $1
                     AND status_v2 NOT IN ('draft', 'void')
                     AND id NOT IN (SELECT bill_id FROM ap)) as paid_count,

                    -- Partial: paid_amount > 0 AND outstanding > 0, not overdue
                    COUNT(*) FILTER (WHERE paid_amount > 0 AND outstanding > 0
                        AND (due_date >= CURRENT_DATE OR due_date IS NULL)) as partial_count,
                    COALESCE(SUM(outstanding) FILTER (WHERE paid_amount > 0 AND outstanding > 0
                        AND (due_date >= CURRENT_DATE OR due_date IS NULL)), 0) as partial_amount,

                    -- Unpaid: paid_amount = 0, not overdue
                    COUNT(*) FILTER (WHERE paid_amount = 0
                        AND (due_date >= CURRENT_DATE OR due_date IS NULL)) as unpaid_count,
                    COALESCE(SUM(outstanding) FILTER (WHERE paid_amount = 0
                        AND (due_date >= CURRENT_DATE OR due_date IS NULL)), 0) as unpaid_amount,

                    -- Overdue: due_date < today AND outstanding > 0
                    COUNT(*) FILTER (WHERE due_date < CURRENT_DATE) as overdue_count,
                    COALESCE(SUM(outstanding) FILTER (WHERE due_date < CURRENT_DATE), 0) as overdue_amount,

                    -- Urgency
                    COALESCE(MAX(CURRENT_DATE - due_date) FILTER (WHERE due_date < CURRENT_DATE), 0) as overdue_oldest_days,
                    COALESCE(MAX(outstanding) FILTER (WHERE due_date < CURRENT_DATE), 0) as overdue_largest,
                    COUNT(*) FILTER (WHERE due_date >= CURRENT_DATE
                        AND due_date <= CURRENT_DATE + INTERVAL '7 days') as due_within_7_days_count,
                    COALESCE(SUM(outstanding) FILTER (WHERE due_date >= CURRENT_DATE
                        AND due_date <= CURRENT_DATE + INTERVAL '7 days'), 0) as due_within_7_days_amount
                FROM ap
            """

            row = await conn.fetchrow(query, tenant_id)

            # Fix A: per-vendor aggregation for deterministic AP rollup intent.
            # Iron Law 1: journal-derived via compute_ap_outstanding().
            # Iron Law 25: Decimal serialized as str.
            by_vendor_query = """
                WITH ap AS (
                    SELECT vendor_id, vendor_name, bill_id, outstanding
                    FROM compute_ap_outstanding($1)
                )
                SELECT
                    ap.vendor_id,
                    -- Group by vendor_id only; resolve display name from master
                    -- first, then fall back to bill snapshot. Prevents duplicate
                    -- rows when legacy bill snapshots disagree with master.
                    COALESCE(MAX(v.name), MAX(ap.vendor_name), '(Tanpa Vendor)') AS name,
                    COUNT(ap.bill_id) AS bill_count,
                    COALESCE(SUM(ap.outstanding), 0) AS total_outstanding
                FROM ap
                LEFT JOIN vendors v
                       ON v.id = ap.vendor_id AND v.tenant_id = $1
                GROUP BY ap.vendor_id
                HAVING COALESCE(SUM(ap.outstanding), 0) > 0
                ORDER BY total_outstanding DESC
            """
            by_vendor_rows = await conn.fetch(by_vendor_query, tenant_id)
            by_vendor = [
                {
                    "vendor_id": str(r["vendor_id"])
                    if r["vendor_id"] is not None
                    else None,
                    "name": r["name"],
                    "count": int(r["bill_count"]),
                    "total_outstanding": str(r["total_outstanding"]),
                }
                for r in by_vendor_rows
            ]

            total_outstanding = int(row["total_outstanding"])
            overdue_amount = int(row["overdue_amount"])
            unpaid_amount = int(row["unpaid_amount"])
            partial_amount = int(row["partial_amount"])

            def calc_percentage(amount):
                if total_outstanding == 0:
                    return 0
                return round((amount / total_outstanding) * 100, 1)

            return {
                "success": True,
                "data": {
                    "as_of_date": today.isoformat(),
                    # Flat amounts for easy access
                    "amounts": {
                        "outstanding": total_outstanding,
                        "overdue": overdue_amount,
                        "unpaid": unpaid_amount,
                        "partial": partial_amount,
                    },
                    # Flat counts for easy access
                    "counts": {
                        "total": row["total_count"],
                        "overdue": row["overdue_count"],
                        "unpaid": row["unpaid_count"],
                        "partial": row["partial_count"],
                    },
                    # Urgency metrics for alerts
                    "urgency": {
                        "overdue_oldest_days": row["overdue_oldest_days"],
                        "overdue_largest": int(row["overdue_largest"]),
                        "due_within_7_days": int(row["due_within_7_days_amount"]),
                        "due_within_7_days_count": row["due_within_7_days_count"],
                    },
                    # Fix A: per-vendor breakdown for deterministic AP rollup intent
                    "by_vendor": by_vendor,
                    # Legacy fields for backward compatibility
                    "total_outstanding": total_outstanding,
                    "total_count": row["total_count"],
                    "vendor_count": row["vendor_count"],
                    "breakdown": {
                        "paid": {
                            "count": row["paid_count"],
                            "amount": 0,
                            "percentage": 0,
                        },
                        "partial": {
                            "count": row["partial_count"],
                            "amount": partial_amount,
                            "percentage": calc_percentage(partial_amount),
                        },
                        "unpaid": {
                            "count": row["unpaid_count"],
                            "amount": unpaid_amount,
                            "percentage": calc_percentage(unpaid_amount),
                        },
                        "overdue": {
                            "count": row["overdue_count"],
                            "amount": overdue_amount,
                            "percentage": calc_percentage(overdue_amount),
                        },
                    },
                },
            }

    # =========================================================================
    # V2 METHODS - Extended for Pharmacy
    # =========================================================================

    async def create_bill_v2(
        self, tenant_id: str, request: Dict[str, Any], user_id: UUID
    ) -> Dict[str, Any]:
        """
        Create a new pharmacy bill with extended fields (V2).

        Features:
        - Multi-level discounts (item, invoice, cash)
        - Tax calculation with DPP
        - Auto-create vendor if vendor_name provided without vendor_id
        - Auto-generate invoice number (format: PB-YYMM-0001)
        - Support draft and posted status

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            await _ensure_bills_service_role_preconditions(self.pool)
            async with conn.transaction():
                # 1. Generate invoice number if not provided
                invoice_number = request.get("invoice_number")
                if not invoice_number:
                    invoice_number = await conn.fetchval(
                        "SELECT generate_purchase_bill_number($1)", tenant_id
                    )

                # 2. Resolve vendor (auto-create if needed)
                vendor_id = request.get("vendor_id")
                vendor_name = request.get("vendor_name")

                if vendor_id:
                    # Look up vendor name from vendors table
                    vendor_row = await conn.fetchrow(
                        "SELECT id, name FROM vendors WHERE id = $1 AND tenant_id = $2",
                        vendor_id,
                        tenant_id,
                    )
                    if vendor_row:
                        vendor_name = vendor_row["name"]
                    else:
                        return {
                            "success": False,
                            "message": f"Vendor with ID {vendor_id} not found",
                            "data": None,
                        }
                elif vendor_name:
                    # Auto-create vendor if vendor_name provided
                    existing_vendor = await conn.fetchrow(
                        "SELECT id, name FROM vendors WHERE tenant_id = $1 AND name = $2",
                        tenant_id,
                        vendor_name,
                    )
                    if existing_vendor:
                        vendor_id = existing_vendor["id"]
                    else:
                        # Create new vendor
                        vendor_id = await conn.fetchval(
                            """
                            INSERT INTO vendors (tenant_id, name, created_by)
                            VALUES ($1, $2, $3)
                            RETURNING id
                        """,
                            tenant_id,
                            vendor_name,
                            user_id,
                        )
                        logger.info(
                            f"Auto-created vendor: {vendor_id}, name={vendor_name}"
                        )
                else:
                    return {
                        "success": False,
                        "message": "Either vendor_id or vendor_name is required",
                        "data": None,
                    }

                # 3. Calculate totals
                items = request.get("items", [])
                if not items:
                    return {
                        "success": False,
                        "message": "Minimal satu item harus diisi",
                        "data": None,
                    }

                # If any item has per-item tax, use 0 for header tax (avoid double-counting)
                has_per_item_tax = any(
                    item.get("tax_rate") and float(item.get("tax_rate", 0)) > 0
                    for item in items
                )
                header_tax_rate = 0 if has_per_item_tax else request.get("tax_rate", 0)
                calc = BillCalculator.calculate(
                    items=items,
                    invoice_discount_percent=Decimal(
                        str(request.get("invoice_discount_percent", 0))
                    ),
                    invoice_discount_amount=request.get("invoice_discount_amount", 0),
                    cash_discount_percent=Decimal(
                        str(request.get("cash_discount_percent", 0))
                    ),
                    cash_discount_amount=request.get("cash_discount_amount", 0),
                    tax_rate=header_tax_rate,
                    dpp_manual=request.get("dpp_manual"),
                )

                # 4. Determine status and dates
                status = request.get("status", "draft")
                issue_date = request.get("issue_date") or date.today()

                # due_date is required
                due_date = request.get("due_date")
                if not due_date:
                    return {
                        "success": False,
                        "message": "Tanggal jatuh tempo (due_date) wajib diisi",
                        "data": None,
                    }

                # 5. Insert bill
                bill_id = await conn.fetchval(
                    """
                    INSERT INTO bills (
                        tenant_id, invoice_number, ref_no, vendor_id, vendor_name,
                        amount, issue_date, due_date, notes, created_by,
                        status_v2, tax_rate, tax_inclusive,
                        invoice_discount_percent, invoice_discount_amount,
                        cash_discount_percent, cash_discount_amount,
                        dpp_manual, subtotal, item_discount_total,
                        invoice_discount_total, cash_discount_total,
                        dpp, tax_amount, grand_total, tax_code_id
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19,
                        $20, $21, $22, $23, $24, $25, $26
                    )
                    RETURNING id
                """,
                    tenant_id,
                    invoice_number,
                    request.get("ref_no"),
                    vendor_id,
                    vendor_name,
                    calc["grand_total"],  # Legacy amount field
                    issue_date,
                    due_date,
                    request.get("notes"),
                    user_id,
                    status,
                    request.get("tax_rate", 11),
                    request.get("tax_inclusive", False),
                    float(request.get("invoice_discount_percent", 0)),
                    request.get("invoice_discount_amount", 0),
                    float(request.get("cash_discount_percent", 0)),
                    request.get("cash_discount_amount", 0),
                    request.get("dpp_manual"),
                    calc["subtotal"],
                    calc["item_discount_total"],
                    calc["invoice_discount_total"],
                    calc["cash_discount_total"],
                    calc["dpp"],
                    calc["tax_amount"],
                    calc["grand_total"],
                    UUID(str(request["tax_code_id"]))
                    if request.get("tax_code_id")
                    else None,  # UUID from tax_codes table
                )

                # 6. Insert items
                for idx, item in enumerate(items, start=1):
                    # Validate required item fields
                    if "qty" not in item or item["qty"] is None:
                        return {
                            "success": False,
                            "message": f"Item {idx}: qty wajib diisi",
                            "data": None,
                        }
                    if "price" not in item or item["price"] is None:
                        return {
                            "success": False,
                            "message": f"Item {idx}: price wajib diisi",
                            "data": None,
                        }

                    try:
                        qty = Decimal(str(item["qty"]))  # decimal qty support (Law 25)
                        price = int(item["price"])
                    except (ValueError, TypeError):
                        return {
                            "success": False,
                            "message": f"Item {idx}: qty dan price harus berupa angka",
                            "data": None,
                        }

                    discount_pct = Decimal(str(item.get("discount_percent", 0)))
                    item_calc = BillCalculator.calculate_item_total(
                        qty, price, discount_pct
                    )

                    # Per-item tax calculation
                    item_tax_code_id = item.get("tax_code_id")
                    item_tax_rate = float(item.get("tax_rate") or 0)
                    item_dpp = float(
                        item_calc["subtotal"]
                    )  # DPP = subtotal after discount
                    item_tax_amount = (
                        round(item_dpp * item_tax_rate / 100)
                        if item_tax_rate > 0
                        else 0
                    )

                    # Convert exp_date string to date if provided
                    # Accepts both YYYY-MM and YYYY-MM-DD formats
                    exp_date = None
                    if item.get("exp_date"):
                        try:
                            exp_val = item["exp_date"]
                            if len(exp_val) == 7:  # YYYY-MM
                                exp_date = date.fromisoformat(f"{exp_val}-01")
                            else:  # YYYY-MM-DD
                                exp_date = date.fromisoformat(exp_val)
                        except ValueError:
                            return {
                                "success": False,
                                "message": f"Item {idx}: format exp_date harus YYYY-MM atau YYYY-MM-DD (contoh: 2025-12 atau 2025-12-31)",
                                "data": None,
                            }

                    await conn.execute(
                        """
                        INSERT INTO bill_items (
                            bill_id, product_id, product_code, product_name,
                            description, quantity, unit, unit_price,
                            discount_percent, discount_amount, total, subtotal,
                            batch_no, exp_date, bonus_qty, line_number,
                            tax_code_id, tax_rate, tax_amount, dpp
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
                    """,
                        bill_id,
                        item.get("product_id"),
                        item.get("product_code"),
                        item.get("product_name"),
                        item.get("product_name"),  # Use product_name as description
                        qty,
                        item.get("unit"),
                        price,
                        float(discount_pct),
                        item_calc["discount_amount"],
                        item_calc["total"],
                        item_calc["subtotal"],
                        item.get("batch_no"),
                        exp_date,
                        item.get("bonus_qty", 0),
                        idx,
                        UUID(str(item_tax_code_id)) if item_tax_code_id else None,
                        item_tax_rate,
                        item_tax_amount,
                        item_dpp,
                    )

                # 6b. Recalculate header tax from per-item sums (if any item has tax)
                item_tax_total = await conn.fetchval(
                    "SELECT COALESCE(SUM(tax_amount), 0) FROM bill_items WHERE bill_id = $1",
                    bill_id,
                )
                if float(item_tax_total) > 0:
                    new_grand = float(calc["grand_total"]) + float(item_tax_total)
                    await conn.execute(
                        "UPDATE bills SET tax_amount = $1, grand_total = $2, amount = $2 WHERE id = $3",
                        float(item_tax_total),
                        new_grand,
                        bill_id,
                    )
                    calc["tax_amount"] = float(item_tax_total)
                    calc["grand_total"] = new_grand

                # 7. If posted status, create AP and journal entry
                ap_id = None
                journal_id = None

                if status == "posted":
                    # ====================================================================
                    # INLINE BILL POSTING (Law 1, 6, 13, 16, 20, 23, 27 + ARAP Rule 1)
                    # - Proper tax split (Dr Inventory net + Dr PPN Masukan + Cr AP)
                    # - DTL write for tax report traceability (Law 16 + /milkyhoop-tax)
                    # - Subcontract routing: bills tied to production_subcontracts → WIP
                    # - Single transaction (Law 23)
                    # ====================================================================
                    # Law 13: advisory lock
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))",
                        f"BILL_POST:{str(bill_id)}",
                    )

                    grand_total_dec = Decimal(str(calc["grand_total"]))
                    tax_amount_dec = Decimal(str(calc["tax_amount"]))
                    subtotal_dec = grand_total_dec - tax_amount_dec

                    # Check if this bill is linked to a production subcontract
                    is_subcontract_bill = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM production_subcontracts WHERE bill_id = $1)",
                        bill_id,
                    )
                    # Law 27 + Fase D2.3 + Fase D3.3: role-based AP + VAT + debit.
                    # Subcontract -> WIP_SUBCONTRACT (was 1-10650);
                    # else -> INVENTORY_MERCHANDISE (was 1-10600).
                    ap_acct_id = await resolve_account_id_by_role(
                        conn, tenant_id, AccountRole.AP_TRADE
                    )
                    debit_role = (
                        AccountRole.WIP_SUBCONTRACT
                        if is_subcontract_bill
                        else AccountRole.INVENTORY_MERCHANDISE
                    )
                    debit_acct_id = await resolve_account_id_by_role(
                        conn, tenant_id, debit_role
                    )
                    debit_account_code = debit_role  # for downstream error messages
                    vat_input_acct_id = None
                    if tax_amount_dec > 0:
                        vat_input_acct_id = await resolve_account_id_by_role_if_pkp(
                            conn, tenant_id, AccountRole.VAT_INPUT
                        )
                        if vat_input_acct_id is None:
                            # Tenant non-PKP submitting bill with PPN > 0 -> 422
                            # (Law 4 consistency, no silent skip).
                            raise ValueError(
                                "Tenant non-PKP tidak dapat memposting tagihan "
                                "dengan PPN > 0. Atur tax_amount = 0 atau "
                                "aktifkan status PKP terlebih dahulu."
                            )

                    if not ap_acct_id:
                        raise ValueError("Akun AP_TRADE tidak ter-resolve")
                    if not debit_acct_id:
                        raise ValueError(
                            f"Akun debit ({debit_account_code} / "
                            f"INVENTORY_MERCHANDISE) tidak ter-resolve"
                        )

                    # Generate journal number
                    journal_number_v2 = (
                        await conn.fetchval(
                            "SELECT get_next_journal_number($1, 'PJ')", tenant_id
                        )
                        or f"PJ-{issue_date.strftime('%y%m')}-AUTO"
                    )

                    # Law 20: Create journal DRAFT → POSTED (triggers hash chain)
                    journal_id = uuid_module.uuid4()
                    trace_id_v2 = uuid_module.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO journal_entries (
                            id, tenant_id, journal_number, journal_date,
                            description, source_type, source_id, trace_id,
                            status, total_debit, total_credit, created_by
                        ) VALUES ($1, $2, $3, $4, $5, 'BILL', $6, $7, 'DRAFT', $8, $8, $9)
                        """,
                        journal_id,
                        tenant_id,
                        journal_number_v2,
                        issue_date,
                        f"Bill dari {vendor_name} - {invoice_number}",
                        bill_id,
                        str(trace_id_v2),
                        grand_total_dec,
                        user_id,
                    )

                    # Line 1: Dr Inventory / WIP (net subtotal)
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, 1, $3, $4, 0, $5)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        debit_acct_id,
                        subtotal_dec,
                        f"{'WIP subkontrak' if is_subcontract_bill else 'Pembelian'} - {vendor_name}",
                    )

                    # Line 2: Dr PPN Masukan (if tax > 0)
                    ppn_journal_line_id = None
                    line_num_v2 = 2
                    if tax_amount_dec > 0 and vat_input_acct_id:
                        ppn_journal_line_id = uuid_module.uuid4()
                        await conn.execute(
                            """
                            INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                            VALUES ($1, $2, $3, $4, $5, 0, 'PPN Masukan')
                            """,
                            ppn_journal_line_id,
                            journal_id,
                            line_num_v2,
                            vat_input_acct_id,
                            tax_amount_dec,
                        )
                        line_num_v2 += 1

                    # Line 3: Cr Utang Usaha (grand_total)
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, 0, $5, $6)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        line_num_v2,
                        ap_acct_id,
                        grand_total_dec,
                        f"Hutang ke {vendor_name}",
                    )

                    # Law 20: POST (hash chain trigger)
                    await conn.execute(
                        "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                        journal_id,
                    )

                    # Create AP record (ARAP Rule 1 — same transaction)
                    ap_id = uuid_module.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO accounts_payable (
                            id, tenant_id, supplier_id, supplier_name,
                            bill_number, bill_date, due_date,
                            amount, amount_paid, status,
                            description, source_type, source_id, currency
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0, 'OPEN', $9, 'BILL', $10, 'IDR')
                        """,
                        ap_id,
                        tenant_id,
                        vendor_id,
                        vendor_name,
                        invoice_number,
                        issue_date,
                        due_date,
                        grand_total_dec,
                        f"AP for {invoice_number}",
                        bill_id,
                    )

                    # Update bill status
                    await conn.execute(
                        """
                        UPDATE bills
                        SET ap_id = $1, journal_id = $2, posted_at = NOW(), posted_by = $3,
                            status = 'posted', status_v2 = 'posted',
                            operational_status = 'RECEIVED', accounting_status = 'POSTED'
                        WHERE id = $4
                        """,
                        str(ap_id),
                        str(journal_id),
                        user_id,
                        bill_id,
                    )

                    # DTL writes for tax report (Law 16, /milkyhoop-tax Rule 3+10)
                    if tax_amount_dec > 0:
                        taxable_items_dtl = await conn.fetch(
                            "SELECT id, tax_code_id, tax_rate, tax_amount, dpp FROM bill_items WHERE bill_id = $1 AND COALESCE(tax_amount, 0) > 0",
                            bill_id,
                        )
                        if taxable_items_dtl:
                            for ti in taxable_items_dtl:
                                _tcid = ti["tax_code_id"]
                                # Fallback: resolve by rate if tax_code_id is NULL
                                if (
                                    not _tcid
                                    and ti["tax_rate"]
                                    and float(ti["tax_rate"]) > 0
                                ):
                                    _tcid = await conn.fetchval(
                                        "SELECT id FROM tax_codes WHERE tenant_id=$1 AND tax_type='ppn' AND rate=$2 AND is_active=true ORDER BY (name ILIKE '%%Masukan%%') DESC LIMIT 1",
                                        tenant_id,
                                        ti["tax_rate"],
                                    )
                                if not _tcid:
                                    continue
                                tc_coa = await conn.fetchval(
                                    "SELECT coa_id FROM tax_codes WHERE id = $1",
                                    _tcid,
                                )
                                dpp_val = (
                                    float(ti["dpp"] or 0)
                                    or float(ti["tax_amount"])
                                    / float(ti["tax_rate"] or 1)
                                    * 100
                                )
                                await conn.execute(
                                    """
                                    INSERT INTO document_tax_lines
                                    (id, tenant_id, document_type, document_id, line_item_id, tax_code_id,
                                     direction, base_amount, tax_amount, coa_id, journal_line_id)
                                    VALUES ($1, $2, 'BILL', $3, $4, $5, 'input', $6, $7, $8, $9)
                                    """,
                                    uuid_module.uuid4(),
                                    tenant_id,
                                    bill_id,
                                    ti["id"],
                                    _tcid,
                                    dpp_val,
                                    float(ti["tax_amount"]),
                                    tc_coa or vat_input_acct_id,
                                    ppn_journal_line_id,
                                )
                    # (inventory ledger block continues below — unchanged)
                    if (
                        self.accounting or True
                    ):  # harmless guard to preserve indentation
                        # UPDATE INVENTORY for inventory-tracked items
                        # Get bill items with product details
                        bill_items_for_inv = await conn.fetch(
                            """
                            SELECT bi.product_id, bi.quantity, bi.unit_price, bi.description,
                                   bi.batch_no, bi.exp_date,
                                   p.nama_produk, p.item_code, p.track_inventory, p.item_type,
                                   p.track_batches
                            FROM bill_items bi
                            LEFT JOIN products p ON p.id = bi.product_id
                            WHERE bi.bill_id = $1 AND bi.product_id IS NOT NULL
                            """,
                            bill_id,
                        )

                        # Get default warehouse for tenant
                        default_warehouse = await conn.fetchrow(
                            "SELECT id FROM warehouses WHERE tenant_id = $1 AND is_default = true LIMIT 1",
                            tenant_id,
                        )
                        warehouse_id = (
                            default_warehouse["id"] if default_warehouse else None
                        )

                        for inv_item in bill_items_for_inv:
                            # Only process inventory-tracked goods
                            if inv_item["item_type"] != "goods" or not inv_item.get(
                                "track_inventory", True
                            ):
                                continue

                            product_id = inv_item["product_id"]
                            quantity = Decimal(str(inv_item["quantity"]))
                            unit_cost = Decimal(str(inv_item["unit_price"]))
                            total_cost = quantity * unit_cost

                            # Get current balance for this product
                            balance_row = await conn.fetchrow(
                                """
                                SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as balance
                                FROM inventory_ledger
                                WHERE tenant_id = $1 AND product_id = $2
                                """,
                                tenant_id,
                                product_id,
                            )
                            current_balance = (
                                Decimal(str(balance_row["balance"]))
                                if balance_row
                                else Decimal("0")
                            )
                            new_balance = current_balance + quantity

                            # Calculate weighted average cost
                            avg_cost_row = await conn.fetchrow(
                                """
                                SELECT
                                    COALESCE(SUM(quantity_in * unit_cost), 0) as total_value,
                                    COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as total_qty
                                FROM inventory_ledger
                                WHERE tenant_id = $1 AND product_id = $2
                                """,
                                tenant_id,
                                product_id,
                            )

                            if avg_cost_row and avg_cost_row["total_qty"] > 0:
                                old_value = Decimal(str(avg_cost_row["total_value"]))
                                old_qty = Decimal(str(avg_cost_row["total_qty"]))
                                new_avg_cost = (old_value + total_cost) / (
                                    old_qty + quantity
                                )
                            else:
                                new_avg_cost = unit_cost

                            # --- Batch resolution (Tahap 1.2) ---
                            batch_id = None
                            if inv_item.get("track_batches") and inv_item.get(
                                "batch_no"
                            ):
                                exp_date_val = inv_item.get(
                                    "exp_date"
                                )  # Already a date from bill_items

                                batch_row = await conn.fetchrow(
                                    """
                                    INSERT INTO item_batches (
                                        tenant_id, item_id, batch_number, expiry_date,
                                        received_date, initial_quantity, current_quantity,
                                        unit_cost, total_value, status, bill_id, created_by
                                    ) VALUES ($1, $2, $3, $4, $5, $6, $6, $7, $8, 'active', $9, $10)
                                    ON CONFLICT (tenant_id, item_id, batch_number)
                                    DO UPDATE SET
                                        current_quantity = item_batches.current_quantity + EXCLUDED.initial_quantity,
                                        total_value = item_batches.total_value + EXCLUDED.total_value,
                                        updated_at = NOW()
                                    RETURNING id
                                """,
                                    tenant_id,
                                    product_id,
                                    inv_item["batch_no"],
                                    exp_date_val,
                                    issue_date,
                                    quantity,
                                    unit_cost,
                                    total_cost,
                                    bill_id,
                                    user_id,
                                )

                                batch_id = batch_row["id"]

                                if warehouse_id:
                                    await conn.execute(
                                        """
                                        INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity)
                                        VALUES ($1, $2, $3, $4)
                                        ON CONFLICT (batch_id, warehouse_id)
                                        DO UPDATE SET
                                            quantity = batch_warehouse_stock.quantity + EXCLUDED.quantity,
                                            last_movement_date = NOW(), updated_at = NOW()
                                    """,
                                        tenant_id,
                                        batch_id,
                                        warehouse_id,
                                        quantity,
                                    )

                                await conn.execute(
                                    """
                                    UPDATE bill_items SET batch_id = $1
                                    WHERE bill_id = $2 AND product_id = $3 AND batch_no = $4
                                """,
                                    batch_id,
                                    bill_id,
                                    product_id,
                                    inv_item["batch_no"],
                                )

                                logger.info(
                                    f"Batch created/updated: {inv_item['batch_no']} for product {product_id}, batch_id={batch_id}"
                                )

                            # Insert inventory_ledger entry
                            await conn.execute(
                                """
                                INSERT INTO inventory_ledger (
                                    tenant_id, product_id, product_code, product_name,
                                    movement_type, movement_date, source_type, source_id, source_number,
                                    quantity_in, quantity_out, quantity_balance,
                                    unit_cost, total_cost, average_cost,
                                    warehouse_id, journal_id, created_by, notes, batch_id
                                ) VALUES (
                                    $1, $2, $3, $4,
                                    'PURCHASE', $5, 'BILL', $6, $7,
                                    $8, 0, $9,
                                    $10, $11, $12,
                                    $13, $14, $15, $16, $17
                                )
                                """,
                                tenant_id,
                                product_id,
                                inv_item.get("item_code"),
                                inv_item.get("nama_produk"),
                                issue_date,
                                bill_id,
                                invoice_number,
                                quantity,
                                new_balance,
                                unit_cost,
                                total_cost,
                                new_avg_cost,
                                warehouse_id,
                                journal_id,
                                user_id,
                                f"Purchase from {vendor_name}",
                                batch_id,
                            )

                            logger.info(
                                f"Inventory updated for product {product_id}: +{quantity} @ {unit_cost}"
                            )

                    else:
                        # Log warning but allow draft-like behavior
                        logger.warning(
                            f"Accounting kernel not available. Bill {bill_id} posted without AP."
                        )

                logger.info(
                    f"Bill V2 created: {bill_id}, status={status}, grand_total={calc['grand_total']}"
                )

                # Build response with all fields needed for frontend list injection
                now_iso = datetime.now().isoformat()
                grand_total = calc["grand_total"]

                return {
                    "success": True,
                    "message": f"Bill created as {status}",
                    "data": {
                        "id": str(bill_id),
                        "invoice_number": invoice_number,
                        "vendor_id": str(vendor_id) if vendor_id else None,
                        "vendor_name": vendor_name,
                        "vendor": {"name": vendor_name} if vendor_name else None,
                        "status": status,
                        "amount": grand_total,
                        "amount_paid": 0,
                        "amount_due": grand_total,
                        "issue_date": issue_date.isoformat() if issue_date else None,
                        "due_date": due_date.isoformat() if due_date else None,
                        "calculation": calc,
                        "created_at": now_iso,
                        "operational_status": "RECEIVED"
                        if status == "posted"
                        else "DRAFT",
                        "accounting_status": "POSTED"
                        if status == "posted"
                        else "UNPOSTED",
                    },
                }

    async def post_bill(
        self, tenant_id: str, bill_id: UUID, user_id: UUID
    ) -> Dict[str, Any]:
        """
        Transition bill from draft to posted.
        Single-transaction pattern (Iron Law 23, ARAP Rule 1).

        Creates in 1 transaction:
        - Journal entry (DR Inventory/Expense + PPN, CR AP) via DRAFT→POSTED
        - AP record in accounts_payable
        - Bill status update
        - Inventory ledger entries (for tracked goods)
        """
        async with self.pool.acquire() as conn:
            await _ensure_bills_service_role_preconditions(self.pool)
            async with conn.transaction():
                # 0. RLS context
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )

                # 1. Advisory lock (Law 13)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    f"BILL_POST:{str(bill_id)}",
                )

                # 2. Get bill with row lock
                bill = await conn.fetchrow(
                    """
                    SELECT id, status_v2, invoice_number, vendor_name, vendor_id,
                           issue_date, due_date, grand_total, tax_amount,
                           tax_code_id, tax_rate, dpp
                    FROM bills
                    WHERE id = $1 AND tenant_id = $2
                    FOR UPDATE
                    """,
                    bill_id,
                    tenant_id,
                )

                if not bill:
                    return {"success": False, "message": "Bill not found", "data": None}

                if bill["status_v2"] != "draft":
                    return {
                        "success": False,
                        "message": f"Cannot post bill with status '{bill['status_v2']}'. Only draft bills can be posted.",
                        "data": None,
                    }

                grand_total = Decimal(str(bill["grand_total"]))
                bill_tax = Decimal(str(bill["tax_amount"] or 0))
                subtotal = grand_total - bill_tax

                # 3. Resolve accounts (Law 27 + Fase D2.3 role-based)
                # Check if this bill is linked to a production subcontract → route to WIP
                is_sc_bill = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM production_subcontracts WHERE bill_id = $1)",
                    bill_id,
                )
                # Fase D3.3: subcontract -> WIP_SUBCONTRACT (was 1-10650);
                # else -> INVENTORY_MERCHANDISE (was 1-10600).
                debit_role = (
                    AccountRole.WIP_SUBCONTRACT
                    if is_sc_bill
                    else AccountRole.INVENTORY_MERCHANDISE
                )
                debit_code = debit_role  # for downstream error messages
                ap_account_id = await resolve_account_id_by_role(
                    conn, tenant_id, AccountRole.AP_TRADE
                )
                inventory_account_id = await resolve_account_id_by_role(
                    conn, tenant_id, debit_role
                )
                vat_input_account_id = None
                if bill_tax > 0:
                    vat_input_account_id = await resolve_account_id_by_role_if_pkp(
                        conn, tenant_id, AccountRole.VAT_INPUT
                    )
                    if vat_input_account_id is None:
                        # Non-PKP tenant with tax_amount > 0 -> reject loudly.
                        return {
                            "success": False,
                            "message": (
                                "Tenant non-PKP tidak dapat memposting tagihan "
                                "dengan PPN > 0. Atur tax_amount = 0 atau "
                                "aktifkan status PKP terlebih dahulu."
                            ),
                            "data": None,
                        }

                if not ap_account_id:
                    return {
                        "success": False,
                        "message": "Akun AP_TRADE tidak ter-resolve",
                        "data": None,
                    }
                if not inventory_account_id:
                    return {
                        "success": False,
                        "message": (
                            f"Akun debit ({debit_code} / "
                            "INVENTORY_MERCHANDISE) tidak ter-resolve"
                        ),
                        "data": None,
                    }

                # 4. Generate journal number
                journal_number = (
                    await conn.fetchval(
                        "SELECT get_next_journal_number($1, 'PJ')", tenant_id
                    )
                    or f"PJ-{bill['issue_date'].strftime('%y%m')}-AUTO"
                )

                # 5. Create journal DRAFT (Law 20)
                journal_id = uuid_module.uuid4()
                trace_id = uuid_module.uuid4()

                await conn.execute(
                    """
                    INSERT INTO journal_entries (
                        id, tenant_id, journal_number, journal_date,
                        description, source_type, source_id, trace_id,
                        status, total_debit, total_credit, created_by
                    ) VALUES ($1, $2, $3, $4, $5, 'BILL', $6, $7, 'DRAFT', $8, $8, $9)
                    """,
                    journal_id,
                    tenant_id,
                    journal_number,
                    bill["issue_date"],
                    f"Bill dari {bill['vendor_name']} - {bill['invoice_number']}",
                    bill_id,
                    str(trace_id),
                    grand_total,
                    user_id,
                )

                # 6. Journal lines
                line_number = 1

                # Dr. Inventory/Expense (subtotal)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, $5, 0, $6)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    inventory_account_id,
                    subtotal,
                    f"Pembelian dari {bill['vendor_name']}",
                )
                line_number += 1

                # Dr. PPN Masukan (if tax > 0)
                if bill_tax > 0 and vat_input_account_id:
                    await conn.execute(
                        """
                        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                        VALUES ($1, $2, $3, $4, $5, 0, $6)
                        """,
                        uuid_module.uuid4(),
                        journal_id,
                        line_number,
                        vat_input_account_id,
                        bill_tax,
                        "PPN Masukan",
                    )
                    line_number += 1

                # Cr. Hutang Usaha (grand_total)
                await conn.execute(
                    """
                    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
                    VALUES ($1, $2, $3, $4, 0, $5, $6)
                    """,
                    uuid_module.uuid4(),
                    journal_id,
                    line_number,
                    ap_account_id,
                    grand_total,
                    f"Hutang ke {bill['vendor_name']}",
                )

                # 7. DRAFT → POSTED (triggers hash chain, Law 20)
                await conn.execute(
                    "UPDATE journal_entries SET status = 'POSTED' WHERE id = $1",
                    journal_id,
                )

                # 8. Create AP record (same transaction)
                ap_id = uuid_module.uuid4()
                await conn.execute(
                    """
                    INSERT INTO accounts_payable (
                        id, tenant_id, supplier_id, supplier_name,
                        bill_number, bill_date, due_date,
                        amount, amount_paid, status,
                        description, source_type, source_id, currency
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0, 'OPEN', $9, 'BILL', $10, 'IDR')
                    """,
                    ap_id,
                    tenant_id,
                    bill["vendor_id"],
                    bill["vendor_name"],
                    bill["invoice_number"],
                    bill["issue_date"],
                    bill["due_date"],
                    grand_total,
                    f"AP for {bill['invoice_number']}",
                    bill_id,
                )

                # 9. Update bill status
                await conn.execute(
                    """
                    UPDATE bills
                    SET status_v2 = 'posted',
                        status = 'posted',
                        operational_status = 'RECEIVED',
                        accounting_status = 'POSTED',
                        ap_id = $1,
                        journal_id = $2,
                        posted_at = NOW(),
                        posted_by = $3,
                        updated_at = NOW()
                    WHERE id = $4
                    """,
                    str(ap_id),
                    str(journal_id),
                    user_id,
                    bill_id,
                )

                # 9.5 Populate document_tax_lines (Fase 2.2)
                if bill_tax > 0:
                    ppn_journal_line_id = await conn.fetchval(
                        "SELECT id FROM journal_lines WHERE journal_id = $1 AND account_id = $2 LIMIT 1",
                        journal_id,
                        vat_input_account_id,
                    )

                    # Per-item DTL: write one row per taxable item
                    taxable_items = await conn.fetch(
                        "SELECT id, tax_code_id, tax_rate, tax_amount, dpp FROM bill_items WHERE bill_id = $1 AND COALESCE(tax_amount, 0) > 0",
                        bill_id,
                    )
                    if taxable_items:
                        for ti in taxable_items:
                            if ti["tax_code_id"]:
                                tc_coa = await conn.fetchval(
                                    "SELECT coa_id FROM tax_codes WHERE id = $1",
                                    ti["tax_code_id"],
                                )
                                await conn.execute(
                                    """
                                    INSERT INTO document_tax_lines
                                    (id, tenant_id, document_type, document_id, line_item_id, tax_code_id,
                                     direction, base_amount, tax_amount, coa_id, journal_line_id)
                                    VALUES ($1, $2, 'BILL', $3, $4, $5, 'input', $6, $7, $8, $9)
                                    """,
                                    uuid_module.uuid4(),
                                    tenant_id,
                                    bill_id,
                                    ti["id"],
                                    ti["tax_code_id"],
                                    float(ti["dpp"] or ti["tax_amount"]),
                                    float(ti["tax_amount"]),
                                    tc_coa or vat_input_account_id,
                                    ppn_journal_line_id,
                                )
                    elif bill.get("tax_code_id"):
                        # Header-level tax fallback (legacy)
                        dpp_amount = Decimal(str(bill["dpp"] or 0))
                        if dpp_amount == 0:
                            dpp_amount = subtotal
                        await conn.execute(
                            """
                            INSERT INTO document_tax_lines
                            (id, tenant_id, document_type, document_id, tax_code_id,
                             direction, base_amount, tax_amount, coa_id, journal_line_id)
                            VALUES ($1, $2, 'BILL', $3, $4, 'input', $5, $6, $7, $8)
                            """,
                            uuid_module.uuid4(),
                            tenant_id,
                            bill_id,
                            bill["tax_code_id"],
                            dpp_amount,
                            bill_tax,
                            vat_input_account_id,
                            ppn_journal_line_id,
                        )
                    logger.info(f"document_tax_lines created for bill {bill_id}")

                # 10. Inventory ledger updates (for tracked goods)
                bill_items = await conn.fetch(
                    """
                    SELECT bi.product_id, bi.quantity, bi.unit_price, bi.description,
                           bi.batch_no, bi.exp_date,
                           p.nama_produk, p.item_code, p.track_inventory, p.item_type,
                           p.track_batches
                    FROM bill_items bi
                    LEFT JOIN products p ON p.id = bi.product_id
                    WHERE bi.bill_id = $1 AND bi.product_id IS NOT NULL
                    """,
                    bill_id,
                )

                default_warehouse = await conn.fetchrow(
                    "SELECT id FROM warehouses WHERE tenant_id = $1 AND is_default = true LIMIT 1",
                    tenant_id,
                )
                warehouse_id = default_warehouse["id"] if default_warehouse else None

                for item in bill_items:
                    if item["item_type"] != "goods" or not item.get(
                        "track_inventory", True
                    ):
                        continue

                    product_id = item["product_id"]
                    transaction_quantity_3 = Decimal(str(item["quantity"]))
                    transaction_unit_3 = item.get("unit")
                    base_qty_3, conversion_factor_3 = await convert_to_base_unit(
                        conn,
                        tenant_id,
                        product_id,
                        transaction_quantity_3,
                        transaction_unit_3,
                    )
                    quantity = base_qty_3
                    unit_cost_raw_3 = Decimal(str(item["unit_price"]))
                    total_cost = transaction_quantity_3 * unit_cost_raw_3
                    unit_cost = total_cost / quantity if quantity else unit_cost_raw_3

                    balance_row = await conn.fetchrow(
                        """
                        SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as balance
                        FROM inventory_ledger
                        WHERE tenant_id = $1 AND product_id = $2
                        """,
                        tenant_id,
                        product_id,
                    )
                    current_balance = (
                        Decimal(str(balance_row["balance"]))
                        if balance_row
                        else Decimal("0")
                    )
                    new_balance = current_balance + quantity

                    avg_cost_row = await conn.fetchrow(
                        """
                        SELECT
                            COALESCE(SUM(quantity_in * unit_cost), 0) as total_value,
                            COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as total_qty
                        FROM inventory_ledger
                        WHERE tenant_id = $1 AND product_id = $2
                        """,
                        tenant_id,
                        product_id,
                    )

                    if avg_cost_row and avg_cost_row["total_qty"] > 0:
                        old_value = Decimal(str(avg_cost_row["total_value"]))
                        old_qty = Decimal(str(avg_cost_row["total_qty"]))
                        new_avg_cost = (old_value + total_cost) / (old_qty + quantity)
                    else:
                        new_avg_cost = unit_cost

                    # --- Batch resolution (Tahap 1.2) ---
                    batch_id = None
                    if item.get("track_batches") and item.get("batch_no"):
                        exp_date_val = None
                        if item.get("exp_date"):
                            exp_date_val = item[
                                "exp_date"
                            ]  # Already a date from bill_items

                        # UPSERT item_batches
                        batch_row = await conn.fetchrow(
                            """
                            INSERT INTO item_batches (
                                tenant_id, item_id, batch_number, expiry_date,
                                received_date, initial_quantity, current_quantity,
                                unit_cost, total_value, status, bill_id, created_by
                            ) VALUES ($1, $2, $3, $4, $5, $6, $6, $7, $8, 'active', $9, $10)
                            ON CONFLICT (tenant_id, item_id, batch_number)
                            DO UPDATE SET
                                current_quantity = item_batches.current_quantity + EXCLUDED.initial_quantity,
                                total_value = item_batches.total_value + EXCLUDED.total_value,
                                updated_at = NOW()
                            RETURNING id
                        """,
                            tenant_id,
                            product_id,
                            item["batch_no"],
                            exp_date_val,
                            bill["issue_date"],
                            quantity,
                            unit_cost,
                            total_cost,
                            bill_id,
                            user_id,
                        )

                        batch_id = batch_row["id"]

                        # UPSERT batch_warehouse_stock
                        if warehouse_id:
                            await conn.execute(
                                """
                                INSERT INTO batch_warehouse_stock (tenant_id, batch_id, warehouse_id, quantity)
                                VALUES ($1, $2, $3, $4)
                                ON CONFLICT (batch_id, warehouse_id)
                                DO UPDATE SET
                                    quantity = batch_warehouse_stock.quantity + EXCLUDED.quantity,
                                    last_movement_date = NOW(), updated_at = NOW()
                            """,
                                tenant_id,
                                batch_id,
                                warehouse_id,
                                quantity,
                            )

                        # Link bill_items.batch_id
                        await conn.execute(
                            """
                            UPDATE bill_items SET batch_id = $1
                            WHERE bill_id = $2 AND product_id = $3 AND batch_no = $4
                        """,
                            batch_id,
                            bill_id,
                            product_id,
                            item["batch_no"],
                        )

                        logger.info(
                            f"Batch created/updated: {item['batch_no']} for product {product_id}, batch_id={batch_id}"
                        )

                    await conn.execute(
                        """
                        INSERT INTO inventory_ledger (
                            tenant_id, product_id, product_code, product_name,
                            movement_type, movement_date, source_type, source_id, source_number,
                            quantity_in, quantity_out, quantity_balance,
                            unit_cost, total_cost, average_cost,
                            warehouse_id, journal_id, created_by, notes, batch_id,
                            transaction_unit, transaction_quantity, conversion_factor
                        ) VALUES (
                            $1, $2, $3, $4,
                            'PURCHASE', $5, 'BILL', $6, $7,
                            $8, 0, $9,
                            $10, $11, $12,
                            $13, $14, $15, $16, $17,
                            $18, $19, $20
                        )
                        """,
                        tenant_id,
                        product_id,
                        item.get("item_code"),
                        item.get("nama_produk"),
                        bill["issue_date"],
                        bill_id,
                        bill["invoice_number"],
                        quantity,
                        new_balance,
                        unit_cost,
                        total_cost,
                        new_avg_cost,
                        warehouse_id,
                        journal_id,
                        user_id,
                        f"Purchase from {bill['vendor_name']}",
                        batch_id,
                        transaction_unit_3,
                        transaction_quantity_3,
                        conversion_factor_3,
                    )

                    logger.info(
                        f"Inventory updated for product {product_id}: +{quantity} @ {unit_cost}"
                    )

                logger.info(f"Bill posted: {bill_id}")

                return {
                    "success": True,
                    "message": "Bill posted successfully",
                    "data": {
                        "id": str(bill_id),
                        "status": "posted",
                        "posted_at": datetime.now().isoformat(),
                    },
                }

    async def update_bill_v2(
        self, tenant_id: str, bill_id: UUID, request: Dict[str, Any], user_id: UUID
    ) -> Dict[str, Any]:
        """
        Update a draft bill (V2). Only draft bills can be edited.

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Check bill exists and is draft
            bill = await conn.fetchrow(
                """
                SELECT id, status_v2, vendor_id, tax_rate FROM bills
                WHERE id = $1 AND tenant_id = $2
            """,
                bill_id,
                tenant_id,
            )

            if not bill:
                return {"success": False, "message": "Bill not found", "data": None}

            if bill["status_v2"] != "draft":
                return {
                    "success": False,
                    "message": f"Cannot edit bill with status '{bill['status_v2']}'. Only draft bills can be edited.",
                    "data": None,
                }

            async with conn.transaction():
                # Resolve vendor if changed
                vendor_id = request.get("vendor_id", bill["vendor_id"])
                vendor_name = request.get("vendor_name")

                if vendor_id and vendor_id != bill["vendor_id"]:
                    vendor_row = await conn.fetchrow(
                        "SELECT name FROM vendors WHERE id = $1 AND tenant_id = $2",
                        vendor_id,
                        tenant_id,
                    )
                    if vendor_row:
                        vendor_name = vendor_row["name"]

                # Recalculate if items provided
                items = request.get("items")
                calc = None

                if items:
                    # If any item has per-item tax, use 0 for header tax (avoid double-counting)
                    has_per_item_tax = any(
                        item.get("tax_rate") and float(item.get("tax_rate", 0)) > 0
                        for item in items
                    )
                    header_tax_rate = (
                        0 if has_per_item_tax else request.get("tax_rate", 0)
                    )

                    calc = BillCalculator.calculate(
                        items=items,
                        invoice_discount_percent=Decimal(
                            str(request.get("invoice_discount_percent", 0))
                        ),
                        invoice_discount_amount=request.get(
                            "invoice_discount_amount", 0
                        ),
                        cash_discount_percent=Decimal(
                            str(request.get("cash_discount_percent", 0))
                        ),
                        cash_discount_amount=request.get("cash_discount_amount", 0),
                        tax_rate=header_tax_rate,
                        dpp_manual=request.get("dpp_manual"),
                    )

                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM bill_items WHERE bill_id = $1", bill_id
                    )

                    # Insert new items
                    for idx, item in enumerate(items, start=1):
                        qty = Decimal(str(item["qty"]))  # decimal qty support (Law 25)
                        price = int(item["price"])
                        discount_pct = Decimal(str(item.get("discount_percent", 0)))
                        item_calc = BillCalculator.calculate_item_total(
                            qty, price, discount_pct
                        )

                        # Accepts both YYYY-MM and YYYY-MM-DD formats
                        exp_date = None
                        if item.get("exp_date"):
                            exp_val = item["exp_date"]
                            if len(exp_val) == 7:  # YYYY-MM
                                exp_date = date.fromisoformat(f"{exp_val}-01")
                            else:  # YYYY-MM-DD
                                exp_date = date.fromisoformat(exp_val)

                        # Per-item tax calculation
                        item_tax_code_id = item.get("tax_code_id")
                        item_tax_rate = float(item.get("tax_rate") or 0)
                        item_dpp = float(
                            item_calc["subtotal"]
                        )  # DPP = subtotal after discount
                        item_tax_amount = (
                            round(item_dpp * item_tax_rate / 100)
                            if item_tax_rate > 0
                            else 0
                        )

                        await conn.execute(
                            """
                            INSERT INTO bill_items (
                                bill_id, product_id, product_code, product_name,
                                description, quantity, unit, unit_price,
                                discount_percent, discount_amount, total, subtotal,
                                batch_no, exp_date, bonus_qty, line_number,
                                tax_code_id, tax_rate, tax_amount, dpp
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
                        """,
                            bill_id,
                            item.get("product_id"),
                            item.get("product_code"),
                            item.get("product_name"),
                            item.get("product_name"),
                            qty,
                            item.get("unit"),
                            price,
                            float(discount_pct),
                            item_calc["discount_amount"],
                            item_calc["total"],
                            item_calc["subtotal"],
                            item.get("batch_no"),
                            exp_date,
                            item.get("bonus_qty", 0),
                            idx,
                            UUID(str(item_tax_code_id)) if item_tax_code_id else None,
                            item_tax_rate,
                            item_tax_amount,
                            item_dpp,
                        )

                # Recalculate header tax from per-item sums
                if items:
                    item_tax_total = await conn.fetchval(
                        "SELECT COALESCE(SUM(tax_amount), 0) FROM bill_items WHERE bill_id = $1",
                        bill_id,
                    )
                    if float(item_tax_total) > 0:
                        calc["tax_amount"] = float(item_tax_total)
                        calc["grand_total"] = float(calc["grand_total"]) + float(
                            item_tax_total
                        )

                # Build update query
                updates = ["updated_at = NOW()"]
                params = []
                param_idx = 1

                if vendor_id:
                    updates.append(f"vendor_id = ${param_idx}")
                    params.append(vendor_id)
                    param_idx += 1

                if vendor_name:
                    updates.append(f"vendor_name = ${param_idx}")
                    params.append(vendor_name)
                    param_idx += 1

                if "ref_no" in request:
                    updates.append(f"ref_no = ${param_idx}")
                    params.append(request["ref_no"])
                    param_idx += 1

                if "due_date" in request:
                    updates.append(f"due_date = ${param_idx}")
                    params.append(request["due_date"])
                    param_idx += 1

                if "notes" in request:
                    updates.append(f"notes = ${param_idx}")
                    params.append(request["notes"])
                    param_idx += 1

                if "tax_rate" in request and request["tax_rate"] is not None:
                    updates.append(f"tax_rate = ${param_idx}")
                    params.append(request["tax_rate"])
                    param_idx += 1

                if "tax_inclusive" in request and request["tax_inclusive"] is not None:
                    updates.append(f"tax_inclusive = ${param_idx}")
                    params.append(request["tax_inclusive"])
                    param_idx += 1

                if "tax_code_id" in request:
                    updates.append(f"tax_code_id = ${param_idx}")
                    from uuid import UUID as _UUID

                    params.append(
                        _UUID(str(request["tax_code_id"]))
                        if request["tax_code_id"]
                        else None
                    )
                    param_idx += 1

                if calc:
                    for field in [
                        "subtotal",
                        "item_discount_total",
                        "invoice_discount_total",
                        "cash_discount_total",
                        "dpp",
                        "tax_amount",
                        "grand_total",
                    ]:
                        updates.append(f"{field} = ${param_idx}")
                        params.append(calc[field])
                        param_idx += 1

                    updates.append(f"amount = ${param_idx}")
                    params.append(calc["grand_total"])
                    param_idx += 1

                params.extend([bill_id, tenant_id])

                query = f"""
                    UPDATE bills
                    SET {", ".join(updates)}
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(query, *params)

                logger.info(f"Bill V2 updated: {bill_id}")

                return {
                    "success": True,
                    "message": "Bill updated successfully",
                    "data": {"id": str(bill_id), "calculation": calc},
                }

    def _money_str(self, value: int) -> str:
        """Convert money value to string with .00 suffix."""
        return cents_to_decimal_string(value or 0)

    async def _map_attachments_with_urls(self, attachment_rows) -> list:
        """Map attachment DB rows to response dicts with signed URLs."""
        try:
            from app.services.storage_service import get_storage_service

            storage = get_storage_service()
            result = []
            for att in attachment_rows:
                try:
                    url = (
                        await storage.generate_signed_url(att["file_path"])
                        if att.get("file_path")
                        else att.get("file_path")
                    )
                except Exception:
                    url = att.get("file_path")
                result.append(
                    {
                        "id": str(att["id"]),
                        "filename": att["filename"],
                        "url": url,
                        "size": att.get("file_size"),
                        "mime_type": att.get("mime_type"),
                        "uploaded_at": att["uploaded_at"].isoformat()
                        if att.get("uploaded_at")
                        else None,
                    }
                )
            return result
        except Exception:
            # Fallback if storage service unavailable
            return [
                {
                    "id": str(att["id"]),
                    "filename": att["filename"],
                    "url": att.get("file_path"),
                    "size": att.get("file_size"),
                    "mime_type": att.get("mime_type"),
                    "uploaded_at": att["uploaded_at"].isoformat()
                    if att.get("uploaded_at")
                    else None,
                }
                for att in attachment_rows
            ]

    async def get_bill_v2(
        self, tenant_id: str, bill_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """
        Get bill detail with extended V2 fields.

        Returns:
            Bill detail dict or None if not found
        """
        async with self.pool.acquire() as conn:
            # Get bill with V2 fields -- Law 16: journal-derived amount_paid (direct journal query, not compute_ap_outstanding which excludes paid bills)
            bill_query = """
                SELECT
                    b.*,
                    COALESCE(ap_paid.total_paid, 0) AS journal_paid,
                    GREATEST(b.amount - COALESCE(ap_paid.total_paid, 0), 0) as amount_due,
                    CASE
                        WHEN b.status = 'void' OR b.status_v2 = 'void' THEN 'void'
                        WHEN b.status_v2 = 'draft' THEN 'draft'
                        WHEN COALESCE(ap_paid.total_paid, 0) >= b.amount THEN 'paid'
                        WHEN COALESCE(ap_paid.total_paid, 0) > 0 AND b.due_date < CURRENT_DATE THEN 'overdue'
                        WHEN COALESCE(ap_paid.total_paid, 0) > 0 THEN 'partial'
                        WHEN b.due_date < CURRENT_DATE THEN 'overdue'
                        ELSE 'unpaid'
                    END as calculated_status
                FROM bills b
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(jl.debit), 0) AS total_paid
                    FROM bill_payment_allocations bpa
                    JOIN bill_payments_v2 bpv2 ON bpv2.id = bpa.payment_id
                    JOIN journal_entries je ON je.id = bpv2.journal_id
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE bpa.bill_id = b.id
                      AND je.status = 'POSTED'
                      AND je.reversed_by_id IS NULL
                      AND coa.account_type = 'PAYABLE'
                      AND jl.debit > 0
                ) ap_paid ON true
                WHERE b.id = $1 AND b.tenant_id = $2
            """
            bill = await conn.fetchrow(bill_query, bill_id, tenant_id)

            if not bill:
                return None

            # Get items with V2 fields
            items_query = """
                SELECT
                    bi.*,
                    p.nama_produk as linked_product_name,
                    tc.name as tax_code_name
                FROM bill_items bi
                LEFT JOIN products p ON bi.product_id = p.id
                LEFT JOIN tax_codes tc ON bi.tax_code_id = tc.id
                WHERE bi.bill_id = $1
                ORDER BY bi.line_number
            """
            items = await conn.fetch(items_query, bill_id)

            # Get payments
            payments_query = """
                SELECT bp.id, bpa.amount_applied as amount, bp.payment_date, bp.payment_method,
                       bp.reference_number as reference, bp.notes, bp.created_at, bp.created_by,
                       bp.payment_number, bp.status,
                       bp.posted_at, bp.posted_by, bp.journal_id, bp.bank_account_id,
                       ba.account_name AS bank_account_name,
                       COALESCE(u_created.name, u_created.fullname, u_created.email) AS created_by_name,
                       COALESCE(u_posted.name, u_posted.fullname, u_posted.email) AS posted_by_name
                FROM bill_payments_v2 bp
                JOIN bill_payment_allocations bpa ON bpa.payment_id = bp.id AND bpa.bill_id = $1
                LEFT JOIN bank_accounts ba ON ba.id = bp.bank_account_id
                LEFT JOIN "User" u_created ON u_created.id = bp.created_by::text
                LEFT JOIN "User" u_posted ON u_posted.id = bp.posted_by::text
                WHERE bp.status != 'voided'
                ORDER BY bp.created_at ASC
            """
            payments = await conn.fetch(payments_query, bill_id)

            # Fetch document_tax_lines (Fase 2.2)
            tax_lines_rows = await conn.fetch(
                """
                SELECT
                    dtl.tax_code_id,
                    tc.name AS tax_code_name,
                    tc.rate AS tax_rate,
                    dtl.direction,
                    dtl.base_amount,
                    dtl.tax_amount
                FROM document_tax_lines dtl
                LEFT JOIN tax_codes tc ON tc.id = dtl.tax_code_id
                WHERE dtl.document_id = $1 AND dtl.document_type = 'BILL'
                  AND dtl.tenant_id = $2
                ORDER BY dtl.created_at
                """,
                bill_id,
                tenant_id,
            )
            tax_lines = [
                {
                    "tax_code_id": str(row["tax_code_id"]),
                    "tax_code_name": row["tax_code_name"] or "Unknown",
                    "tax_rate": float(row["tax_rate"] or 0),
                    "direction": row["direction"],
                    "base_amount": str(row["base_amount"]),
                    "tax_amount": str(row["tax_amount"]),
                }
                for row in tax_lines_rows
            ]
            # Build vendor info
            vendor_name = bill["vendor_name"] or ""
            words = vendor_name.split()
            if len(words) >= 2:
                initials = (words[0][0] + words[1][0]).upper()
            elif len(words) == 1 and len(words[0]) >= 2:
                initials = words[0][:2].upper()
            else:
                initials = "??"

            # Build items list with money as strings
            items_list = [
                {
                    "id": str(item["id"]),
                    "product_id": str(item["product_id"])
                    if item["product_id"]
                    else None,
                    "product_code": item["product_code"],
                    "product_name": item["product_name"]
                    or item.get("linked_product_name"),
                    "qty": int(item["quantity"]),
                    "unit": item["unit"],
                    "price": self._money_str(item["unit_price"]),
                    "discount_percent": float(item["discount_percent"] or 0),
                    "discount_amount": self._money_str(item["discount_amount"]),
                    "total": self._money_str(item["total"] or item["subtotal"]),
                    "batch_no": item["batch_no"],
                    "exp_date": item["exp_date"].strftime("%Y-%m")
                    if item["exp_date"]
                    else None,
                    "bonus_qty": int(item["bonus_qty"] or 0),
                    "tax_code_id": str(item["tax_code_id"])
                    if item.get("tax_code_id")
                    else None,
                    "tax_code_name": item.get("tax_code_name") or "",
                    "tax_rate": float(item["tax_rate"]) if item.get("tax_rate") else 0,
                    "tax_amount": float(item["tax_amount"])
                    if item.get("tax_amount")
                    else 0,
                    "dpp": float(item["dpp"]) if item.get("dpp") else 0,
                }
                for item in items
            ]

            return {
                "id": str(bill["id"]),
                "invoice_number": bill["invoice_number"],
                "ref_no": bill["ref_no"],
                "vendor": {
                    "id": str(bill["vendor_id"]) if bill["vendor_id"] else None,
                    "name": bill["vendor_name"],
                    "initials": initials,
                },
                "status": bill["calculated_status"],
                "issue_date": bill["issue_date"].isoformat(),
                "due_date": bill["due_date"].isoformat(),
                "tax_rate": bill["tax_rate"],
                "tax_code_id": str(bill["tax_code_id"])
                if bill.get("tax_code_id")
                else None,
                "tax_lines": tax_lines,
                "tax_inclusive": bill["tax_inclusive"],
                "invoice_discount_percent": float(
                    bill["invoice_discount_percent"] or 0
                ),
                "invoice_discount_amount": self._money_str(
                    bill["invoice_discount_amount"]
                ),
                "cash_discount_percent": float(bill["cash_discount_percent"] or 0),
                "cash_discount_amount": self._money_str(bill["cash_discount_amount"]),
                "dpp_manual": bill["dpp_manual"],
                "calculation": {
                    "subtotal": self._money_str(bill["subtotal"]),
                    "item_discount_total": self._money_str(bill["item_discount_total"]),
                    "invoice_discount_total": self._money_str(
                        bill["invoice_discount_total"]
                    ),
                    "cash_discount_total": self._money_str(bill["cash_discount_total"]),
                    "dpp": self._money_str(bill["dpp"]),
                    "tax_amount": self._money_str(bill["tax_amount"]),
                    "grand_total": self._money_str(
                        bill["grand_total"] or bill["amount"]
                    ),
                },
                "subtotal": self._money_str(bill["subtotal"]),
                "item_discount_total": self._money_str(bill["item_discount_total"]),
                "invoice_discount_total": self._money_str(
                    bill["invoice_discount_total"]
                ),
                "cash_discount_total": self._money_str(bill["cash_discount_total"]),
                "dpp": self._money_str(bill["dpp"]),
                "tax_amount": self._money_str(bill["tax_amount"]),
                "grand_total": self._money_str(bill["grand_total"] or bill["amount"]),
                "amount": self._money_str(bill["amount"]),
                "amount_paid": self._money_str(
                    bill["journal_paid"]
                ),  # Law 16: journal-derived
                "amount_due": self._money_str(bill["amount_due"]),
                "notes": bill["notes"],
                "operational_status": bill.get("operational_status") or "DRAFT",
                "doc_status": derive_doc_status(bill),
                "accounting_status": bill.get("accounting_status") or "UNPOSTED",
                "vendor_id": str(bill["vendor_id"]) if bill["vendor_id"] else None,
                "vendor_name": bill["vendor_name"],
                "items": items_list,
                "lines": items_list,  # Alias for OpenAPI v2 compatibility
                "payments": [
                    {
                        "id": str(payment["id"]),
                        "payment_number": payment["payment_number"] or "",
                        "amount": self._money_str(payment["amount"]),
                        "total_amount": self._money_str(payment["amount"]),
                        "payment_date": payment["payment_date"].isoformat(),
                        "payment_method": payment["payment_method"],
                        "status": payment["status"],
                        "reference": payment["reference"],
                        "notes": payment["notes"],
                        "created_at": payment["created_at"].isoformat(),
                        "bank_account_id": str(payment["bank_account_id"])
                        if payment.get("bank_account_id")
                        else None,
                        "bank_account_name": payment.get("bank_account_name"),
                        "journal_id": str(payment["journal_id"])
                        if payment.get("journal_id")
                        else None,
                        "created_by_name": payment.get("created_by_name"),
                        "posted_at": payment["posted_at"].isoformat()
                        if payment.get("posted_at")
                        else None,
                        "posted_by_name": payment.get("posted_by_name"),
                    }
                    for payment in payments
                ],
                "posted_at": bill["posted_at"].isoformat()
                if bill["posted_at"]
                else None,
                "created_at": bill["created_at"].isoformat(),
                "updated_at": bill["updated_at"].isoformat(),
            }
