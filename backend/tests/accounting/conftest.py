"""
Fixtures for Accounting Kernel Tests
=====================================

Provides pytest fixtures for:
- Database connection pool
- Test tenant setup
- Chart of Accounts seeding
- Transaction rollback for test isolation
"""

import os
import sys
import pytest
import pytest_asyncio
import asyncio
import asyncpg
from decimal import Decimal
from datetime import date
from uuid import uuid4

# Add paths for imports
sys.path.insert(0, '/root/milkyhoop-dev/backend/services')
sys.path.insert(0, '/root/milkyhoop-dev/backend')

from accounting_kernel.integration.facade import AccountingFacade
from accounting_kernel.services.journal_service import JournalService
from accounting_kernel.services.coa_service import CoAService
from accounting_kernel.services.ledger_service import LedgerService
from accounting_kernel.constants import SourceType, JournalStatus

# Test configuration
TEST_TENANT_ID = "test-tenant-accounting"
DATABASE_URL = os.environ.get(
    'TEST_DATABASE_URL',
    'postgresql://postgres:Proyek771977@localhost:5433/milkydb'
)

# Configure pytest-asyncio loop scope
pytest_plugins = ('pytest_asyncio',)


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests (session-scoped)."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_pool(event_loop):
    """Create database connection pool (session-scoped for performance)."""
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30
    )
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def db_conn(db_pool):
    """
    Get a database connection with transaction rollback.
    Each test runs in a transaction that gets rolled back.
    """
    async with db_pool.acquire() as conn:
        txn = conn.transaction()
        await txn.start()

        # Set tenant context
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true)",
            TEST_TENANT_ID
        )

        yield conn

        # Rollback to clean up test data
        await txn.rollback()


@pytest.fixture
def tenant_id():
    """Get test tenant ID."""
    return TEST_TENANT_ID


@pytest_asyncio.fixture
async def setup_tenant(db_conn, tenant_id):
    """
    Ensure test tenant exists in the database.
    Creates it if it doesn't exist.
    """
    # Check if tenant exists
    existing = await db_conn.fetchval(
        'SELECT COUNT(*) FROM "Tenant" WHERE id = $1',
        tenant_id
    )

    if existing == 0:
        # Create test tenant
        await db_conn.execute(
            '''
            INSERT INTO "Tenant" (id, alias, display_name, menu_items, status, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (id) DO NOTHING
            ''',
            tenant_id,
            'test-accounting',
            'Test Tenant Accounting',
            '{}',  # empty menu_items JSON
            'ACTIVE'
        )

    return tenant_id


