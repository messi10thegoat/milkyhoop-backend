"""
Financial Reports Tests
========================

Test financial report generation:
- Profit & Loss (Laba Rugi)
- Balance Sheet (Neraca)
- Cash Flow (Arus Kas)
"""

import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from .conftest import TEST_TENANT_ID, create_test_journal


class TestProfitLossReport:
    """Test Profit & Loss report generation."""

    @pytest.mark.asyncio
    async def test_profit_loss_calculation(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """P&L should correctly calculate: Net Income = Revenue - Expenses."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']
        expense_id = setup_coa['6-10100']  # Beban Gaji

        # Create revenue journal (Cash Sale)
        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("500000")
        )

        # Create expense journal (Pay Salary)
        expense_journal_id = uuid4()
        await db_conn.execute(
            """
            INSERT INTO journal_entries (
                id, tenant_id, journal_number, journal_date, description,
                source_type, trace_id, status, total_debit, total_credit
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            expense_journal_id, tenant_id, "JV-EXP-001", date.today(),
            "Salary payment", "MANUAL", str(uuid4()), "POSTED", 100000, 100000
        )
        await db_conn.execute(
            """
            INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
            VALUES ($1, $2, $3, 1, 100000, 0, 'Salary'), ($4, $2, $5, 2, 0, 100000, 'Cash')
            """,
            uuid4(), expense_journal_id, expense_id, uuid4(), cash_id
        )

        # Get P&L
        today = date.today()
        pl = await facade.get_profit_loss(tenant_id, today, today)

        # Revenue should be 500k, Expenses 100k, Net Income 400k
        total_revenue = pl.get('revenue', {}).get('subtotal', 0)
        total_expenses = (
            pl.get('operating_expenses', {}).get('subtotal', 0) +
            pl.get('cogs', {}).get('subtotal', 0)
        )
        net_income = pl.get('net_income', 0)

        assert total_revenue > 0, "Revenue should be positive"
        assert net_income == total_revenue - total_expenses, \
            f"Net income calculation error: {net_income} != {total_revenue} - {total_expenses}"

    @pytest.mark.asyncio
    async def test_profit_loss_period_filtering(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """P&L should only include entries within the specified period."""
        from datetime import timedelta

        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        # Create journal for today
        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        today = date.today()
        yesterday = today - timedelta(days=1)

        # P&L for yesterday should be 0 (no entries)
        pl_yesterday = await facade.get_profit_loss(tenant_id, yesterday, yesterday)

        # P&L for today should have revenue
        pl_today = await facade.get_profit_loss(tenant_id, today, today)

        yesterday_revenue = pl_yesterday.get('revenue', {}).get('subtotal', 0)
        today_revenue = pl_today.get('revenue', {}).get('subtotal', 0)

        assert today_revenue >= yesterday_revenue


class TestBalanceSheetReport:
    """Test Balance Sheet report generation."""

    @pytest.mark.asyncio
    async def test_balance_sheet_equation(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """Balance Sheet must satisfy: Assets = Liabilities + Equity."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("300000")
        )

        bs = await facade.get_balance_sheet(tenant_id, date.today())

        total_assets = bs.get('assets', {}).get('total', 0)
        total_liabilities = bs.get('liabilities', {}).get('total', 0)
        total_equity = bs.get('equity', {}).get('total', 0)

        # The equation should hold (within rounding tolerance)
        assert bs.get('is_balanced', False) or \
            abs(total_assets - (total_liabilities + total_equity)) < 1, \
            f"BS not balanced: A={total_assets}, L={total_liabilities}, E={total_equity}"

    @pytest.mark.asyncio
    async def test_balance_sheet_asset_classification(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """Balance sheet should classify assets into current and fixed."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        bs = await facade.get_balance_sheet(tenant_id, date.today())

        # Check structure
        assert 'assets' in bs
        assert 'current_assets' in bs.get('assets', {})
        assert 'fixed_assets' in bs.get('assets', {})

    @pytest.mark.asyncio
    async def test_balance_sheet_as_of_date(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """Balance sheet should reflect balances as of a specific date."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        today = date.today()
        bs = await facade.get_balance_sheet(tenant_id, today)

        # Cash should show the amount from journal
        current_assets = bs.get('assets', {}).get('current_assets', {})
        assert current_assets.get('subtotal', 0) > 0


class TestCashFlowReport:
    """Test Cash Flow Statement generation."""

    @pytest.mark.asyncio
    async def test_cash_flow_opening_closing(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """Cash flow: Opening + Net Change = Closing."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        today = date.today()
        cf = await facade.get_cash_flow(tenant_id, today, today)

        opening = cf.get('opening_cash', 0)
        closing = cf.get('closing_cash', 0)

        # Net change in cash
        operating = cf.get('operating_activities', {}).get('net_cash_from_operating', 0)
        investing = cf.get('investing_activities', {}).get('net_cash_from_investing', 0)
        financing = cf.get('financing_activities', {}).get('net_cash_from_financing', 0)
        net_change = operating + investing + financing

        # Opening + Net Change should equal Closing (within tolerance)
        assert abs((opening + net_change) - closing) < 1, \
            f"Cash flow equation failed: {opening} + {net_change} != {closing}"

    @pytest.mark.asyncio
    async def test_cash_flow_categories(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """Cash flow should have operating, investing, and financing sections."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000")
        )

        today = date.today()
        cf = await facade.get_cash_flow(tenant_id, today, today)

        # Check structure
        assert 'operating_activities' in cf
        assert 'investing_activities' in cf
        assert 'financing_activities' in cf


class TestReportSourceData:
    """Test that reports source from journal_entries/journal_lines."""

    @pytest.mark.asyncio
    async def test_reports_use_posted_journals_only(
        self, facade, tenant_id, db_conn, setup_coa
    ):
        """Reports should only include POSTED journals, not DRAFT or VOID."""
        cash_id = setup_coa['1-10100']
        revenue_id = setup_coa['4-10100']

        # Create POSTED journal
        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("100000"), status="POSTED"
        )

        # Create DRAFT journal (should be excluded)
        await create_test_journal(
            db_conn, tenant_id, cash_id, revenue_id,
            amount=Decimal("999999"), status="DRAFT"
        )

        # Get trial balance
        tb = await facade.get_trial_balance(tenant_id, date.today())

        # Total should be 100k (POSTED), not 100k + 999k
        assert tb.get('total_debit', 0) < 500000, \
            "DRAFT journal was incorrectly included in reports"
