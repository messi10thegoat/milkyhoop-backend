"""
Accounts Payable Service
========================

Manages accounts payable (money we owe to suppliers):
- Credit purchases tracking
- Payment applications
- Aging reports
- Supplier statements
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import asyncpg

from ..constants import ARAPStatus, SourceType
from ..models.ap import AccountPayable, APPaymentApplication
from ..models.journal import CreateJournalRequest, JournalLineInput
from ..config import settings


@dataclass
class APAgingRow:
    """Single row in AP Aging report"""
    supplier_id: Optional[UUID]
    supplier_name: str
    current: Decimal = Decimal("0")
    days_1_30: Decimal = Decimal("0")
    days_31_60: Decimal = Decimal("0")
    days_61_90: Decimal = Decimal("0")
    days_over_90: Decimal = Decimal("0")
    total: Decimal = Decimal("0")


@dataclass
class APAgingReport:
    """AP Aging Report"""
    tenant_id: str
    as_of_date: date
    rows: List[APAgingRow] = field(default_factory=list)
    totals: APAgingRow = field(default_factory=lambda: APAgingRow(None, "TOTAL"))
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SupplierStatement:
    """Supplier statement showing AP activity"""
    tenant_id: str
    supplier_id: UUID
    supplier_name: str
    start_date: date
    end_date: date
    opening_balance: Decimal = Decimal("0")
    bills: List[Dict[str, Any]] = field(default_factory=list)
    payments: List[Dict[str, Any]] = field(default_factory=list)
    closing_balance: Decimal = Decimal("0")


class APService:
    """
    Accounts Payable Service.

    Manages:
    - Recording bills/credit purchases
    - Applying payments to suppliers
    - Aging reports
    - Supplier statements
    """

    def __init__(self, pool: asyncpg.Pool, journal_service=None):
        self.pool = pool
        self.journal_service = journal_service

    async def create_payable(
        self,
        tenant_id: str,
        supplier_id: Optional[UUID],
        supplier_name: str,
        bill_number: str,
        bill_date: date,
        due_date: date,
        amount: Decimal,
        description: str,
        source_type: SourceType = SourceType.BILL,
        source_id: Optional[UUID] = None,
        currency: str = "IDR"
    ) -> AccountPayable:
        """
        Create a new accounts payable record.

        Args:
            tenant_id: Tenant UUID
            supplier_id: Supplier UUID (optional)
            supplier_name: Supplier name for display
            bill_number: Bill/invoice number from supplier
            bill_date: Bill date
            due_date: Payment due date
            amount: Bill amount
            description: Description
            source_type: Source document type
            source_id: Source document UUID
            currency: Currency code (default IDR)

        Returns:
            Created AccountPayable
        """
        ap_id = uuid4()

        query = """
            INSERT INTO accounts_payable (
                id, tenant_id, supplier_id, supplier_name,
                bill_number, bill_date, due_date,
                amount, amount_paid, status,
                description, source_type, source_id, currency
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0, 'OPEN', $9, $10, $11, $12)
            RETURNING *
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            row = await conn.fetchrow(
                query,
                ap_id,
                tenant_id,
                supplier_id,
                supplier_name,
                bill_number,
                bill_date,
                due_date,
                amount,
                description,
                source_type.value,
                source_id,
                currency
            )

        return AccountPayable(
            id=row['id'],
            tenant_id=row['tenant_id'],
            supplier_id=row['supplier_id'],
            supplier_name=row['supplier_name'],
            source_number=row['bill_number'],
            issue_date=row['bill_date'],
            due_date=row['due_date'],
            amount=Decimal(str(row['amount'])),
            balance=Decimal(str(row['amount'])) - Decimal(str(row['amount_paid'])),
            status=ARAPStatus(row['status']),
            source_type=SourceType(row['source_type']),
            source_id=row['source_id'],
            currency=row['currency'],
            created_at=row['created_at']
        )

    async def apply_payment(
        self,
        tenant_id: str,
        ap_id: UUID,
        payment_date: date,
        amount: Decimal,
        payment_method: str,
        reference_number: Optional[str] = None,
        notes: Optional[str] = None,
        create_journal: bool = True
    ) -> APPaymentApplication:
        """
        Apply a payment to an AP record (pay supplier).

        Args:
            tenant_id: Tenant UUID
            ap_id: AP record UUID
            payment_date: Date of payment
            amount: Payment amount
            payment_method: Payment method (cash, bank, etc.)
            reference_number: Payment reference
            notes: Additional notes
            create_journal: Create journal entry for payment

        Returns:
            Created APPaymentApplication
        """
        application_id = uuid4()

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Get AP record
            ap_row = await conn.fetchrow(
                "SELECT * FROM accounts_payable WHERE id = $1 AND tenant_id = $2",
                ap_id, tenant_id
            )

            if not ap_row:
                raise ValueError(f"AP record not found: {ap_id}")

            ap_amount = Decimal(str(ap_row['amount']))
            ap_paid = Decimal(str(ap_row['amount_paid']))
            remaining = ap_amount - ap_paid

            if amount > remaining:
                raise ValueError(
                    f"Payment amount {amount} exceeds remaining balance {remaining}"
                )

            # Begin transaction
            async with conn.transaction():
                # Create payment application
                app_row = await conn.fetchrow(
                    """
                    INSERT INTO ap_payment_applications (
                        id, tenant_id, ap_id, payment_date,
                        amount_applied, payment_method,
                        reference_number, notes
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING *
                    """,
                    application_id,
                    tenant_id,
                    ap_id,
                    payment_date,
                    amount,
                    payment_method,
                    reference_number,
                    notes
                )

                # Update AP record
                new_paid = ap_paid + amount
                new_status = ARAPStatus.OPEN.value
                if new_paid >= ap_amount:
                    new_status = ARAPStatus.PAID.value
                elif new_paid > 0:
                    new_status = ARAPStatus.PARTIAL.value

                await conn.execute(
                    """
                    UPDATE accounts_payable
                    SET amount_paid = $1, status = $2, updated_at = NOW()
                    WHERE id = $3
                    """,
                    new_paid,
                    new_status,
                    ap_id
                )

                # Create journal entry if requested
                journal_id = None
                if create_journal and self.journal_service:
                    # Debit AP, Credit Cash/Bank
                    account_config = settings.accounting

                    # Determine cash/bank account based on payment method
                    if payment_method.lower() in ['cash', 'tunai']:
                        credit_account = account_config.CASH_ACCOUNT
                    else:
                        credit_account = account_config.BANK_ACCOUNT

                    journal_request = CreateJournalRequest(
                        tenant_id=tenant_id,
                        journal_date=payment_date,
                        description=f"Payment to supplier: {ap_row['bill_number']} - {ap_row['supplier_name']}",
                        source_type=SourceType.PAYMENT_BILL.value,
                        source_id=application_id,
                        lines=[
                            JournalLineInput(
                                account_code=account_config.AP_ACCOUNT,
                                debit=amount,
                                credit=Decimal("0"),
                                memo=f"Bill: {ap_row['bill_number']}"
                            ),
                            JournalLineInput(
                                account_code=credit_account,
                                debit=Decimal("0"),
                                credit=amount,
                                memo=f"Payment to {ap_row['supplier_name']}"
                            )
                        ]
                    )

                    journal_response = await self.journal_service.create_journal(
                        journal_request
                    )
                    journal_id = journal_response.id

        return APPaymentApplication(
            id=app_row['id'],
            tenant_id=app_row['tenant_id'],
            ap_id=app_row['ap_id'],
            payment_date=app_row['payment_date'],
            amount_applied=Decimal(str(app_row['amount_applied'])),
            payment_method=app_row['payment_method'],
            reference_number=app_row['reference_number'],
            notes=app_row['notes'],
            journal_id=journal_id,
            created_at=app_row['created_at']
        )

    async def get_payable(
        self,
        tenant_id: str,
        ap_id: UUID
    ) -> Optional[AccountPayable]:
        """Get a single AP record by ID."""
        query = """
            SELECT * FROM accounts_payable
            WHERE id = $1 AND tenant_id = $2
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )
            row = await conn.fetchrow(query, ap_id, tenant_id)

        if not row:
            return None

        return AccountPayable(
            id=row['id'],
            tenant_id=row['tenant_id'],
            supplier_id=row['supplier_id'],
            supplier_name=row['supplier_name'],
            source_number=row['bill_number'],
            issue_date=row['bill_date'],
            due_date=row['due_date'],
            amount=Decimal(str(row['amount'])),
            balance=Decimal(str(row['amount'])) - Decimal(str(row['amount_paid'])),
            status=ARAPStatus(row['status']),
            source_type=SourceType(row['source_type']),
            source_id=row['source_id'],
            currency=row['currency'],
            created_at=row['created_at']
        )

    async def list_payables(
        self,
        tenant_id: str,
        status: Optional[ARAPStatus] = None,
        supplier_id: Optional[UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[AccountPayable]:
        """List AP records with optional filters."""
        query = """
            SELECT * FROM accounts_payable
            WHERE tenant_id = $1
                AND ($2::text IS NULL OR status = $2)
                AND ($3::uuid IS NULL OR supplier_id = $3)
                AND ($4::date IS NULL OR bill_date >= $4)
                AND ($5::date IS NULL OR bill_date <= $5)
            ORDER BY bill_date DESC, bill_number
            LIMIT $6 OFFSET $7
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(
                query,
                tenant_id,
                status.value if status else None,
                supplier_id,
                start_date,
                end_date,
                limit,
                offset
            )

        return [
            AccountPayable(
                id=row['id'],
                tenant_id=row['tenant_id'],
                supplier_id=row['supplier_id'],
                supplier_name=row['supplier_name'],
                bill_number=row['bill_number'],
                bill_date=row['bill_date'],
                due_date=row['due_date'],
                amount=Decimal(str(row['amount'])),
                amount_paid=Decimal(str(row['amount_paid'])),
                status=ARAPStatus(row['status']),
                description=row['description'],
                source_type=row['source_type'],
                source_id=row['source_id'],
                currency=row['currency'],
                created_at=row['created_at']
            )
            for row in rows
        ]

    async def get_aging_report(
        self,
        tenant_id: str,
        as_of_date: Optional[date] = None,
        supplier_id: Optional[UUID] = None
    ) -> APAgingReport:
        """
        Generate AP Aging Report.

        Args:
            tenant_id: Tenant UUID
            as_of_date: Date for aging calculation (default: today)
            supplier_id: Filter by specific supplier

        Returns:
            APAgingReport with aging buckets
        """
        if as_of_date is None:
            as_of_date = date.today()

        query = """
            SELECT
                supplier_id,
                supplier_name,
                bill_number,
                bill_date,
                due_date,
                amount,
                amount_paid,
                (amount - amount_paid) as outstanding
            FROM accounts_payable
            WHERE tenant_id = $1
                AND status IN ('OPEN', 'PARTIAL')
                AND ($2::uuid IS NULL OR supplier_id = $2)
            ORDER BY supplier_name, due_date
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(query, tenant_id, supplier_id)

        # Group by supplier and calculate aging
        supplier_aging: Dict[str, APAgingRow] = {}

        for row in rows:
            supplier_name = row['supplier_name']
            outstanding = Decimal(str(row['outstanding']))
            days_past_due = (as_of_date - row['due_date']).days

            if supplier_name not in supplier_aging:
                supplier_aging[supplier_name] = APAgingRow(
                    supplier_id=row['supplier_id'],
                    supplier_name=supplier_name
                )

            aging_row = supplier_aging[supplier_name]

            # Assign to appropriate bucket
            if days_past_due <= 0:
                aging_row.current += outstanding
            elif days_past_due <= 30:
                aging_row.days_1_30 += outstanding
            elif days_past_due <= 60:
                aging_row.days_31_60 += outstanding
            elif days_past_due <= 90:
                aging_row.days_61_90 += outstanding
            else:
                aging_row.days_over_90 += outstanding

            aging_row.total += outstanding

        # Build report
        report = APAgingReport(
            tenant_id=tenant_id,
            as_of_date=as_of_date
        )

        for aging_row in supplier_aging.values():
            report.rows.append(aging_row)
            report.totals.current += aging_row.current
            report.totals.days_1_30 += aging_row.days_1_30
            report.totals.days_31_60 += aging_row.days_31_60
            report.totals.days_61_90 += aging_row.days_61_90
            report.totals.days_over_90 += aging_row.days_over_90
            report.totals.total += aging_row.total

        return report

    async def get_supplier_statement(
        self,
        tenant_id: str,
        supplier_id: UUID,
        start_date: date,
        end_date: date
    ) -> SupplierStatement:
        """
        Generate supplier statement showing AP activity.

        Args:
            tenant_id: Tenant UUID
            supplier_id: Supplier UUID
            start_date: Statement start date
            end_date: Statement end date

        Returns:
            SupplierStatement with activity details
        """
        # Get supplier name and opening balance
        opening_query = """
            SELECT
                supplier_name,
                COALESCE(SUM(amount - amount_paid), 0) as opening
            FROM accounts_payable
            WHERE tenant_id = $1
                AND supplier_id = $2
                AND bill_date < $3
                AND status != 'VOID'
            GROUP BY supplier_name
        """

        # Get bills in period
        bills_query = """
            SELECT
                bill_number,
                bill_date,
                due_date,
                description,
                amount,
                status
            FROM accounts_payable
            WHERE tenant_id = $1
                AND supplier_id = $2
                AND bill_date BETWEEN $3 AND $4
                AND status != 'VOID'
            ORDER BY bill_date
        """

        # Get payments in period
        payments_query = """
            SELECT
                p.payment_date,
                p.amount_applied,
                p.payment_method,
                p.reference_number,
                ap.bill_number
            FROM ap_payment_applications p
            JOIN accounts_payable ap ON ap.id = p.ap_id
            WHERE p.tenant_id = $1
                AND ap.supplier_id = $2
                AND p.payment_date BETWEEN $3 AND $4
            ORDER BY p.payment_date
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Get opening balance
            opening_row = await conn.fetchrow(
                opening_query,
                tenant_id,
                supplier_id,
                start_date
            )

            supplier_name = opening_row['supplier_name'] if opening_row else "Unknown"
            opening_balance = Decimal(str(opening_row['opening'])) if opening_row else Decimal("0")

            # Get bills
            bill_rows = await conn.fetch(
                bills_query,
                tenant_id,
                supplier_id,
                start_date,
                end_date
            )

            # Get payments
            payment_rows = await conn.fetch(
                payments_query,
                tenant_id,
                supplier_id,
                start_date,
                end_date
            )

        # Build statement
        statement = SupplierStatement(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            start_date=start_date,
            end_date=end_date,
            opening_balance=opening_balance
        )

        total_bills = Decimal("0")
        for row in bill_rows:
            bill_amount = Decimal(str(row['amount']))
            statement.bills.append({
                "bill_number": row['bill_number'],
                "bill_date": row['bill_date'],
                "due_date": row['due_date'],
                "description": row['description'],
                "amount": bill_amount,
                "status": row['status']
            })
            total_bills += bill_amount

        total_payments = Decimal("0")
        for row in payment_rows:
            payment_amount = Decimal(str(row['amount_applied']))
            statement.payments.append({
                "payment_date": row['payment_date'],
                "amount": payment_amount,
                "payment_method": row['payment_method'],
                "reference_number": row['reference_number'],
                "bill_number": row['bill_number']
            })
            total_payments += payment_amount

        statement.closing_balance = opening_balance + total_bills - total_payments

        return statement

    async def void_payable(
        self,
        tenant_id: str,
        ap_id: UUID,
        reason: str
    ) -> bool:
        """
        Void an AP record (cannot void if payments applied).

        Args:
            tenant_id: Tenant UUID
            ap_id: AP record UUID
            reason: Reason for voiding

        Returns:
            True if voided successfully
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Check if payments exist
            payment_count = await conn.fetchval(
                "SELECT COUNT(*) FROM ap_payment_applications WHERE ap_id = $1",
                ap_id
            )

            if payment_count > 0:
                raise ValueError(
                    "Cannot void AP with payments applied. Reverse payments first."
                )

            # Void the record
            result = await conn.execute(
                """
                UPDATE accounts_payable
                SET status = 'VOID', updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2 AND status != 'VOID'
                """,
                ap_id,
                tenant_id
            )

            return result == "UPDATE 1"

    async def get_total_outstanding(
        self,
        tenant_id: str,
        supplier_id: Optional[UUID] = None
    ) -> Decimal:
        """Get total outstanding AP balance."""
        query = """
            SELECT COALESCE(SUM(amount - amount_paid), 0) as total
            FROM accounts_payable
            WHERE tenant_id = $1
                AND status IN ('OPEN', 'PARTIAL')
                AND ($2::uuid IS NULL OR supplier_id = $2)
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            result = await conn.fetchval(query, tenant_id, supplier_id)

        return Decimal(str(result)) if result else Decimal("0")

    async def get_due_soon(
        self,
        tenant_id: str,
        days: int = 7
    ) -> List[AccountPayable]:
        """
        Get AP records due within specified days.

        Args:
            tenant_id: Tenant UUID
            days: Number of days to look ahead

        Returns:
            List of AP records due soon
        """
        query = """
            SELECT * FROM accounts_payable
            WHERE tenant_id = $1
                AND status IN ('OPEN', 'PARTIAL')
                AND due_date <= CURRENT_DATE + $2
            ORDER BY due_date
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(query, tenant_id, days)

        return [
            AccountPayable(
                id=row['id'],
                tenant_id=row['tenant_id'],
                supplier_id=row['supplier_id'],
                supplier_name=row['supplier_name'],
                bill_number=row['bill_number'],
                bill_date=row['bill_date'],
                due_date=row['due_date'],
                amount=Decimal(str(row['amount'])),
                amount_paid=Decimal(str(row['amount_paid'])),
                status=ARAPStatus(row['status']),
                description=row['description'],
                source_type=row['source_type'],
                source_id=row['source_id'],
                currency=row['currency'],
                created_at=row['created_at']
            )
            for row in rows
        ]
