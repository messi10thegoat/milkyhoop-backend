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
from decimal import Decimal

import asyncpg

from ..utils.sorting import build_order_by_clause

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
    7. dpp = dpp_manual OR (after_invoice - cash_discount_total)
    8. tax_amount = dpp * tax_rate / 100
    9. grand_total = dpp + tax_amount
    """

    @staticmethod
    def calculate(
        items: List[Dict],
        invoice_discount_percent: Decimal = Decimal("0"),
        invoice_discount_amount: int = 0,
        cash_discount_percent: Decimal = Decimal("0"),
        cash_discount_amount: int = 0,
        tax_rate: int = 11,
        dpp_manual: Optional[int] = None,
    ) -> Dict[str, int]:
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
        # Step 1 & 2: Calculate subtotal and item discounts
        subtotal = 0
        item_discount_total = 0

        for item in items:
            qty = int(item.get("qty", 0))
            price = int(item.get("price", 0))
            discount_pct = Decimal(str(item.get("discount_percent", 0)))

            line_subtotal = qty * price
            line_discount = int(line_subtotal * discount_pct / 100)

            subtotal += line_subtotal
            item_discount_total += line_discount

        after_item_discount = subtotal - item_discount_total

        # Step 3: Invoice discount (% takes precedence over amount)
        if invoice_discount_percent > 0:
            invoice_discount_total = int(
                after_item_discount * invoice_discount_percent / 100
            )
        else:
            invoice_discount_total = invoice_discount_amount

        after_invoice_discount = after_item_discount - invoice_discount_total

        # Step 4: Cash discount (% takes precedence over amount)
        if cash_discount_percent > 0:
            cash_discount_total = int(
                after_invoice_discount * cash_discount_percent / 100
            )
        else:
            cash_discount_total = cash_discount_amount

        # Step 5: DPP (manual override or auto)
        auto_dpp = after_invoice_discount - cash_discount_total
        dpp = dpp_manual if dpp_manual is not None else auto_dpp

        # Step 6: Tax
        tax_amount = int(dpp * tax_rate / 100)

        # Step 7: Grand total
        grand_total = dpp + tax_amount

        return {
            "subtotal": subtotal,
            "item_discount_total": item_discount_total,
            "invoice_discount_total": invoice_discount_total,
            "cash_discount_total": cash_discount_total,
            "dpp": dpp,
            "tax_amount": tax_amount,
            "grand_total": grand_total,
        }

    @staticmethod
    def calculate_item_total(
        qty: int, price: int, discount_percent: Decimal
    ) -> Dict[str, int]:
        """
        Calculate single item totals.

        Returns:
            Dict with subtotal, discount_amount, total
        """
        subtotal = qty * price
        discount_amount = int(subtotal * discount_percent / 100)
        total = subtotal - discount_amount

        return {
            "subtotal": subtotal,
            "discount_amount": discount_amount,
            "total": total,
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
            conditions = ["tenant_id = $1"]
            params: List[Any] = [tenant_id]
            param_idx = 2

            # Status filter (with dynamic overdue calculation)
            if status != "all":
                if status == "overdue":
                    # Overdue = unpaid/partial AND due_date < today
                    conditions.append(
                        "(status IN ('unpaid', 'partial') AND due_date < CURRENT_DATE)"
                    )
                else:
                    conditions.append(f"status = ${param_idx}")
                    params.append(status)
                    param_idx += 1

            # Search filter
            if search:
                conditions.append(
                    f"(invoice_number ILIKE ${param_idx} OR vendor_name ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1

            # Date range filter
            if due_date_from:
                conditions.append(f"due_date >= ${param_idx}")
                params.append(due_date_from)
                param_idx += 1

            if due_date_to:
                conditions.append(f"due_date <= ${param_idx}")
                params.append(due_date_to)
                param_idx += 1

            # Vendor filter
            if vendor_id:
                conditions.append(f"vendor_id = ${param_idx}")
                params.append(vendor_id)
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # Build compound ORDER BY clause
            field_mapping = {
                "created_at": "created_at",
                "date": "issue_date",
                "number": "invoice_number",
                "supplier": "vendor_name",
                "due_date": "due_date",
                "amount": "COALESCE(grand_total, amount)",
                "balance": "(COALESCE(amount, 0) - COALESCE(amount_paid, 0))",
                "updated_at": "updated_at",
                # Status ordering: overdue(1) > unpaid(2) > partial(3) > paid(4) > void(6)
                "status": """CASE
                    WHEN status = 'void' THEN 6
                    WHEN amount_paid >= amount THEN 4
                    WHEN amount_paid > 0 AND due_date < CURRENT_DATE THEN 1
                    WHEN amount_paid > 0 THEN 3
                    WHEN due_date < CURRENT_DATE THEN 1
                    ELSE 2
                END""",
                # Legacy aliases
                "vendor_name": "vendor_name",
                "invoice_number": "invoice_number",
            }

            order_by_clause = build_order_by_clause(sort_fields, field_mapping)

            # Get total count
            count_query = f"SELECT COUNT(*) FROM bills WHERE {where_clause}"
            total = await conn.fetchval(count_query, *params)

            # Get items with dynamic status calculation
            query = f"""
                SELECT
                    id,
                    invoice_number,
                    vendor_id,
                    vendor_name,
                    amount,
                    amount_paid,
                    (amount - amount_paid) as amount_due,
                    CASE
                        WHEN status = 'void' THEN 'void'
                        WHEN amount_paid >= amount THEN 'paid'
                        WHEN amount_paid > 0 AND due_date < CURRENT_DATE THEN 'overdue'
                        WHEN amount_paid > 0 THEN 'partial'
                        WHEN due_date < CURRENT_DATE THEN 'overdue'
                        ELSE 'unpaid'
                    END as status,
                    issue_date,
                    due_date,
                    created_at,
                    updated_at
                FROM bills
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
            # Get bill
            bill_query = """
                SELECT
                    b.*,
                    (b.amount - b.amount_paid) as amount_due,
                    CASE
                        WHEN b.status = 'void' THEN 'void'
                        WHEN b.amount_paid >= b.amount THEN 'paid'
                        WHEN b.amount_paid > 0 AND b.due_date < CURRENT_DATE THEN 'overdue'
                        WHEN b.amount_paid > 0 THEN 'partial'
                        WHEN b.due_date < CURRENT_DATE THEN 'overdue'
                        ELSE 'unpaid'
                    END as calculated_status
                FROM bills b
                WHERE b.id = $1 AND b.tenant_id = $2
            """
            bill = await conn.fetchrow(bill_query, bill_id, tenant_id)

            if not bill:
                return None

            # Get items
            items_query = """
                SELECT
                    bi.*,
                    p.nama_produk as product_name
                FROM bill_items bi
                LEFT JOIN products p ON bi.product_id = p.id
                WHERE bi.bill_id = $1
                ORDER BY bi.line_number
            """
            items = await conn.fetch(items_query, bill_id)

            # Get payments
            payments_query = """
                SELECT *
                FROM bill_payments
                WHERE bill_id = $1
                ORDER BY payment_date DESC
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
                "amount": int(bill["amount"]),
                "amount_paid": int(bill["amount_paid"]),
                "amount_due": int(bill["amount_due"]),
                "status": bill["calculated_status"],
                "issue_date": bill["issue_date"].isoformat(),
                "due_date": bill["due_date"].isoformat(),
                "notes": bill["notes"],
                "items": [
                    {
                        "id": str(item["id"]),
                        "product_id": str(item["product_id"])
                        if item["product_id"]
                        else None,
                        "product_name": item.get("product_name"),
                        "description": item["description"],
                        "quantity": float(item["quantity"]),
                        "unit": item["unit"],
                        "unit_price": int(item["unit_price"]),
                        "subtotal": int(item["subtotal"]),
                    }
                    for item in items
                ],
                "payments": [
                    {
                        "id": str(payment["id"]),
                        "amount": int(payment["amount"]),
                        "payment_date": payment["payment_date"].isoformat(),
                        "payment_method": payment["payment_method"],
                        "reference": payment["reference"],
                        "notes": payment["notes"],
                        "created_at": payment["created_at"].isoformat(),
                    }
                    for payment in payments
                ],
                "attachments": [
                    {
                        "id": str(att["id"]),
                        "filename": att["filename"],
                        "url": att["file_path"],  # TODO: Generate signed URL
                        "uploaded_at": att["uploaded_at"].isoformat(),
                    }
                    for att in attachments
                ],
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

                    # Update bill with AP link
                    await conn.execute(
                        """
                        UPDATE bills
                        SET ap_id = $1, journal_id = $2
                        WHERE id = $3
                    """,
                        ap_id,
                        journal_id,
                        bill_id,
                    )

                    # UPDATE INVENTORY for inventory-tracked items
                    # Get default warehouse for tenant
                    default_warehouse = await conn.fetchrow(
                        "SELECT id FROM warehouses WHERE tenant_id = $1 AND is_default = true LIMIT 1",
                        tenant_id
                    )
                    warehouse_id = default_warehouse["id"] if default_warehouse else None

                    for item in items:
                        product_id = item.get("product_id")
                        if not product_id:
                            continue

                        # Check if product is inventory-tracked
                        product = await conn.fetchrow(
                            """
                            SELECT id, nama_produk, item_code, track_inventory, item_type
                            FROM products WHERE id = $1
                            """,
                            product_id
                        )
                        
                        if not product or product["item_type"] != "goods" or not product.get("track_inventory", True):
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
                            tenant_id, product_id
                        )
                        current_balance = Decimal(str(balance_row["balance"])) if balance_row else Decimal("0")
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
                            tenant_id, product_id
                        )
                        
                        if avg_cost_row and avg_cost_row["total_qty"] > 0:
                            old_value = Decimal(str(avg_cost_row["total_value"]))
                            old_qty = Decimal(str(avg_cost_row["total_qty"]))
                            new_avg_cost = (old_value + total_cost) / (old_qty + quantity)
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
                                warehouse_id, journal_id, created_by, notes
                            ) VALUES (
                                $1, $2, $3, $4,
                                'PURCHASE', $5, 'BILL', $6, $7,
                                $8, 0, $9,
                                $10, $11, $12,
                                $13, $14, $15, $16
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
                            f"Purchase from {vendor_name}"
                        )

                        logger.info(f"Inventory updated for product {product_id}: +{quantity} @ {unit_cost}")

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
                        SET {', '.join(updates)}
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
        Delete a bill. Only allowed if no payments have been made.

        Returns:
            {success: bool, message: str}
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
                return {"success": False, "message": "Bill not found"}

            # Check voided status
            if bill["status"] == "void":
                return {"success": False, "message": "Cannot delete voided bill"}

            # Check status_v2 (only draft can be deleted)
            if bill.get("status_v2") and bill["status_v2"] != "draft":
                return {
                    "success": False,
                    "message": f"Cannot delete bill with status '{bill['status_v2']}'. Only draft bills can be deleted. Use void instead.",
                }

            if bill["amount_paid"] > 0:
                return {
                    "success": False,
                    "message": "Cannot delete bill with payments. Void the bill instead.",
                }

            # Delete bill (items and attachments cascade)
            await conn.execute(
                "DELETE FROM bills WHERE id = $1 AND tenant_id = $2", bill_id, tenant_id
            )

            return {"success": True, "message": "Bill deleted successfully"}

    # =========================================================================
    # RECORD PAYMENT
    # =========================================================================
    async def record_payment(
        self, tenant_id: str, bill_id: UUID, request: Dict[str, Any], user_id: UUID
    ) -> Dict[str, Any]:
        """
        Record a payment for a bill.

        Account handling:
        - bank_account_id (preferred): Links to bank_accounts, creates bank transaction
        - account_id (legacy): Direct CoA UUID, no bank transaction

        Sign convention for bank transactions:
        - Bank/cash (ASSET): negative amount (decreases asset)
        - Credit card (LIABILITY): positive amount (increases liability)

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Get bill
            bill = await conn.fetchrow(
                """
                SELECT id, amount, amount_paid, status, ap_id, vendor_name
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

            amount_due = bill["amount"] - bill["amount_paid"]
            payment_amount = int(request["amount"])

            if payment_amount > amount_due:
                return {
                    "success": False,
                    "message": f"Payment amount ({payment_amount}) exceeds amount due ({amount_due})",
                    "data": None,
                }

            async with conn.transaction():
                payment_date = request.get("payment_date") or date.today()

                # Resolve account - bank_account_id takes precedence
                bank_account_id = request.get("bank_account_id")
                coa_id = request.get("account_id")
                bank_transaction_id = None
                bank_account = None

                if bank_account_id:
                    # NEW FLOW: Use bank_account_id
                    bank_account = await conn.fetchrow(
                        """
                        SELECT id, coa_id, account_type, account_name, current_balance
                        FROM bank_accounts
                        WHERE id = $1 AND tenant_id = $2 AND is_active = true
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

                    coa_id = bank_account["coa_id"]

                    # Determine amount sign based on account type
                    # Credit card = LIABILITY: positive amount (increases liability)
                    # Bank/cash = ASSET: negative amount (decreases asset)
                    if bank_account["account_type"] == "credit_card":
                        tx_amount = payment_amount  # positive = liability increase
                        tx_type = "charge"
                    else:
                        tx_amount = -payment_amount  # negative = asset decrease
                        tx_type = "payment_made"

                    # Create bank transaction (trigger updates balance atomically)
                    import uuid as uuid_module

                    bank_transaction_id = uuid_module.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO bank_transactions (
                            id, tenant_id, bank_account_id, transaction_date,
                            transaction_type, amount, running_balance,
                            reference_type, reference_id, description,
                            payee_payer, created_by
                        ) VALUES ($1, $2, $3, $4, $5, $6, 0, 'bill', $7, $8, $9, $10)
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
                        user_id,
                    )

                    logger.info(
                        f"Bank transaction created: {bank_transaction_id}, "
                        f"type={tx_type}, amount={tx_amount}, bank={bank_account['account_name']}"
                    )

                elif not coa_id:
                    return {
                        "success": False,
                        "message": "Either bank_account_id or account_id is required",
                        "data": None,
                    }

                # 1. Insert payment record
                payment_id = await conn.fetchval(
                    """
                    INSERT INTO bill_payments (
                        tenant_id, bill_id, amount, payment_date, payment_method,
                        account_id, bank_account_id, bank_transaction_id,
                        reference, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                """,
                    tenant_id,
                    bill_id,
                    payment_amount,
                    payment_date,
                    request["payment_method"],
                    coa_id,
                    bank_account_id,
                    bank_transaction_id,
                    request.get("reference"),
                    request.get("notes"),
                    user_id,
                )

                # Note: Trigger will update bill.amount_paid and status

                # 2. Apply payment to AP (REQUIRED - atomic with payment)
                # Golden Rule: Payment must ALWAYS update AP and create Journal
                journal_id = None

                if not bill["ap_id"]:
                    logger.warning(
                        f"Bill {bill_id} has no AP record. Skipping AP integration. "
                        "This may indicate a data integrity issue."
                    )
                elif not self.accounting:
                    logger.warning(
                        "Accounting kernel not available. Skipping AP integration."
                    )
                else:
                    ap_result = await self.accounting.apply_ap_payment(
                        tenant_id=tenant_id,
                        ap_id=bill["ap_id"],
                        payment_amount=Decimal(payment_amount),
                        payment_date=payment_date,
                        payment_method=request["payment_method"],
                        account_id=coa_id,
                    )

                    if not ap_result.get("success"):
                        # Rollback by raising exception
                        raise ValueError(
                            f"AP payment failed: {ap_result.get('error', 'Unknown error')}. "
                            "Payment rolled back."
                        )

                    journal_id = ap_result.get("journal_id")

                # Update payment and bank transaction with journal link
                if journal_id:
                    await conn.execute(
                        """
                        UPDATE bill_payments
                        SET journal_id = $1
                        WHERE id = $2
                    """,
                        journal_id,
                        payment_id,
                    )

                    if bank_transaction_id:
                        await conn.execute(
                            """
                            UPDATE bank_transactions
                            SET journal_id = $1
                            WHERE id = $2
                        """,
                            journal_id,
                            bank_transaction_id,
                        )

                # Get updated bill status
                updated_bill = await conn.fetchrow(
                    """
                    SELECT amount, amount_paid, status FROM bills WHERE id = $1
                """,
                    bill_id,
                )

                new_amount_due = updated_bill["amount"] - updated_bill["amount_paid"]

                return {
                    "success": True,
                    "message": "Payment recorded successfully",
                    "data": {
                        "id": str(payment_id),
                        "bill_id": str(bill_id),
                        "amount": payment_amount,
                        "bill_status": updated_bill["status"],
                        "amount_due": new_amount_due,
                        "bank_transaction_id": str(bank_transaction_id)
                        if bank_transaction_id
                        else None,
                    },
                }

    # =========================================================================
    # MARK AS PAID
    # =========================================================================
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
        Void a bill. Only allowed if no payments have been made.

        For bills with payments, use refund flow instead:
        1. Refund the payments first
        2. Then void the bill

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Get bill with amount_paid
            bill = await conn.fetchrow(
                """
                SELECT id, status, journal_id, ap_id, amount_paid
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
                    "message": "Bill is already voided",
                    "data": None,
                }

            # Block void if payments exist
            if bill["amount_paid"] > 0:
                return {
                    "success": False,
                    "message": "Cannot void bill with payments. Refund the payments first.",
                    "data": None,
                }

            async with conn.transaction():
                reason = request.get("reason", "Voided")

                # 1. Void AP and create reversal journal (REQUIRED - atomic)
                # Must void AP before updating bill status
                if not bill["ap_id"]:
                    logger.warning(
                        f"Bill {bill_id} has no AP record - data integrity issue"
                    )
                    # Allow void for data cleanup, but log warning
                elif not self.accounting:
                    raise ValueError(
                        "Accounting kernel not available. Void requires AP integration."
                    )
                else:
                    ap_result = await self.accounting.void_payable(
                        tenant_id=tenant_id,
                        ap_id=bill["ap_id"],
                        void_reason=reason,
                        voided_by=user_id,
                    )

                    if not ap_result.get("success"):
                        # Rollback by raising exception
                        raise ValueError(
                            f"AP void failed: {ap_result.get('error', 'Unknown error')}. "
                            "Void rolled back."
                        )

                # 2. Update bill status
                await conn.execute(
                    """
                    UPDATE bills
                    SET status = 'void',
                        voided_at = NOW(),
                        voided_reason = $1,
                        updated_at = NOW()
                    WHERE id = $2
                """,
                    reason,
                    bill_id,
                )

                return {
                    "success": True,
                    "message": "Bill voided successfully",
                    "data": {
                        "id": str(bill_id),
                        "status": "void",
                        "voided_at": datetime.now().isoformat(),
                        "voided_reason": reason,
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

            # Get summary statistics
            # NOTE: amount = sisa tagihan yang belum dibayar (remaining), bukan total faktur
            query = """
                SELECT
                    COUNT(*) as total_count,
                    COALESCE(SUM(amount), 0) as total_amount,
                    COALESCE(SUM(amount - COALESCE(amount_paid, 0)), 0) as total_remaining,
                    COUNT(DISTINCT vendor_name) as vendor_count,
                    -- Paid: sudah lunas, sisa = 0
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) >= amount AND status != 'void') as paid_count,
                    0 as paid_remaining,
                    -- Partial: bayar sebagian, sisa = amount - amount_paid
                    COUNT(*) FILTER (WHERE amount_paid > 0 AND amount_paid < amount AND status != 'void') as partial_count,
                    COALESCE(SUM(amount - amount_paid) FILTER (WHERE amount_paid > 0 AND amount_paid < amount AND status != 'void'), 0) as partial_remaining,
                    -- Unpaid: belum bayar sama sekali, sisa = amount (full)
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) = 0 AND due_date >= CURRENT_DATE AND status != 'void') as unpaid_count,
                    COALESCE(SUM(amount) FILTER (WHERE COALESCE(amount_paid, 0) = 0 AND due_date >= CURRENT_DATE AND status != 'void'), 0) as unpaid_remaining,
                    -- Overdue: jatuh tempo dan belum lunas, sisa = amount - amount_paid
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date < CURRENT_DATE AND status != 'void') as overdue_count,
                    COALESCE(SUM(amount - COALESCE(amount_paid, 0)) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date < CURRENT_DATE AND status != 'void'), 0) as overdue_remaining
                FROM bills
                WHERE tenant_id = $1
                    AND issue_date >= $2
                    AND issue_date < $3
                    AND status != 'void'
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

            query = """
                SELECT
                    -- Total outstanding (all non-void, non-paid bills)
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND status != 'void') as total_count,
                    COALESCE(SUM(amount - COALESCE(amount_paid, 0)) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND status != 'void'), 0) as total_outstanding,
                    COUNT(DISTINCT vendor_name) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND status != 'void') as vendor_count,

                    -- Paid: lunas (sisa = 0)
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) >= amount AND status != 'void') as paid_count,
                    0 as paid_amount,

                    -- Partial: bayar sebagian, belum jatuh tempo (mutually exclusive with overdue)
                    COUNT(*) FILTER (WHERE amount_paid > 0 AND amount_paid < amount AND due_date >= CURRENT_DATE AND status != 'void') as partial_count,
                    COALESCE(SUM(amount - amount_paid) FILTER (WHERE amount_paid > 0 AND amount_paid < amount AND due_date >= CURRENT_DATE AND status != 'void'), 0) as partial_amount,

                    -- Unpaid: belum bayar sama sekali, belum jatuh tempo (mutually exclusive with overdue)
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) = 0 AND due_date >= CURRENT_DATE AND status != 'void') as unpaid_count,
                    COALESCE(SUM(amount) FILTER (WHERE COALESCE(amount_paid, 0) = 0 AND due_date >= CURRENT_DATE AND status != 'void'), 0) as unpaid_amount,

                    -- Overdue: jatuh tempo dan belum lunas (includes partial + unpaid yang sudah lewat due_date)
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date < CURRENT_DATE AND status != 'void') as overdue_count,
                    COALESCE(SUM(amount - COALESCE(amount_paid, 0)) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date < CURRENT_DATE AND status != 'void'), 0) as overdue_amount,

                    -- Urgency metrics
                    -- Oldest overdue (days since due_date)
                    COALESCE(MAX(CURRENT_DATE - due_date) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date < CURRENT_DATE AND status != 'void'), 0) as overdue_oldest_days,
                    -- Largest single overdue amount
                    COALESCE(MAX(amount - COALESCE(amount_paid, 0)) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date < CURRENT_DATE AND status != 'void'), 0) as overdue_largest,
                    -- Due within 7 days (excluding overdue)
                    COUNT(*) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date >= CURRENT_DATE AND due_date <= CURRENT_DATE + INTERVAL '7 days' AND status != 'void') as due_within_7_days_count,
                    COALESCE(SUM(amount - COALESCE(amount_paid, 0)) FILTER (WHERE COALESCE(amount_paid, 0) < amount AND due_date >= CURRENT_DATE AND due_date <= CURRENT_DATE + INTERVAL '7 days' AND status != 'void'), 0) as due_within_7_days_amount
                FROM bills
                WHERE tenant_id = $1
            """

            row = await conn.fetchrow(query, tenant_id)

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
                    tax_rate=request.get("tax_rate", 11),
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
                        dpp, tax_amount, grand_total
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19,
                        $20, $21, $22, $23, $24, $25
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
                        qty = int(item["qty"])
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

                    # Convert exp_date string to date if provided
                    exp_date = None
                    if item.get("exp_date"):
                        try:
                            exp_date = date.fromisoformat(f"{item['exp_date']}-01")
                        except ValueError:
                            return {
                                "success": False,
                                "message": f"Item {idx}: format exp_date harus YYYY-MM (contoh: 2025-12)",
                                "data": None,
                            }

                    await conn.execute(
                        """
                        INSERT INTO bill_items (
                            bill_id, product_id, product_code, product_name,
                            description, quantity, unit, unit_price,
                            discount_percent, discount_amount, total, subtotal,
                            batch_no, exp_date, bonus_qty, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
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
                    )

                # 7. If posted status, create AP and journal entry
                ap_id = None
                journal_id = None

                if status == "posted":
                    if self.accounting:
                        ap_result = await self.accounting.create_payable(
                            tenant_id=tenant_id,
                            supplier_name=vendor_name,
                            bill_number=invoice_number,
                            bill_date=issue_date,
                            due_date=due_date,
                            amount=Decimal(calc["grand_total"]),
                            source_type="BILL",
                            source_id=bill_id,
                        )

                        if not ap_result.get("success"):
                            raise ValueError(
                                f"AP creation failed: {ap_result.get('error')}"
                            )

                        ap_id = ap_result.get("ap_id")
                        journal_id = ap_result.get("journal_id")

                        await conn.execute(
                            """
                            UPDATE bills
                            SET ap_id = $1, journal_id = $2, posted_at = NOW(), posted_by = $3
                            WHERE id = $4
                        """,
                            ap_id,
                            journal_id,
                            user_id,
                            bill_id,
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
                    },
                }

    async def post_bill(
        self, tenant_id: str, bill_id: UUID, user_id: UUID
    ) -> Dict[str, Any]:
        """
        Transition bill from draft to posted.

        This action:
        - Creates an AP (Accounts Payable) record
        - Creates a journal entry (DR Inventory/Expense, CR AP)
        - Changes status_v2 from 'draft' to 'posted'

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Get bill
            bill = await conn.fetchrow(
                """
                SELECT id, status_v2, invoice_number, vendor_name, vendor_id,
                       issue_date, due_date, grand_total
                FROM bills
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
                    "message": f"Cannot post bill with status '{bill['status_v2']}'. Only draft bills can be posted.",
                    "data": None,
                }

            async with conn.transaction():
                # Create AP and journal
                ap_id = None
                journal_id = None

                if self.accounting:
                    ap_result = await self.accounting.create_payable(
                        tenant_id=tenant_id,
                        supplier_name=bill["vendor_name"],
                        bill_number=bill["invoice_number"],
                        bill_date=bill["issue_date"],
                        due_date=bill["due_date"],
                        amount=Decimal(bill["grand_total"]),
                        source_type="BILL",
                        source_id=bill_id,
                    )

                    if not ap_result.get("success"):
                        raise ValueError(
                            f"AP creation failed: {ap_result.get('error')}"
                        )

                    ap_id = ap_result.get("ap_id")
                    journal_id = ap_result.get("journal_id")
                else:
                    logger.warning(
                        f"Accounting kernel not available. Bill {bill_id} posted without AP."
                    )

                # Update bill status
                await conn.execute(
                    """
                    UPDATE bills
                    SET status_v2 = 'posted',
                        ap_id = $1,
                        journal_id = $2,
                        posted_at = NOW(),
                        posted_by = $3,
                        updated_at = NOW()
                    WHERE id = $4
                """,
                    ap_id,
                    journal_id,
                    user_id,
                    bill_id,
                )

                # UPDATE INVENTORY for inventory-tracked items
                # Get bill items with product details
                bill_items = await conn.fetch(
                    """
                    SELECT bi.product_id, bi.quantity, bi.unit_price, bi.description,
                           p.nama_produk, p.item_code, p.track_inventory, p.item_type
                    FROM bill_items bi
                    LEFT JOIN products p ON p.id = bi.product_id
                    WHERE bi.bill_id = $1 AND bi.product_id IS NOT NULL
                    """,
                    bill_id
                )

                # Get default warehouse for tenant
                default_warehouse = await conn.fetchrow(
                    "SELECT id FROM warehouses WHERE tenant_id = $1 AND is_default = true LIMIT 1",
                    tenant_id
                )
                warehouse_id = default_warehouse["id"] if default_warehouse else None

                for item in bill_items:
                    # Only process inventory-tracked goods
                    if item["item_type"] != "goods" or not item.get("track_inventory", True):
                        continue

                    product_id = item["product_id"]
                    quantity = Decimal(str(item["quantity"]))
                    unit_cost = Decimal(str(item["unit_price"]))
                    total_cost = quantity * unit_cost

                    # Get current balance for this product
                    balance_row = await conn.fetchrow(
                        """
                        SELECT COALESCE(SUM(quantity_in) - SUM(quantity_out), 0) as balance
                        FROM inventory_ledger
                        WHERE tenant_id = $1 AND product_id = $2
                        """,
                        tenant_id, product_id
                    )
                    current_balance = Decimal(str(balance_row["balance"])) if balance_row else Decimal("0")
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
                        tenant_id, product_id
                    )
                    
                    if avg_cost_row and avg_cost_row["total_qty"] > 0:
                        old_value = Decimal(str(avg_cost_row["total_value"]))
                        old_qty = Decimal(str(avg_cost_row["total_qty"]))
                        new_avg_cost = (old_value + total_cost) / (old_qty + quantity)
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
                            warehouse_id, journal_id, created_by, notes
                        ) VALUES (
                            $1, $2, $3, $4,
                            'PURCHASE', $5, 'BILL', $6, $7,
                            $8, 0, $9,
                            $10, $11, $12,
                            $13, $14, $15, $16
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
                        f"Purchase from {bill['vendor_name']}"
                    )

                    logger.info(f"Inventory updated for product {product_id}: +{quantity} @ {unit_cost}")

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
                SELECT id, status_v2, vendor_id FROM bills
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
                        tax_rate=request.get("tax_rate", 11),
                        dpp_manual=request.get("dpp_manual"),
                    )

                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM bill_items WHERE bill_id = $1", bill_id
                    )

                    # Insert new items
                    for idx, item in enumerate(items, start=1):
                        qty = int(item["qty"])
                        price = int(item["price"])
                        discount_pct = Decimal(str(item.get("discount_percent", 0)))
                        item_calc = BillCalculator.calculate_item_total(
                            qty, price, discount_pct
                        )

                        exp_date = None
                        if item.get("exp_date"):
                            exp_date = date.fromisoformat(f"{item['exp_date']}-01")

                        await conn.execute(
                            """
                            INSERT INTO bill_items (
                                bill_id, product_id, product_code, product_name,
                                description, quantity, unit, unit_price,
                                discount_percent, discount_amount, total, subtotal,
                                batch_no, exp_date, bonus_qty, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
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
                    SET {', '.join(updates)}
                    WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
                """
                await conn.execute(query, *params)

                logger.info(f"Bill V2 updated: {bill_id}")

                return {
                    "success": True,
                    "message": "Bill updated successfully",
                    "data": {"id": str(bill_id), "calculation": calc},
                }

    async def get_bill_v2(
        self, tenant_id: str, bill_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """
        Get bill detail with extended V2 fields.

        Returns:
            Bill detail dict or None if not found
        """
        async with self.pool.acquire() as conn:
            # Get bill with V2 fields
            bill_query = """
                SELECT
                    b.*,
                    (b.amount - b.amount_paid) as amount_due
                FROM bills b
                WHERE b.id = $1 AND b.tenant_id = $2
            """
            bill = await conn.fetchrow(bill_query, bill_id, tenant_id)

            if not bill:
                return None

            # Get items with V2 fields
            items_query = """
                SELECT
                    bi.*,
                    p.nama_produk as linked_product_name
                FROM bill_items bi
                LEFT JOIN products p ON bi.product_id = p.id
                WHERE bi.bill_id = $1
                ORDER BY bi.line_number
            """
            items = await conn.fetch(items_query, bill_id)

            # Get payments
            payments_query = """
                SELECT * FROM bill_payments
                WHERE bill_id = $1
                ORDER BY payment_date DESC
            """
            payments = await conn.fetch(payments_query, bill_id)

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
                "ref_no": bill["ref_no"],
                "vendor": {
                    "id": str(bill["vendor_id"]) if bill["vendor_id"] else None,
                    "name": bill["vendor_name"],
                    "initials": initials,
                },
                "status": bill["status_v2"] or bill["status"],
                "issue_date": bill["issue_date"].isoformat(),
                "due_date": bill["due_date"].isoformat(),
                "tax_rate": bill["tax_rate"],
                "tax_inclusive": bill["tax_inclusive"],
                "invoice_discount_percent": float(
                    bill["invoice_discount_percent"] or 0
                ),
                "invoice_discount_amount": int(bill["invoice_discount_amount"] or 0),
                "cash_discount_percent": float(bill["cash_discount_percent"] or 0),
                "cash_discount_amount": int(bill["cash_discount_amount"] or 0),
                "dpp_manual": bill["dpp_manual"],
                "calculation": {
                    "subtotal": int(bill["subtotal"] or 0),
                    "item_discount_total": int(bill["item_discount_total"] or 0),
                    "invoice_discount_total": int(bill["invoice_discount_total"] or 0),
                    "cash_discount_total": int(bill["cash_discount_total"] or 0),
                    "dpp": int(bill["dpp"] or 0),
                    "tax_amount": int(bill["tax_amount"] or 0),
                    "grand_total": int(bill["grand_total"] or bill["amount"] or 0),
                },
                "amount_paid": int(bill["amount_paid"]),
                "amount_due": int(bill["amount_due"]),
                "notes": bill["notes"],
                "items": [
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
                        "price": int(item["unit_price"]),
                        "discount_percent": float(item["discount_percent"] or 0),
                        "discount_amount": int(item["discount_amount"] or 0),
                        "total": int(item["total"] or item["subtotal"] or 0),
                        "batch_no": item["batch_no"],
                        "exp_date": item["exp_date"].strftime("%Y-%m")
                        if item["exp_date"]
                        else None,
                        "bonus_qty": int(item["bonus_qty"] or 0),
                    }
                    for item in items
                ],
                "payments": [
                    {
                        "id": str(payment["id"]),
                        "amount": int(payment["amount"]),
                        "payment_date": payment["payment_date"].isoformat(),
                        "payment_method": payment["payment_method"],
                        "reference": payment["reference"],
                        "notes": payment["notes"],
                        "created_at": payment["created_at"].isoformat(),
                    }
                    for payment in payments
                ],
                "posted_at": bill["posted_at"].isoformat()
                if bill["posted_at"]
                else None,
                "created_at": bill["created_at"].isoformat(),
                "updated_at": bill["updated_at"].isoformat(),
            }
