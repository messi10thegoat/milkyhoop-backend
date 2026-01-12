"""
Bills Service - Business logic for Faktur Pembelian module.

This service handles bill CRUD operations and integrates with the
accounting kernel for AP management and journal entries.
"""

import logging
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal

import asyncpg

logger = logging.getLogger(__name__)


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
        sort_by: str = "created_at",
        sort_order: str = "desc",
        due_date_from: Optional[date] = None,
        due_date_to: Optional[date] = None,
        vendor_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """
        List bills with filtering, sorting, and pagination.

        Returns:
            {items: [...], total: int, has_more: bool}
        """
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
                        f"(status IN ('unpaid', 'partial') AND due_date < CURRENT_DATE)"
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

            # Validate sort_by to prevent SQL injection
            valid_sort_fields = {
                "created_at": "created_at",
                "due_date": "due_date",
                "amount": "amount",
                "vendor_name": "vendor_name",
                "invoice_number": "invoice_number"
            }
            sort_field = valid_sort_fields.get(sort_by, "created_at")
            sort_dir = "DESC" if sort_order.lower() == "desc" else "ASC"

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
                ORDER BY {sort_field} {sort_dir}
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            params.extend([limit, skip])

            rows = await conn.fetch(query, *params)

            items = []
            for row in rows:
                # Generate initials from vendor name
                vendor_name = row['vendor_name'] or ''
                words = vendor_name.split()
                if len(words) >= 2:
                    initials = (words[0][0] + words[1][0]).upper()
                elif len(words) == 1 and len(words[0]) >= 2:
                    initials = words[0][:2].upper()
                else:
                    initials = "??"

                items.append({
                    "id": str(row['id']),
                    "invoice_number": row['invoice_number'],
                    "vendor": {
                        "id": str(row['vendor_id']) if row['vendor_id'] else None,
                        "name": row['vendor_name'],
                        "initials": initials
                    },
                    "amount": int(row['amount']),
                    "amount_paid": int(row['amount_paid']),
                    "amount_due": int(row['amount_due']),
                    "status": row['status'],
                    "issue_date": row['issue_date'].isoformat(),
                    "due_date": row['due_date'].isoformat(),
                    "created_at": row['created_at'].isoformat(),
                    "updated_at": row['updated_at'].isoformat()
                })

            return {
                "items": items,
                "total": total,
                "has_more": (skip + limit) < total
            }

    # =========================================================================
    # GET BILL DETAIL
    # =========================================================================
    async def get_bill(
        self,
        tenant_id: str,
        bill_id: UUID
    ) -> Optional[Dict[str, Any]]:
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
            vendor_name = bill['vendor_name'] or ''
            words = vendor_name.split()
            if len(words) >= 2:
                initials = (words[0][0] + words[1][0]).upper()
            elif len(words) == 1 and len(words[0]) >= 2:
                initials = words[0][:2].upper()
            else:
                initials = "??"

            return {
                "id": str(bill['id']),
                "invoice_number": bill['invoice_number'],
                "vendor": {
                    "id": str(bill['vendor_id']) if bill['vendor_id'] else None,
                    "name": bill['vendor_name'],
                    "initials": initials
                },
                "amount": int(bill['amount']),
                "amount_paid": int(bill['amount_paid']),
                "amount_due": int(bill['amount_due']),
                "status": bill['calculated_status'],
                "issue_date": bill['issue_date'].isoformat(),
                "due_date": bill['due_date'].isoformat(),
                "notes": bill['notes'],
                "items": [
                    {
                        "id": str(item['id']),
                        "product_id": str(item['product_id']) if item['product_id'] else None,
                        "product_name": item.get('product_name'),
                        "description": item['description'],
                        "quantity": float(item['quantity']),
                        "unit": item['unit'],
                        "unit_price": int(item['unit_price']),
                        "subtotal": int(item['subtotal'])
                    }
                    for item in items
                ],
                "payments": [
                    {
                        "id": str(payment['id']),
                        "amount": int(payment['amount']),
                        "payment_date": payment['payment_date'].isoformat(),
                        "payment_method": payment['payment_method'],
                        "reference": payment['reference'],
                        "notes": payment['notes'],
                        "created_at": payment['created_at'].isoformat()
                    }
                    for payment in payments
                ],
                "attachments": [
                    {
                        "id": str(att['id']),
                        "filename": att['filename'],
                        "url": att['file_path'],  # TODO: Generate signed URL
                        "uploaded_at": att['uploaded_at'].isoformat()
                    }
                    for att in attachments
                ],
                "created_at": bill['created_at'].isoformat(),
                "updated_at": bill['updated_at'].isoformat()
            }

    # =========================================================================
    # CREATE BILL
    # =========================================================================
    async def create_bill(
        self,
        tenant_id: str,
        request: Dict[str, Any],
        user_id: UUID
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
                invoice_number = request.get('invoice_number')
                if not invoice_number:
                    invoice_number = await conn.fetchval(
                        "SELECT generate_bill_number($1, 'BILL')",
                        tenant_id
                    )

                # 2. Get vendor name
                vendor_name = request.get('vendor_name')
                vendor_id = request.get('vendor_id')

                if vendor_id and not vendor_name:
                    # Look up vendor name from suppliers table
                    vendor_row = await conn.fetchrow(
                        "SELECT nama_supplier FROM suppliers WHERE id = $1",
                        vendor_id
                    )
                    if vendor_row:
                        vendor_name = vendor_row['nama_supplier']

                if not vendor_name:
                    return {
                        "success": False,
                        "message": "Vendor name is required",
                        "data": None
                    }

                # 3. Calculate total amount
                items = request.get('items', [])
                total_amount = 0
                for item in items:
                    qty = Decimal(str(item['quantity']))
                    price = int(item['unit_price'])
                    subtotal = int(qty * price)
                    item['subtotal'] = subtotal
                    total_amount += subtotal

                # 4. Insert bill
                issue_date = request.get('issue_date') or date.today()
                due_date = request['due_date']

                bill_id = await conn.fetchval("""
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
                    request.get('notes'),
                    user_id
                )

                # 5. Insert items
                for idx, item in enumerate(items, start=1):
                    await conn.execute("""
                        INSERT INTO bill_items (
                            bill_id, product_id, description, quantity,
                            unit, unit_price, subtotal, line_number
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                        bill_id,
                        item.get('product_id'),
                        item.get('description'),
                        Decimal(str(item['quantity'])),
                        item.get('unit'),
                        int(item['unit_price']),
                        int(item['subtotal']),
                        idx
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
                        source_id=bill_id
                    )

                    if not ap_result.get('success'):
                        # Rollback by raising exception - transaction will be rolled back
                        raise ValueError(
                            f"AP creation failed: {ap_result.get('error', 'Unknown error')}. "
                            "Bill creation rolled back."
                        )

                    ap_id = ap_result.get('ap_id')
                    journal_id = ap_result.get('journal_id')

                    # Update bill with AP link
                    await conn.execute("""
                        UPDATE bills
                        SET ap_id = $1, journal_id = $2
                        WHERE id = $3
                    """, ap_id, journal_id, bill_id)
                else:
                    # Accounting kernel not available - this is a configuration error
                    logger.error("Accounting kernel not configured - bills require AP integration")
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
                        "created_at": datetime.now().isoformat()
                    }
                }

    # =========================================================================
    # UPDATE BILL
    # =========================================================================
    async def update_bill(
        self,
        tenant_id: str,
        bill_id: UUID,
        request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update a bill. Only allowed if no payments have been made.

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Check if bill exists and is unpaid
            bill = await conn.fetchrow("""
                SELECT id, amount_paid, status
                FROM bills
                WHERE id = $1 AND tenant_id = $2
            """, bill_id, tenant_id)

            if not bill:
                return {
                    "success": False,
                    "message": "Bill not found",
                    "data": None
                }

            if bill['amount_paid'] > 0:
                return {
                    "success": False,
                    "message": "Cannot update bill with payments",
                    "data": None
                }

            if bill['status'] == 'void':
                return {
                    "success": False,
                    "message": "Cannot update voided bill",
                    "data": None
                }

            async with conn.transaction():
                # Update bill fields
                updates = []
                params = []
                param_idx = 1

                if 'invoice_number' in request and request['invoice_number']:
                    updates.append(f"invoice_number = ${param_idx}")
                    params.append(request['invoice_number'])
                    param_idx += 1

                if 'vendor_name' in request and request['vendor_name']:
                    updates.append(f"vendor_name = ${param_idx}")
                    params.append(request['vendor_name'])
                    param_idx += 1

                if 'due_date' in request and request['due_date']:
                    updates.append(f"due_date = ${param_idx}")
                    params.append(request['due_date'])
                    param_idx += 1

                if 'notes' in request:
                    updates.append(f"notes = ${param_idx}")
                    params.append(request['notes'])
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
                if 'items' in request and request['items']:
                    # Delete existing items
                    await conn.execute(
                        "DELETE FROM bill_items WHERE bill_id = $1",
                        bill_id
                    )

                    # Insert new items
                    total_amount = 0
                    for idx, item in enumerate(request['items'], start=1):
                        qty = Decimal(str(item['quantity']))
                        price = int(item['unit_price'])
                        subtotal = int(qty * price)
                        total_amount += subtotal

                        await conn.execute("""
                            INSERT INTO bill_items (
                                bill_id, product_id, description, quantity,
                                unit, unit_price, subtotal, line_number
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                            bill_id,
                            item.get('product_id'),
                            item.get('description'),
                            qty,
                            item.get('unit'),
                            price,
                            subtotal,
                            idx
                        )

                    # Update bill amount
                    await conn.execute("""
                        UPDATE bills SET amount = $1, updated_at = NOW()
                        WHERE id = $2
                    """, total_amount, bill_id)

                return {
                    "success": True,
                    "message": "Bill updated successfully",
                    "data": {
                        "id": str(bill_id),
                        "updated_at": datetime.now().isoformat()
                    }
                }

    # =========================================================================
    # DELETE BILL
    # =========================================================================
    async def delete_bill(
        self,
        tenant_id: str,
        bill_id: UUID
    ) -> Dict[str, Any]:
        """
        Delete a bill. Only allowed if no payments have been made.

        Returns:
            {success: bool, message: str}
        """
        async with self.pool.acquire() as conn:
            # Check if bill exists and is unpaid
            bill = await conn.fetchrow("""
                SELECT id, amount_paid, status
                FROM bills
                WHERE id = $1 AND tenant_id = $2
            """, bill_id, tenant_id)

            if not bill:
                return {
                    "success": False,
                    "message": "Bill not found"
                }

            if bill['amount_paid'] > 0:
                return {
                    "success": False,
                    "message": "Cannot delete bill with payments. Void the bill instead."
                }

            # Delete bill (items and attachments cascade)
            await conn.execute(
                "DELETE FROM bills WHERE id = $1 AND tenant_id = $2",
                bill_id, tenant_id
            )

            return {
                "success": True,
                "message": "Bill deleted successfully"
            }

    # =========================================================================
    # RECORD PAYMENT
    # =========================================================================
    async def record_payment(
        self,
        tenant_id: str,
        bill_id: UUID,
        request: Dict[str, Any],
        user_id: UUID
    ) -> Dict[str, Any]:
        """
        Record a payment for a bill.

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Get bill
            bill = await conn.fetchrow("""
                SELECT id, amount, amount_paid, status, ap_id, vendor_name
                FROM bills
                WHERE id = $1 AND tenant_id = $2
            """, bill_id, tenant_id)

            if not bill:
                return {
                    "success": False,
                    "message": "Bill not found",
                    "data": None
                }

            if bill['status'] == 'void':
                return {
                    "success": False,
                    "message": "Cannot pay voided bill",
                    "data": None
                }

            amount_due = bill['amount'] - bill['amount_paid']
            payment_amount = int(request['amount'])

            if payment_amount > amount_due:
                return {
                    "success": False,
                    "message": f"Payment amount ({payment_amount}) exceeds amount due ({amount_due})",
                    "data": None
                }

            async with conn.transaction():
                # 1. Insert payment record
                payment_date = request.get('payment_date') or date.today()

                payment_id = await conn.fetchval("""
                    INSERT INTO bill_payments (
                        bill_id, amount, payment_date, payment_method,
                        account_id, reference, notes, created_by
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                """,
                    bill_id,
                    payment_amount,
                    payment_date,
                    request['payment_method'],
                    request['account_id'],
                    request.get('reference'),
                    request.get('notes'),
                    user_id
                )

                # Note: Trigger will update bill.amount_paid and status

                # 2. Apply payment to AP (REQUIRED - atomic with payment)
                # Golden Rule: Payment must ALWAYS update AP and create Journal
                if not bill['ap_id']:
                    raise ValueError(
                        f"Bill {bill_id} has no AP record. Cannot record payment. "
                        "Run reconciliation to fix data integrity."
                    )

                if not self.accounting:
                    raise ValueError(
                        "Accounting kernel not available. Payment requires AP integration."
                    )

                ap_result = await self.accounting.apply_ap_payment(
                    tenant_id=tenant_id,
                    ap_id=bill['ap_id'],
                    payment_amount=Decimal(payment_amount),
                    payment_date=payment_date,
                    payment_method=request['payment_method'],
                    account_id=request['account_id']
                )

                if not ap_result.get('success'):
                    # Rollback by raising exception
                    raise ValueError(
                        f"AP payment failed: {ap_result.get('error', 'Unknown error')}. "
                        "Payment rolled back."
                    )

                journal_id = ap_result.get('journal_id')

                # Update payment with journal link
                await conn.execute("""
                    UPDATE bill_payments
                    SET journal_id = $1
                    WHERE id = $2
                """, journal_id, payment_id)

                # Get updated bill status
                updated_bill = await conn.fetchrow("""
                    SELECT amount, amount_paid, status FROM bills WHERE id = $1
                """, bill_id)

                new_amount_due = updated_bill['amount'] - updated_bill['amount_paid']

                return {
                    "success": True,
                    "message": "Payment recorded successfully",
                    "data": {
                        "id": str(payment_id),
                        "bill_id": str(bill_id),
                        "amount": payment_amount,
                        "bill_status": updated_bill['status'],
                        "amount_due": new_amount_due
                    }
                }

    # =========================================================================
    # MARK AS PAID
    # =========================================================================
    async def mark_paid(
        self,
        tenant_id: str,
        bill_id: UUID,
        request: Dict[str, Any],
        user_id: UUID
    ) -> Dict[str, Any]:
        """
        Mark a bill as fully paid (pay the remaining balance).

        Returns:
            {success: bool, message: str, data: {...}}
        """
        async with self.pool.acquire() as conn:
            # Get bill
            bill = await conn.fetchrow("""
                SELECT id, amount, amount_paid, status
                FROM bills
                WHERE id = $1 AND tenant_id = $2
            """, bill_id, tenant_id)

            if not bill:
                return {
                    "success": False,
                    "message": "Bill not found",
                    "data": None
                }

            if bill['status'] == 'void':
                return {
                    "success": False,
                    "message": "Cannot pay voided bill",
                    "data": None
                }

            if bill['status'] == 'paid':
                return {
                    "success": False,
                    "message": "Bill is already paid",
                    "data": None
                }

            amount_due = bill['amount'] - bill['amount_paid']

            # Create payment for remaining amount
            payment_request = {
                'amount': amount_due,
                'payment_method': request['payment_method'],
                'account_id': request['account_id'],
                'reference': request.get('reference'),
                'notes': request.get('notes', 'Full payment')
            }

            return await self.record_payment(tenant_id, bill_id, payment_request, user_id)

    # =========================================================================
    # VOID BILL
    # =========================================================================
    async def void_bill(
        self,
        tenant_id: str,
        bill_id: UUID,
        request: Dict[str, Any],
        user_id: UUID
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
            bill = await conn.fetchrow("""
                SELECT id, status, journal_id, ap_id, amount_paid
                FROM bills
                WHERE id = $1 AND tenant_id = $2
            """, bill_id, tenant_id)

            if not bill:
                return {
                    "success": False,
                    "message": "Bill not found",
                    "data": None
                }

            if bill['status'] == 'void':
                return {
                    "success": False,
                    "message": "Bill is already voided",
                    "data": None
                }

            # Block void if payments exist
            if bill['amount_paid'] > 0:
                return {
                    "success": False,
                    "message": "Cannot void bill with payments. Refund the payments first.",
                    "data": None
                }

            async with conn.transaction():
                reason = request.get('reason', 'Voided')

                # 1. Void AP and create reversal journal (REQUIRED - atomic)
                # Must void AP before updating bill status
                if not bill['ap_id']:
                    logger.warning(f"Bill {bill_id} has no AP record - data integrity issue")
                    # Allow void for data cleanup, but log warning
                elif not self.accounting:
                    raise ValueError(
                        "Accounting kernel not available. Void requires AP integration."
                    )
                else:
                    ap_result = await self.accounting.void_payable(
                        tenant_id=tenant_id,
                        ap_id=bill['ap_id'],
                        void_reason=reason,
                        voided_by=user_id
                    )

                    if not ap_result.get('success'):
                        # Rollback by raising exception
                        raise ValueError(
                            f"AP void failed: {ap_result.get('error', 'Unknown error')}. "
                            "Void rolled back."
                        )

                # 2. Update bill status
                await conn.execute("""
                    UPDATE bills
                    SET status = 'void',
                        voided_at = NOW(),
                        voided_reason = $1,
                        updated_at = NOW()
                    WHERE id = $2
                """, reason, bill_id)

                return {
                    "success": True,
                    "message": "Bill voided successfully",
                    "data": {
                        "id": str(bill_id),
                        "status": "void",
                        "voided_at": datetime.now().isoformat(),
                        "voided_reason": reason
                    }
                }

    # =========================================================================
    # GET SUMMARY
    # =========================================================================
    async def get_summary(
        self,
        tenant_id: str,
        period: str = "current_month"
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
                    year, month = map(int, period.split('-'))
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
            query = """
                SELECT
                    COUNT(*) as total_count,
                    COALESCE(SUM(amount), 0) as total_amount,
                    COUNT(DISTINCT vendor_name) as vendor_count,
                    COUNT(*) FILTER (WHERE amount_paid >= amount AND status != 'void') as paid_count,
                    COALESCE(SUM(amount) FILTER (WHERE amount_paid >= amount AND status != 'void'), 0) as paid_amount,
                    COUNT(*) FILTER (WHERE amount_paid > 0 AND amount_paid < amount AND status != 'void') as partial_count,
                    COALESCE(SUM(amount) FILTER (WHERE amount_paid > 0 AND amount_paid < amount AND status != 'void'), 0) as partial_amount,
                    COUNT(*) FILTER (WHERE amount_paid = 0 AND due_date >= CURRENT_DATE AND status != 'void') as unpaid_count,
                    COALESCE(SUM(amount) FILTER (WHERE amount_paid = 0 AND due_date >= CURRENT_DATE AND status != 'void'), 0) as unpaid_amount,
                    COUNT(*) FILTER (WHERE amount_paid < amount AND due_date < CURRENT_DATE AND status != 'void') as overdue_count,
                    COALESCE(SUM(amount) FILTER (WHERE amount_paid < amount AND due_date < CURRENT_DATE AND status != 'void'), 0) as overdue_amount
                FROM bills
                WHERE tenant_id = $1
                    AND issue_date >= $2
                    AND issue_date < $3
                    AND status != 'void'
            """

            row = await conn.fetchrow(query, tenant_id, start_date, end_date)

            total_amount = int(row['total_amount'])

            def calc_percentage(amount):
                if total_amount == 0:
                    return 0
                return round((amount / total_amount) * 100, 1)

            return {
                "success": True,
                "data": {
                    "period": period,
                    "period_label": period_label,
                    "total_amount": total_amount,
                    "total_count": row['total_count'],
                    "vendor_count": row['vendor_count'],
                    "breakdown": {
                        "paid": {
                            "count": row['paid_count'],
                            "amount": int(row['paid_amount']),
                            "percentage": calc_percentage(row['paid_amount'])
                        },
                        "partial": {
                            "count": row['partial_count'],
                            "amount": int(row['partial_amount']),
                            "percentage": calc_percentage(row['partial_amount'])
                        },
                        "unpaid": {
                            "count": row['unpaid_count'],
                            "amount": int(row['unpaid_amount']),
                            "percentage": calc_percentage(row['unpaid_amount'])
                        },
                        "overdue": {
                            "count": row['overdue_count'],
                            "amount": int(row['overdue_amount']),
                            "percentage": calc_percentage(row['overdue_amount'])
                        }
                    }
                }
            }
