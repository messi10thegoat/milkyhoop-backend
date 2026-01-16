"""
Trial Balance Tests
====================

Test trial balance calculation and balance verification.
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from .conftest import TEST_TENANT_ID, create_test_journal


class TestTrialBalanceCalculation:
    """Test trial balance calculations."""

    @pytest.mark.asyncio
    async def test_trial_balance_is_balanced(
        self, facade, tenant_id, db_conn, cash_account_id, revenue_account_id
    ):
        """Trial balance total debit must equal total credit."""
        # Create some journals
        for i in range(3):
            await create_test_journal(
                db_conn, tenant_id, cash_account_id, revenue_account_id,
                amount=Decimal(str((i + 1) * 100000))
            )

        # Get trial balance
        tb = await facade.get_trial_balance(tenant_id, date.today())

        total_debit = Decimal(str(tb.get('total_debit', 0)))
        total_credit = Decimal(str(tb.get('total_credit', 0)))

        assert tb.get('is_balanced', False), \
            f"Trial balance not balanced: DR={total_debit}, CR={total_credit}"

    @pytest.mark.asyncio
    async def test_trial_balance_includes_all_accounts_with_activity(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """Trial balance should include all accounts that have journal entries."""
        cash_id = setup_coa['1-10100']
        bank_id = setup_coa['1-10200']
        revenue_id = setup_coa['4-10100']

        # Create cash sale
        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        # Create bank sale
        journal_id = uuid4()
        await db_conn.execute(
            """
            INSERT INTO journal_entries (
                id, tenant_id, journal_number, journal_date, description,
                source_type, trace_id, status, total_debit, total_credit
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            journal_id, tenant_id, f"JV-BANK-001", date.today(),
            "Bank sale", "MANUAL", str(uuid4()), "POSTED", 200000, 200000
        )
        await db_conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
            VALUES ($1, $2, $3, 1, 200000, 0, ''), ($4, $2, $5, 2, 0, 200000, '')
            """,
            uuid4(), journal_id, bank_id, uuid4(), revenue_id
        )

        # Get trial balance
        tb = await facade.get_trial_balance(tenant_id, date.today())
        account_codes = [row['account_code'] for row in tb.get('rows', [])]

        assert '1-10100' in account_codes, "Cash account missing from TB"
        assert '1-10200' in account_codes, "Bank account missing from TB"
        assert '4-10100' in account_codes, "Revenue account missing from TB"

    @pytest.mark.asyncio
    async def test_trial_balance_respects_date(
        self, ledger_service, tenant_id, db_conn, cash_account_id, revenue_account_id
    ):
        """Trial balance should only include entries up to the specified date."""
        today = date.today()

        # Create a journal for today
        await create_test_journal(
            db_conn, tenant_id, cash_account_id, revenue_account_id,
            amount=Decimal("100000")
        )

        # Get TB as of today - should include the journal
        tb_today = await ledger_service.get_trial_balance(tenant_id, today)
        assert tb_today.total_debit > 0

        # Get TB as of yesterday - should not include today's journal
        from datetime import timedelta
        yesterday = today - timedelta(days=1)
        tb_yesterday = await ledger_service.get_trial_balance(tenant_id, yesterday)

        # Yesterday's TB should have less or equal activity
        assert tb_yesterday.total_debit <= tb_today.total_debit


class TestTrialBalanceAccuracy:
    """Test trial balance accuracy and account balances."""

    @pytest.mark.asyncio
    async def test_account_balance_calculation(
        self, ledger_service, tenant_id, db_conn, cash_account_id, revenue_account_id
    ):
        """Account balances should be calculated correctly."""
        amounts = [Decimal("50000"), Decimal("75000"), Decimal("25000")]
        total_expected = sum(amounts)

        for amount in amounts:
            await create_test_journal(
                db_conn, tenant_id, cash_account_id, revenue_account_id,
                amount=amount
            )

        # Get cash account balance
        cash_balance = await ledger_service.get_account_balance(
            tenant_id, '1-10100', date.today()
        )

        assert cash_balance.balance == total_expected, \
            f"Expected {total_expected}, got {cash_balance.balance}"

    @pytest.mark.asyncio
    async def test_normal_balance_direction(
        self, ledger_service, tenant_id, db_conn, setup_coa
    ):
        """
        Test that normal balance direction is respected:
        - Assets: Debit balance
        - Revenue: Credit balance
        """
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        tb = await ledger_service.get_trial_balance(tenant_id, date.today())

        for row in tb.rows:
            if row.account_code == '1-10100':  # Cash (Asset)
                # Asset with debit normal balance should show debit_balance
                assert row.debit_balance >= 0
            elif row.account_code == '4-10100':  # Revenue
                # Revenue with credit normal balance should show credit_balance
                assert row.credit_balance >= 0


class TestTrialBalanceByType:
    """Test trial balance filtering by account type."""

    @pytest.mark.asyncio
    async def test_filter_by_account_type(
        self, ledger_service, tenant_id, db_conn, setup_coa
    ):
        """Trial balance should be filterable by account type."""
        from accounting_kernel.constants import AccountType

        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        # Get only ASSET accounts
        tb_assets = await ledger_service.get_trial_balance(
            tenant_id, date.today(), account_type=AccountType.ASSET
        )

        for row in tb_assets.rows:
            assert row.account_type == AccountType.ASSET

        # Get only INCOME accounts
        tb_income = await ledger_service.get_trial_balance(
            tenant_id, date.today(), account_type=AccountType.INCOME
        )

        for row in tb_income.rows:
            assert row.account_type == AccountType.INCOME
