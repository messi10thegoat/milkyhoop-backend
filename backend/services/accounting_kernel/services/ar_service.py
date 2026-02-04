"""
Accounts Receivable Service
===========================

Manages accounts receivable (money owed to us by customers):
- Credit sales tracking
- Payment applications
- Aging reports
- Customer statements
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from uuid import UUID, uuid4
from enum import Enum

import asyncpg

from ..constants import ARAPStatus, AgingBucket, SourceType, AccountType
from ..models.ar import AccountReceivable, ARPaymentApplication
from ..models.journal import CreateJournalRequest, JournalLineInput
from ..config import settings


@dataclass
class ARAgingRow:
    """Single row in AR Aging report"""
    customer_id: Optional[UUID]
    customer_name: str
    current: Decimal = Decimal("0")
    days_1_30: Decimal = Decimal("0")
    days_31_60: Decimal = Decimal("0")
    days_61_90: Decimal = Decimal("0")
    days_over_90: Decimal = Decimal("0")
    total: Decimal = Decimal("0")


@dataclass
class ARAgingReport:
    """AR Aging Report"""
    tenant_id: str
    as_of_date: date
    rows: List[ARAgingRow] = field(default_factory=list)
    totals: ARAgingRow = field(default_factory=lambda: ARAgingRow(None, "TOTAL"))
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CustomerStatement:
    """Customer statement showing AR activity"""
    tenant_id: str
    customer_id: UUID
    customer_name: str
    start_date: date
    end_date: date
    opening_balance: Decimal = Decimal("0")
    invoices: List[Dict[str, Any]] = field(default_factory=list)
    payments: List[Dict[str, Any]] = field(default_factory=list)
    closing_balance: Decimal = Decimal("0")


class ARService:
    """
    Accounts Receivable Service.

    Manages:
    - Recording invoices/credit sales
    - Applying customer payments
    - Aging reports
    - Customer statements
    """

    def __init__(self, pool: asyncpg.Pool, journal_service=None):
        self.pool = pool
        self.journal_service = journal_service

    async def create_receivable(
        self,
        tenant_id: str,
        customer_id: Optional[UUID],
        customer_name: str,
        invoice_number: str,
        invoice_date: date,
        due_date: date,
        amount: Decimal,
        description: str,
        source_type: SourceType = SourceType.INVOICE,
        source_id: Optional[UUID] = None,
        currency: str = "IDR"
    ) -> AccountReceivable:
        """
        Create a new accounts receivable record.

        Args:
            tenant_id: Tenant UUID
            customer_id: Customer UUID (optional)
            customer_name: Customer name for display
            invoice_number: Invoice/reference number
            invoice_date: Invoice date
            due_date: Payment due date
            amount: Invoice amount
            description: Description
            source_type: Source document type
            source_id: Source document UUID
            currency: Currency code (default IDR)

        Returns:
            Created AccountReceivable
        """
        ar_id = uuid4()

        query = """
            INSERT INTO accounts_receivable (
                id, tenant_id, customer_id, customer_name,
                invoice_number, invoice_date, due_date,
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
                ar_id,
                tenant_id,
                customer_id,
                customer_name,
                invoice_number,
                invoice_date,
                due_date,
                amount,
                description,
                source_type.value,
                source_id,
                currency
            )

        return AccountReceivable(
            id=row['id'],
            tenant_id=row['tenant_id'],
            customer_id=row['customer_id'],
            customer_name=row['customer_name'],
            invoice_number=row['invoice_number'],
            invoice_date=row['invoice_date'],
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

    async def apply_payment(
        self,
        tenant_id: str,
        ar_id: UUID,
        payment_date: date,
        amount: Decimal,
        payment_method: str,
        reference_number: Optional[str] = None,
        notes: Optional[str] = None,
        create_journal: bool = True
    ) -> ARPaymentApplication:
        """
        Apply a payment to an AR record.

        Args:
            tenant_id: Tenant UUID
            ar_id: AR record UUID
            payment_date: Date of payment
            amount: Payment amount
            payment_method: Payment method (cash, bank, etc.)
            reference_number: Payment reference
            notes: Additional notes
            create_journal: Create journal entry for payment

        Returns:
            Created ARPaymentApplication
        """
        application_id = uuid4()

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            # Get AR record
            ar_row = await conn.fetchrow(
                "SELECT * FROM accounts_receivable WHERE id = $1 AND tenant_id = $2",
                ar_id, tenant_id
            )

            if not ar_row:
                raise ValueError(f"AR record not found: {ar_id}")

            ar_amount = Decimal(str(ar_row['amount']))
            ar_paid = Decimal(str(ar_row['amount_paid']))
            remaining = ar_amount - ar_paid

            if amount > remaining:
                raise ValueError(
                    f"Payment amount {amount} exceeds remaining balance {remaining}"
                )

            # Begin transaction
            async with conn.transaction():
                # Create payment application
                app_row = await conn.fetchrow(
                    """
                    INSERT INTO ar_payment_applications (
                        id, tenant_id, ar_id, payment_date,
                        amount_applied, payment_method,
                        reference_number, notes
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING *
                    """,
                    application_id,
                    tenant_id,
                    ar_id,
                    payment_date,
                    amount,
                    payment_method,
                    reference_number,
                    notes
                )

                # Update AR record
                new_paid = ar_paid + amount
                new_status = ARAPStatus.OPEN.value
                if new_paid >= ar_amount:
                    new_status = ARAPStatus.PAID.value
                elif new_paid > 0:
                    new_status = ARAPStatus.PARTIAL.value

                await conn.execute(
                    """
                    UPDATE accounts_receivable
                    SET amount_paid = $1, status = $2, updated_at = NOW()
                    WHERE id = $3
                    """,
                    new_paid,
                    new_status,
                    ar_id
                )

                # Create journal entry if requested
                journal_id = None
                if create_journal and self.journal_service:
                    # Debit Cash/Bank, Credit AR
                    account_config = settings.accounting

                    # Determine cash/bank account based on payment method
                    if payment_method.lower() in ['cash', 'tunai']:
                        debit_account = account_config.CASH_ACCOUNT
                    else:
                        debit_account = account_config.BANK_ACCOUNT

                    journal_request = CreateJournalRequest(
                        tenant_id=tenant_id,
                        journal_date=payment_date,
                        description=f"Payment received: {ar_row['invoice_number']} - {ar_row['customer_name']}",
                        source_type=SourceType.PAYMENT_RECEIVED,
                        source_id=application_id,
                        lines=[
                            JournalLineInput(
                                account_code=debit_account,
                                debit=amount,
                                credit=Decimal("0"),
                                memo=f"Payment from {ar_row['customer_name']}"
                            ),
                            JournalLineInput(
                                account_code=account_config.AR_ACCOUNT,
                                debit=Decimal("0"),
                                credit=amount,
                                memo=f"Inv: {ar_row['invoice_number']}"
                            )
                        ]
                    )

                    journal_response = await self.journal_service.create_journal(
                        journal_request
                    )
                    journal_id = journal_response.journal_id

        return ARPaymentApplication(
            id=app_row['id'],
            tenant_id=app_row['tenant_id'],
            ar_id=app_row['ar_id'],
            payment_date=app_row['payment_date'],
            amount_applied=Decimal(str(app_row['amount_applied'])),
            payment_method=app_row['payment_method'],
            reference_number=app_row['reference_number'],
            notes=app_row['notes'],
            journal_id=journal_id,
            created_at=app_row['created_at']
        )

    async def get_receivable(
        self,
        tenant_id: str,
        ar_id: UUID
    ) -> Optional[AccountReceivable]:
        """Get a single AR record by ID."""
        query = """
            SELECT * FROM accounts_receivable
            WHERE id = $1 AND tenant_id = $2
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )
            row = await conn.fetchrow(query, ar_id, tenant_id)

        if not row:
            return None

        return AccountReceivable(
            id=row['id'],
            tenant_id=row['tenant_id'],
            customer_id=row['customer_id'],
            customer_name=row['customer_name'],
            invoice_number=row['invoice_number'],
            invoice_date=row['invoice_date'],
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

    async def list_receivables(
        self,
        tenant_id: str,
        status: Optional[ARAPStatus] = None,
        customer_id: Optional[UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[AccountReceivable]:
        """List AR records with optional filters."""
        query = """
            SELECT * FROM accounts_receivable
            WHERE tenant_id = $1
                AND ($2::text IS NULL OR status = $2)
                AND ($3::uuid IS NULL OR customer_id = $3)
                AND ($4::date IS NULL OR invoice_date >= $4)
                AND ($5::date IS NULL OR invoice_date <= $5)
            ORDER BY invoice_date DESC, invoice_number
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
                customer_id,
                start_date,
                end_date,
                limit,
                offset
            )

        return [
            AccountReceivable(
                id=row['id'],
                tenant_id=row['tenant_id'],
                customer_id=row['customer_id'],
                customer_name=row['customer_name'],
                invoice_number=row['invoice_number'],
                invoice_date=row['invoice_date'],
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
        customer_id: Optional[UUID] = None
    ) -> ARAgingReport:
        """
        Generate AR Aging Report.

        Args:
            tenant_id: Tenant UUID
            as_of_date: Date for aging calculation (default: today)
            customer_id: Filter by specific customer

        Returns:
            ARAgingReport with aging buckets
        """
        if as_of_date is None:
            as_of_date = date.today()

        query = """
            SELECT
                customer_id,
                customer_name,
                invoice_number,
                invoice_date,
                due_date,
                amount,
                amount_paid,
                (amount - amount_paid) as outstanding
            FROM accounts_receivable
            WHERE tenant_id = $1
                AND status IN ('OPEN', 'PARTIAL')
                AND ($2::uuid IS NULL OR customer_id = $2)
            ORDER BY customer_name, due_date
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            rows = await conn.fetch(query, tenant_id, customer_id)

        # Group by customer and calculate aging
        customer_aging: Dict[str, ARAgingRow] = {}

        for row in rows:
            cust_name = row['customer_name']
            outstanding = Decimal(str(row['outstanding']))
            days_past_due = (as_of_date - row['due_date']).days

            if cust_name not in customer_aging:
                customer_aging[cust_name] = ARAgingRow(
                    customer_id=row['customer_id'],
                    customer_name=cust_name
                )

            aging_row = customer_aging[cust_name]

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
        report = ARAgingReport(
            tenant_id=tenant_id,
            as_of_date=as_of_date
        )

        for aging_row in customer_aging.values():
            report.rows.append(aging_row)
            report.totals.current += aging_row.current
            report.totals.days_1_30 += aging_row.days_1_30
            report.totals.days_31_60 += aging_row.days_31_60
            report.totals.days_61_90 += aging_row.days_61_90
            report.totals.days_over_90 += aging_row.days_over_90
            report.totals.total += aging_row.total

        return report

    async def get_customer_statement(
        self,
        tenant_id: str,
        customer_id: UUID,
        start_date: date,
        end_date: date
    ) -> CustomerStatement:
        """
        Generate customer statement showing AR activity.

        Args:
            tenant_id: Tenant UUID
            customer_id: Customer UUID
            start_date: Statement start date
            end_date: Statement end date

        Returns:
            CustomerStatement with activity details
        """
        # Get customer name and opening balance
        opening_query = """
            SELECT
                customer_name,
                COALESCE(SUM(amount - amount_paid), 0) as opening
            FROM accounts_receivable
            WHERE tenant_id = $1
                AND customer_id = $2
                AND invoice_date < $3
                AND status != 'VOID'
            GROUP BY customer_name
        """

        # Get invoices in period
        invoices_query = """
            SELECT
                invoice_number,
                invoice_date,
                due_date,
                description,
                amount,
                status
            FROM accounts_receivable
            WHERE tenant_id = $1
                AND customer_id = $2
                AND invoice_date BETWEEN $3 AND $4
                AND status != 'VOID'
            ORDER BY invoice_date
        """

        # Get payments in period
        payments_query = """
            SELECT
                p.payment_date,
                p.amount_applied,
                p.payment_method,
                p.reference_number,
                ar.invoice_number
            FROM ar_payment_applications p
            JOIN accounts_receivable ar ON ar.id = p.ar_id
            WHERE p.tenant_id = $1
                AND ar.customer_id = $2
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
                customer_id,
                start_date
            )

            customer_name = opening_row['customer_name'] if opening_row else "Unknown"
            opening_balance = Decimal(str(opening_row['opening'])) if opening_row else Decimal("0")

            # Get invoices
            invoice_rows = await conn.fetch(
                invoices_query,
                tenant_id,
                customer_id,
                start_date,
                end_date
            )

            # Get payments
            payment_rows = await conn.fetch(
                payments_query,
                tenant_id,
                customer_id,
                start_date,
                end_date
            )

        # Build statement
        statement = CustomerStatement(
            tenant_id=tenant_id,
            customer_id=customer_id,
            customer_name=customer_name,
            start_date=start_date,
            end_date=end_date,
            opening_balance=opening_balance
        )

        total_invoices = Decimal("0")
        for row in invoice_rows:
            invoice_amount = Decimal(str(row['amount']))
            statement.invoices.append({
                "invoice_number": row['invoice_number'],
                "invoice_date": row['invoice_date'],
                "due_date": row['due_date'],
                "description": row['description'],
                "amount": invoice_amount,
                "status": row['status']
            })
            total_invoices += invoice_amount

        total_payments = Decimal("0")
        for row in payment_rows:
            payment_amount = Decimal(str(row['amount_applied']))
            statement.payments.append({
                "payment_date": row['payment_date'],
                "amount": payment_amount,
                "payment_method": row['payment_method'],
                "reference_number": row['reference_number'],
                "invoice_number": row['invoice_number']
            })
            total_payments += payment_amount

        statement.closing_balance = opening_balance + total_invoices - total_payments

        return statement

    async def void_receivable(
        self,
        tenant_id: str,
        ar_id: UUID,
        reason: str
    ) -> bool:
        """
        Void an AR record (cannot void if payments applied).

        Args:
            tenant_id: Tenant UUID
            ar_id: AR record UUID
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
                "SELECT COUNT(*) FROM ar_payment_applications WHERE ar_id = $1",
                ar_id
            )

            if payment_count > 0:
                raise ValueError(
                    "Cannot void AR with payments applied. Reverse payments first."
                )

            # Void the record
            result = await conn.execute(
                """
                UPDATE accounts_receivable
                SET status = 'VOID', updated_at = NOW()
                WHERE id = $1 AND tenant_id = $2 AND status != 'VOID'
                """,
                ar_id,
                tenant_id
            )

            return result == "UPDATE 1"

    async def get_total_outstanding(
        self,
        tenant_id: str,
        customer_id: Optional[UUID] = None
    ) -> Decimal:
        """Get total outstanding AR balance."""
        query = """
            SELECT COALESCE(SUM(amount - amount_paid), 0) as total
            FROM accounts_receivable
            WHERE tenant_id = $1
                AND status IN ('OPEN', 'PARTIAL')
                AND ($2::uuid IS NULL OR customer_id = $2)
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id)
            )

            result = await conn.fetchval(query, tenant_id, customer_id)

        return Decimal(str(result)) if result else Decimal("0")
