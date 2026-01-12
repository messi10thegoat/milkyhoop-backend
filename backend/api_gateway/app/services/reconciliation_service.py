"""
Reconciliation Service - AP/GL Balance Validation.

This service ensures the golden rule:
GL_AP_Balance == SUM(bills WHERE status NOT IN ('paid', 'void'))

Any variance indicates a bug in the system.
"""

import logging
from typing import Dict, Any, List
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationResult:
    """Result of AP reconciliation check."""
    bills_outstanding: int
    ap_subledger: Decimal
    gl_ap_balance: Decimal
    variance_bills_ap: Decimal
    variance_ap_gl: Decimal
    is_in_sync: bool


@dataclass
class UnmatchedRecord:
    """Record that doesn't have a matching counterpart."""
    record_type: str  # 'bill' or 'ap'
    record_id: str
    record_number: str
    amount: int
    issue_type: str


class ReconciliationService:
    """Service for checking AP reconciliation status."""

    # AP Account code in Chart of Accounts
    AP_ACCOUNT_CODE = "2-10100"

    def __init__(self, pool: asyncpg.Pool):
        """
        Initialize ReconciliationService.

        Args:
            pool: asyncpg connection pool
        """
        self.pool = pool

    async def check_ap_reconciliation(
        self,
        tenant_id: str
    ) -> ReconciliationResult:
        """
        Compare GL AP balance vs Outstanding Bills.

        This check validates the golden rule:
        GL_AP_Balance == SUM(bills WHERE status NOT IN ('paid', 'void'))

        Returns:
            ReconciliationResult with totals and variance
        """
        async with self.pool.acquire() as conn:
            # Outstanding Bills total
            bills_query = """
                SELECT COALESCE(SUM(amount - amount_paid), 0) as total
                FROM bills
                WHERE tenant_id = $1 AND status NOT IN ('paid', 'void')
            """
            bills_total = await conn.fetchval(bills_query, tenant_id)

            # AP Subledger total
            ap_query = """
                SELECT COALESCE(SUM(amount - amount_paid), 0) as total
                FROM accounts_payable
                WHERE tenant_id = $1 AND status IN ('OPEN', 'PARTIAL')
            """
            ap_total = await conn.fetchval(ap_query, tenant_id)

            # GL AP Account balance (account 2-10100)
            # Formula: SUM(credit - debit) for liability account
            gl_query = """
                SELECT COALESCE(SUM(jl.credit - jl.debit), 0) as balance
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                    AND je.journal_date = jl.journal_date
                JOIN chart_of_accounts coa ON coa.id = jl.account_id
                WHERE je.tenant_id = $1
                  AND je.status = 'POSTED'
                  AND coa.code = $2
            """
            gl_balance = await conn.fetchval(gl_query, tenant_id, self.AP_ACCOUNT_CODE)

            # Calculate variances
            variance_bills_ap = Decimal(str(bills_total)) - ap_total
            variance_ap_gl = ap_total - gl_balance

            # Check if in sync (tolerance: 0.01)
            is_in_sync = (abs(variance_bills_ap) < Decimal('0.01') and
                         abs(variance_ap_gl) < Decimal('0.01'))

            return ReconciliationResult(
                bills_outstanding=int(bills_total),
                ap_subledger=ap_total,
                gl_ap_balance=gl_balance,
                variance_bills_ap=variance_bills_ap,
                variance_ap_gl=variance_ap_gl,
                is_in_sync=is_in_sync
            )

    async def get_unmatched_records(
        self,
        tenant_id: str
    ) -> Dict[str, List[UnmatchedRecord]]:
        """
        Find bills without AP, AP without bills, and bills without journal.

        Returns:
            Dict with lists of unmatched records by type
        """
        async with self.pool.acquire() as conn:
            # Bills without AP record
            bills_without_ap = await conn.fetch("""
                SELECT id, invoice_number, amount, status
                FROM bills
                WHERE tenant_id = $1
                  AND ap_id IS NULL
                  AND status NOT IN ('void', 'paid')
            """, tenant_id)

            # Bills without Journal Entry
            bills_without_journal = await conn.fetch("""
                SELECT id, invoice_number, amount, status
                FROM bills
                WHERE tenant_id = $1
                  AND journal_id IS NULL
                  AND status NOT IN ('void', 'paid')
            """, tenant_id)

            # AP without matching bill
            ap_without_bill = await conn.fetch("""
                SELECT ap.id, ap.bill_number, ap.amount, ap.status
                FROM accounts_payable ap
                LEFT JOIN bills b ON b.ap_id = ap.id
                WHERE ap.tenant_id = $1
                  AND b.id IS NULL
                  AND ap.status NOT IN ('VOID', 'PAID')
            """, tenant_id)

            # Amount mismatch between Bill and AP
            amount_mismatch = await conn.fetch("""
                SELECT b.id, b.invoice_number, b.amount as bill_amount,
                       ap.amount as ap_amount
                FROM bills b
                JOIN accounts_payable ap ON ap.id = b.ap_id
                WHERE b.tenant_id = $1
                  AND b.amount != ap.amount::BIGINT
                  AND b.status NOT IN ('void', 'paid')
            """, tenant_id)

            return {
                "bills_without_ap": [
                    UnmatchedRecord(
                        record_type="bill",
                        record_id=str(row['id']),
                        record_number=row['invoice_number'],
                        amount=int(row['amount']),
                        issue_type="BILL_NO_AP"
                    )
                    for row in bills_without_ap
                ],
                "bills_without_journal": [
                    UnmatchedRecord(
                        record_type="bill",
                        record_id=str(row['id']),
                        record_number=row['invoice_number'],
                        amount=int(row['amount']),
                        issue_type="BILL_NO_JOURNAL"
                    )
                    for row in bills_without_journal
                ],
                "ap_without_bill": [
                    UnmatchedRecord(
                        record_type="ap",
                        record_id=str(row['id']),
                        record_number=row['bill_number'],
                        amount=int(row['amount']),
                        issue_type="AP_NO_BILL"
                    )
                    for row in ap_without_bill
                ],
                "amount_mismatch": [
                    UnmatchedRecord(
                        record_type="bill",
                        record_id=str(row['id']),
                        record_number=row['invoice_number'],
                        amount=int(row['bill_amount']),
                        issue_type="AMOUNT_MISMATCH"
                    )
                    for row in amount_mismatch
                ]
            }

    async def get_reconciliation_summary(
        self,
        tenant_id: str
    ) -> Dict[str, Any]:
        """
        Get full reconciliation summary for dashboard.

        Returns:
            Dict with reconciliation status and unmatched records
        """
        result = await self.check_ap_reconciliation(tenant_id)
        unmatched = await self.get_unmatched_records(tenant_id)

        # Count total issues
        total_issues = (
            len(unmatched['bills_without_ap']) +
            len(unmatched['bills_without_journal']) +
            len(unmatched['ap_without_bill']) +
            len(unmatched['amount_mismatch'])
        )

        return {
            "in_sync": result.is_in_sync,
            "status": "OK" if result.is_in_sync and total_issues == 0 else "WARNING",
            "bills_outstanding": result.bills_outstanding,
            "ap_subledger": float(result.ap_subledger),
            "gl_ap_balance": float(result.gl_ap_balance),
            "variance_bills_ap": float(result.variance_bills_ap),
            "variance_ap_gl": float(result.variance_ap_gl),
            "issues_count": total_issues,
            "issues": {
                "bills_without_ap": len(unmatched['bills_without_ap']),
                "bills_without_journal": len(unmatched['bills_without_journal']),
                "ap_without_bill": len(unmatched['ap_without_bill']),
                "amount_mismatch": len(unmatched['amount_mismatch'])
            }
        }

    async def audit_divergence(
        self,
        tenant_id: str
    ) -> List[Dict[str, Any]]:
        """
        Run full audit using database function.

        Returns:
            List of divergent records
        """
        async with self.pool.acquire() as conn:
            # Use the database audit function
            rows = await conn.fetch(
                "SELECT * FROM audit_ap_divergence($1)",
                tenant_id
            )

            return [
                {
                    "issue_type": row['issue_type'],
                    "record_id": str(row['record_id']),
                    "record_number": row['record_number'],
                    "amount": int(row['amount']),
                    "expected": int(row['expected']),
                    "actual": int(row['actual'])
                }
                for row in rows
            ]
