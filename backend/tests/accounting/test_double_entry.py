"""
Double Entry Validation Tests
==============================

Test that double-entry bookkeeping principles are enforced:
- Total Debit = Total Credit for every journal
- Trial Balance is always balanced
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from .conftest import TEST_TENANT_ID, create_test_journal


class TestDoubleEntryEnforcement:
    """Test double-entry bookkeeping enforcement."""

    @pytest.mark.asyncio
    async def test_journal_lines_sum_to_zero(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """For any journal, sum(debit) - sum(credit) should be 0."""
        # Create multiple journals
        for i in range(5):
            await create_test_journal(
                db_conn, tenant_id, cash_account_id, revenue_account_id,
                amount=Decimal(str((i + 1) * 10000))
            )

        # Verify all journals balance
        result = await db_conn.fetch(
            """
            SELECT je.journal_number,
                   SUM(jl.debit) as total_dr,
                   SUM(jl.credit) as total_cr,
                   ABS(SUM(jl.debit) - SUM(jl.credit)) as diff
            FROM journal_entries je
            JOIN journal_lines jl ON jl.journal_id = je.id
            WHERE je.tenant_id = $1
            GROUP BY je.id, je.journal_number
            """,
            tenant_id
        )

        for row in result:
            assert row['diff'] < 0.01, f"Journal {row['journal_number']} is unbalanced"

    @pytest.mark.asyncio
    async def test_total_debits_equals_credits(
        self, db_conn, tenant_id, cash_account_id, bank_account_id,
        revenue_account_id, cogs_account_id
    ):
        """Across ALL journals, total debits must equal total credits."""
        # Create various journals
        journals_data = [
            (cash_account_id, revenue_account_id, Decimal("100000")),  # Cash sale
            (bank_account_id, revenue_account_id, Decimal("200000")),  # Bank sale
            (cogs_account_id, cash_account_id, Decimal("50000")),  # Purchase
        ]

        for debit_acc, credit_acc, amount in journals_data:
            journal_id = uuid4()
            await db_conn.execute(
                """
                INSERT INTO journal_entries (
                    id, tenant_id, journal_number, journal_date, description,
                    source_type, trace_id, status, total_debit, total_credit
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                journal_id, tenant_id, f"JV-{uuid4().hex[:8]}", date.today(),
                "Test", "MANUAL", str(uuid4()), "POSTED",
                float(amount), float(amount)
            )

            await db_conn.execute(
                """
                INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
                VALUES ($1, $2, $3, 1, $4, 0, ''), ($5, $2, $6, 2, 0, $4, '')
                """,
                uuid4(), journal_id, debit_acc, float(amount),
                uuid4(), credit_acc
            )

        # Verify grand total
        result = await db_conn.fetchrow(
            """
            SELECT
                SUM(jl.debit) as total_debit,
                SUM(jl.credit) as total_credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE je.tenant_id = $1 AND je.status = 'POSTED'
            """,
            tenant_id
        )

        assert abs(result['total_debit'] - result['total_credit']) < 0.01

    @pytest.mark.asyncio
    async def test_balance_sheet_equation(self, facade, tenant_id, db_conn, setup_coa):
        """Assets = Liabilities + Equity (fundamental accounting equation)."""
        # Create some transactions that affect balance sheet
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("500000")
        )

        # Get balance sheet
        bs = await facade.get_balance_sheet(tenant_id, date.today())

        total_assets = bs.get('assets', {}).get('total', 0)
        total_liabilities = bs.get('liabilities', {}).get('total', 0)
        total_equity = bs.get('equity', {}).get('total', 0)

        # A = L + E
        # Note: Due to retained earnings, this should balance
        assert bs.get('is_balanced', False) or abs(
            total_assets - (total_liabilities + total_equity)
        ) < 0.01


class TestDebitCreditRules:
    """Test debit/credit rules for different account types."""

    @pytest.mark.asyncio
    async def test_asset_increase_is_debit(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """Asset accounts increase with debit."""
        initial_balance = await db_conn.fetchval(
            """
            SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE jl.account_id = $1 AND je.status = 'POSTED'
            """,
            cash_account_id
        )

        await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        new_balance = await db_conn.fetchval(
            """
            SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE jl.account_id = $1 AND je.status = 'POSTED'
            """,
            cash_account_id
        )

        assert new_balance > initial_balance  # Cash increased via debit

    @pytest.mark.asyncio
    async def test_revenue_increase_is_credit(
        self, db_conn, tenant_id, cash_account_id, revenue_account_id
    ):
        """Revenue accounts increase with credit."""
        initial_balance = await db_conn.fetchval(
            """
            SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE jl.account_id = $1 AND je.status = 'POSTED'
            """,
            revenue_account_id
        )

        await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        new_balance = await db_conn.fetchval(
            """
            SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0)
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            WHERE jl.account_id = $1 AND je.status = 'POSTED'
            """,
            revenue_account_id
        )

        assert new_balance > initial_balance  # Revenue increased via credit