@pytest_asyncio.fixture
async def setup_coa(db_conn, setup_tenant):
    """
    Set up Chart of Accounts for test tenant.
    Returns dict of account codes to account IDs.
    """
    tenant_id = setup_tenant  # setup_tenant returns the tenant_id

    # Check if CoA already exists
    existing = await db_conn.fetchval(
        "SELECT COUNT(*) FROM chart_of_accounts WHERE tenant_id = $1",
        tenant_id
    )

    if existing > 0:
        # Return existing account mapping
        rows = await db_conn.fetch(
            "SELECT id, account_code FROM chart_of_accounts WHERE tenant_id = $1",
            tenant_id
        )
        return {row['account_code']: row['id'] for row in rows}

    # Create test accounts (id, tenant_id, account_code, name, account_type, normal_balance)
    accounts = [
        # Assets
        (uuid4(), tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT'),
        (uuid4(), tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT'),
        (uuid4(), tenant_id, '1-10300', 'Piutang Usaha', 'ASSET', 'DEBIT'),
        (uuid4(), tenant_id, '1-10400', 'Persediaan', 'ASSET', 'DEBIT'),
        # Liabilities
        (uuid4(), tenant_id, '2-10100', 'Hutang Usaha', 'LIABILITY', 'CREDIT'),
        (uuid4(), tenant_id, '2-10200', 'Hutang Gaji', 'LIABILITY', 'CREDIT'),
        # Equity
        (uuid4(), tenant_id, '3-10100', 'Modal', 'EQUITY', 'CREDIT'),
        (uuid4(), tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT'),
        # Revenue
        (uuid4(), tenant_id, '4-10100', 'Pendapatan Penjualan', 'INCOME', 'CREDIT'),
        (uuid4(), tenant_id, '4-10200', 'Pendapatan Lainnya', 'INCOME', 'CREDIT'),
        # Expenses
        (uuid4(), tenant_id, '5-10100', 'Harga Pokok Penjualan', 'EXPENSE', 'DEBIT'),
        (uuid4(), tenant_id, '6-10100', 'Beban Gaji', 'EXPENSE', 'DEBIT'),
        (uuid4(), tenant_id, '6-10200', 'Beban Sewa', 'EXPENSE', 'DEBIT'),
    ]

    for acc in accounts:
        await db_conn.execute(
            """
            INSERT INTO chart_of_accounts (id, tenant_id, account_code, name, account_type, normal_balance)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id, account_code) DO NOTHING
            """,
            *acc
        )

    return {acc[2]: acc[0] for acc in accounts}


@pytest.fixture
def cash_account_id(setup_coa):
    """Get Cash account ID."""
    return setup_coa.get('1-10100')


@pytest.fixture
def bank_account_id(setup_coa):
    """Get Bank account ID."""
    return setup_coa.get('1-10200')


@pytest.fixture
def ar_account_id(setup_coa):
    """Get Accounts Receivable ID."""
    return setup_coa.get('1-10300')


@pytest.fixture
def ap_account_id(setup_coa):
    """Get Accounts Payable ID."""
    return setup_coa.get('2-10100')


@pytest.fixture
def revenue_account_id(setup_coa):
    """Get Sales Revenue account ID."""
    return setup_coa.get('4-10100')


@pytest.fixture
def cogs_account_id(setup_coa):
    """Get COGS account ID."""
    return setup_coa.get('5-10100')


@pytest.fixture
def facade(db_pool):
    """Get AccountingFacade instance."""
    return AccountingFacade(db_pool)


@pytest.fixture
def coa_service(db_pool):
    """Get CoAService instance."""
    return CoAService(db_pool)


@pytest.fixture
def journal_service(db_pool, coa_service):
    """Get JournalService instance."""
    return JournalService(db_pool, coa_service)


@pytest.fixture
def ledger_service(db_pool):
    """Get LedgerService instance."""
    return LedgerService(db_pool)


# Helper functions for tests
async def create_test_journal(
    conn,
    tenant_id: str,
    cash_account_id,
    revenue_account_id,
    amount: Decimal = Decimal("100000"),
    status: str = "POSTED"
) -> tuple:
    """
    Create a test journal entry (cash sale).
    Returns (journal_id, journal_number).
    """
    journal_id = uuid4()
    journal_number = f"JV-TEST-{uuid4().hex[:8].upper()}"

    # Create journal header
    await conn.execute(
        """
        INSERT INTO journal_entries (
            id, tenant_id, journal_number, journal_date, description,
            source_type, trace_id, status, total_debit, total_credit
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        journal_id,
        tenant_id,
        journal_number,
        date.today(),
        "Test journal entry",
        SourceType.MANUAL.value,
        str(uuid4()),
        status,
        float(amount),
        float(amount)
    )

    # Create journal lines
    await conn.execute(
        """
        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        uuid4(),
        journal_id,
        cash_account_id,
        1,
        float(amount),
        0,
        "Cash received"
    )

    await conn.execute(
        """
        INSERT INTO journal_lines (id, journal_id, account_id, line_number, debit, credit, memo)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        uuid4(),
        journal_id,
        revenue_account_id,
        2,
        0,
        float(amount),
        "Sales revenue"
    )

    return journal_id, journal_number
