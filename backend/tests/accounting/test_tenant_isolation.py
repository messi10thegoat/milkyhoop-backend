"""
Test Tenant Isolation — Law 24 Compliance
==========================================

Automated cross-tenant leak detection for CashFlowGenerator.

Uses TWO synthetic tenants with different data and verifies that
querying as Tenant A NEVER returns data belonging to Tenant B.

Run: cd /root/milkyhoop-dev && python3 -m pytest backend/tests/accounting/test_tenant_isolation.py -v
"""

import os
import sys
import asyncio
import pytest
import pytest_asyncio
import asyncpg
from datetime import date
from uuid import uuid4

sys.path.insert(0, "/root/milkyhoop-dev/backend/services")
sys.path.insert(0, "/root/milkyhoop-dev/backend")

from accounting_kernel.reports.cash_flow import CashFlowGenerator

# Force all tests + fixtures to share one event loop
pytestmark = pytest.mark.asyncio(loop_scope="session")

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:Proyek771977@localhost:5433/milkydb"
)

TENANT_A = "test-isolation-alpha"
TENANT_B = "test-isolation-bravo"
PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 12, 31)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    yield pool
    await pool.close()


async def _ensure_coa(conn, tenant_id):
    """Create minimal CoA for a test tenant if not present."""
    count = await conn.fetchval(
        "SELECT count(*) FROM chart_of_accounts WHERE tenant_id = $1",
        tenant_id,
    )
    if count > 0:
        rows = await conn.fetch(
            "SELECT id, account_code FROM chart_of_accounts WHERE tenant_id = $1",
            tenant_id,
        )
        return {r["account_code"]: r["id"] for r in rows}

    accounts = [
        ("1-10100", "Kas", "ASSET", "DEBIT"),
        ("1-10200", "Bank", "ASSET", "DEBIT"),
        ("1-10400", "Piutang Usaha", "ASSET", "DEBIT"),
        ("2-10100", "Hutang Usaha", "LIABILITY", "CREDIT"),
        ("3-10100", "Modal Pemilik", "EQUITY", "CREDIT"),
        ("4-10100", "Penjualan", "REVENUE", "CREDIT"),
        ("5-10100", "HPP", "COGS", "DEBIT"),
        ("6-10100", "Beban Operasi", "EXPENSE", "DEBIT"),
    ]
    for code, name, atype, normal in accounts:
        await conn.execute(
            """
            INSERT INTO chart_of_accounts
                (id, tenant_id, account_code, name, account_type,
                 normal_balance, level, is_active, is_header,
                 created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6, 1, true, false, NOW(), NOW())
            ON CONFLICT (tenant_id, account_code) DO NOTHING
        """,
            uuid4(),
            tenant_id,
            code,
            name,
            atype,
            normal,
        )

    rows = await conn.fetch(
        "SELECT id, account_code FROM chart_of_accounts WHERE tenant_id = $1",
        tenant_id,
    )
    return {r["account_code"]: r["id"] for r in rows}


async def _create_journal(
    conn, tenant_id, acct_map, jdate, desc, lines, source_type="MANUAL"
):
    """Create a posted journal entry."""
    jid = uuid4()
    total = sum(d for _, d, _ in lines)
    await conn.execute(
        """
        INSERT INTO journal_entries
            (id, tenant_id, journal_number, journal_date, description,
             source_type, trace_id, status, total_debit, total_credit)
        VALUES ($1,$2,$3,$4,$5,$6,$7,'POSTED',$8,$9)
    """,
        jid,
        tenant_id,
        f"JV-ISO-{jid.hex[:8].upper()}",
        jdate,
        desc,
        source_type,
        str(uuid4()),
        float(total),
        float(total),
    )
    for i, (code, debit, credit) in enumerate(lines, 1):
        await conn.execute(
            """
            INSERT INTO journal_lines
                (id, journal_id, account_id, line_number, debit, credit, memo)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
        """,
            uuid4(),
            jid,
            acct_map[code],
            i,
            float(debit),
            float(credit),
            desc,
        )


async def _cleanup_tenant(conn, tenant_id):
    """Remove all test data for a tenant, bypassing immutability triggers."""
    # Disable triggers temporarily for cleanup
    await conn.execute("SET session_replication_role = replica")
    await conn.execute(
        "DELETE FROM journal_lines WHERE journal_id IN "
        "(SELECT id FROM journal_entries WHERE tenant_id = $1)",
        tenant_id,
    )
    await conn.execute("DELETE FROM journal_entries WHERE tenant_id = $1", tenant_id)
    await conn.execute("DELETE FROM chart_of_accounts WHERE tenant_id = $1", tenant_id)
    await conn.execute('DELETE FROM "Tenant" WHERE id = $1', tenant_id)
    await conn.execute("SET session_replication_role = DEFAULT")


@pytest_asyncio.fixture(scope="session")
async def seed_tenants(db_pool):
    """
    Seed two test tenants with DIFFERENT cash flow data.
    Tenant A: 50M opening + 10M sale = 60M cash
    Tenant B:  5M opening +  2M sale =  7M cash
    """
    async with db_pool.acquire() as conn:
        # Clean previous runs (bypass triggers)
        for tid in [TENANT_A, TENANT_B]:
            await _cleanup_tenant(conn, tid)

        # Create tenants
        for tid, display in [
            (TENANT_A, "Isolation Test Alpha"),
            (TENANT_B, "Isolation Test Bravo"),
        ]:
            await conn.execute(
                """
                INSERT INTO "Tenant"
                    (id, alias, display_name, menu_items, status, updated_at)
                VALUES ($1, $1, $2, '{}', 'ACTIVE', NOW())
                ON CONFLICT (id) DO NOTHING
            """,
                tid,
                display,
            )

        # Create CoA
        accounts = {}
        for tid in [TENANT_A, TENANT_B]:
            accounts[tid] = await _ensure_coa(conn, tid)

        # Tenant A: 50M opening + 10M cash sale
        await _create_journal(
            conn,
            TENANT_A,
            accounts[TENANT_A],
            date(2026, 1, 1),
            "Opening Balance Alpha",
            [("1-10100", 50_000_000, 0), ("3-10100", 0, 50_000_000)],
            source_type="OPENING",
        )
        await _create_journal(
            conn,
            TENANT_A,
            accounts[TENANT_A],
            date(2026, 2, 15),
            "Cash Sale Alpha",
            [("1-10100", 10_000_000, 0), ("4-10100", 0, 10_000_000)],
        )

        # Tenant B: 5M opening + 2M cash sale
        await _create_journal(
            conn,
            TENANT_B,
            accounts[TENANT_B],
            date(2026, 1, 1),
            "Opening Balance Bravo",
            [("1-10100", 5_000_000, 0), ("3-10100", 0, 5_000_000)],
            source_type="OPENING",
        )
        await _create_journal(
            conn,
            TENANT_B,
            accounts[TENANT_B],
            date(2026, 3, 10),
            "Cash Sale Bravo",
            [("1-10100", 2_000_000, 0), ("4-10100", 0, 2_000_000)],
        )

    yield {
        "tenant_a": TENANT_A,
        "tenant_b": TENANT_B,
        "expected": {
            TENANT_A: {"cash": 60_000_000, "opening": 50_000_000},
            TENANT_B: {"cash": 7_000_000, "opening": 5_000_000},
        },
    }

    # Teardown
    async with db_pool.acquire() as conn:
        for tid in [TENANT_A, TENANT_B]:
            await _cleanup_tenant(conn, tid)


# ---------------------------------------------------------------------------
# Tests: CashFlowGenerator Tenant Isolation
# ---------------------------------------------------------------------------


class TestCashFlowTenantIsolation:
    async def test_tenant_a_sees_only_own_cash(self, db_pool, seed_tenants):
        """Tenant A (60M) must NOT see Tenant B's 7M."""
        gen = CashFlowGenerator(db_pool)
        report = await gen.generate(TENANT_A, PERIOD_START, PERIOD_END)
        expected = seed_tenants["expected"][TENANT_A]["cash"]
        assert (
            int(report.ending_cash) == expected
        ), f"Tenant A ending_cash={report.ending_cash}, expected={expected}"

    async def test_tenant_b_sees_only_own_cash(self, db_pool, seed_tenants):
        """Tenant B (7M) must NOT see Tenant A's 60M."""
        gen = CashFlowGenerator(db_pool)
        report = await gen.generate(TENANT_B, PERIOD_START, PERIOD_END)
        expected = seed_tenants["expected"][TENANT_B]["cash"]
        assert (
            int(report.ending_cash) == expected
        ), f"Tenant B ending_cash={report.ending_cash}, expected={expected}"

    async def test_no_cross_contamination_operating(self, db_pool, seed_tenants):
        """Operating lines must not leak across tenants."""
        gen = CashFlowGenerator(db_pool)
        report_a = await gen.generate(TENANT_A, PERIOD_START, PERIOD_END)
        report_b = await gen.generate(TENANT_B, PERIOD_START, PERIOD_END)

        for d in [l.description for l in report_a.operating_activities.lines]:
            assert "Bravo" not in d, f"LEAK: A operating has B data: '{d}'"
        for d in [l.description for l in report_b.operating_activities.lines]:
            assert "Alpha" not in d, f"LEAK: B operating has A data: '{d}'"

    async def test_no_cross_contamination_financing(self, db_pool, seed_tenants):
        """Financing lines must not leak across tenants."""
        gen = CashFlowGenerator(db_pool)
        report_a = await gen.generate(TENANT_A, PERIOD_START, PERIOD_END)
        report_b = await gen.generate(TENANT_B, PERIOD_START, PERIOD_END)

        for d in [l.description for l in report_a.financing_activities.lines]:
            assert "Bravo" not in d, f"LEAK: A financing has B data: '{d}'"
        for d in [l.description for l in report_b.financing_activities.lines]:
            assert "Alpha" not in d, f"LEAK: B financing has A data: '{d}'"

    async def test_beginning_cash_isolation(self, db_pool, seed_tenants):
        """Beginning cash must be isolated per tenant."""
        gen = CashFlowGenerator(db_pool)
        report_a = await gen.generate(TENANT_A, date(2026, 2, 1), date(2026, 2, 28))
        report_b = await gen.generate(TENANT_B, date(2026, 2, 1), date(2026, 2, 28))
        exp_a = seed_tenants["expected"][TENANT_A]["opening"]
        exp_b = seed_tenants["expected"][TENANT_B]["opening"]

        assert int(report_a.beginning_cash) == exp_a
        assert int(report_b.beginning_cash) == exp_b
        assert report_a.beginning_cash != report_b.beginning_cash

    async def test_is_balanced_per_tenant(self, db_pool, seed_tenants):
        """IronLaw Guard must reconcile per-tenant, not globally."""
        gen = CashFlowGenerator(db_pool)
        report_a = await gen.generate(TENANT_A, PERIOD_START, PERIOD_END)
        report_b = await gen.generate(TENANT_B, PERIOD_START, PERIOD_END)
        assert (
            report_a.is_balanced
        ), f"A: ending={report_a.ending_cash} actual={report_a.actual_ending_cash}"
        assert (
            report_b.is_balanced
        ), f"B: ending={report_b.ending_cash} actual={report_b.actual_ending_cash}"

    async def test_nonexistent_tenant_returns_zeros(self, db_pool, seed_tenants):
        """Tenant with no data must get all zeros."""
        gen = CashFlowGenerator(db_pool)
        report = await gen.generate("nonexistent-tenant-xyz", PERIOD_START, PERIOD_END)
        assert int(report.beginning_cash) == 0
        assert int(report.ending_cash) == 0
        assert len(report.operating_activities.lines) == 0
        assert len(report.financing_activities.lines) == 0


# ---------------------------------------------------------------------------
# Tests: Raw SQL Tenant Isolation
# ---------------------------------------------------------------------------


class TestRawSQLTenantIsolation:
    async def test_cash_balance_filtered_vs_combined(self, db_pool, seed_tenants):
        """
        Filtered query returns per-tenant balance.
        Combined query returns sum — proving filter is necessary.
        """
        async with db_pool.acquire() as conn:
            balances = {}
            for tid in [TENANT_A, TENANT_B]:
                bal = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
                    FROM journal_lines jl
                    JOIN journal_entries je ON je.id = jl.journal_id
                    JOIN chart_of_accounts c ON c.id = jl.account_id
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND je.journal_date <= $2
                      AND (c.account_code LIKE '1-101%'
                           OR c.account_code LIKE '1-102%'
                           OR c.account_code LIKE '1-103%')
                """,
                    tid,
                    PERIOD_END,
                )
                balances[tid] = int(bal)

            exp = seed_tenants["expected"]
            assert balances[TENANT_A] == exp[TENANT_A]["cash"]
            assert balances[TENANT_B] == exp[TENANT_B]["cash"]

            combined = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.debit - jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.journal_id
                JOIN chart_of_accounts c ON c.id = jl.account_id
                WHERE je.tenant_id IN ($1, $2)
                  AND je.status = 'POSTED'
                  AND je.journal_date <= $3
                  AND (c.account_code LIKE '1-101%'
                       OR c.account_code LIKE '1-102%'
                       OR c.account_code LIKE '1-103%')
            """,
                TENANT_A,
                TENANT_B,
                PERIOD_END,
            )

            assert int(combined) == exp[TENANT_A]["cash"] + exp[TENANT_B]["cash"]
            assert int(combined) > balances[TENANT_A]

    async def test_journal_count_filtered(self, db_pool, seed_tenants):
        """Each tenant must see exactly their own journal count."""
        async with db_pool.acquire() as conn:
            for tid in [TENANT_A, TENANT_B]:
                count = await conn.fetchval(
                    """
                    SELECT count(DISTINCT je.id)
                    FROM journal_entries je
                    WHERE je.tenant_id = $1
                      AND je.status = 'POSTED'
                      AND je.journal_date >= $2
                      AND je.journal_date <= $3
                """,
                    tid,
                    PERIOD_START,
                    PERIOD_END,
                )
                assert count == 2, f"{tid}: count={count}, expected=2"

    async def test_rls_layer(self, db_pool, seed_tenants):
        """
        Layer 2: RLS within transaction should also isolate.

        NOTE: PostgreSQL superusers and table owners bypass RLS.
        If this test skips, the app DB role should be a non-superuser
        with FORCE ROW LEVEL SECURITY for full Layer 2 protection.
        """
        async with db_pool.acquire() as conn:
            rls_enabled = await conn.fetchval(
                """
                SELECT relrowsecurity FROM pg_class
                WHERE relname = 'journal_entries'
            """
            )
            if not rls_enabled:
                pytest.skip("RLS not enabled on journal_entries")

            # Check if current role is superuser (bypasses RLS)
            is_super = await conn.fetchval("SELECT current_setting('is_superuser')")
            if is_super == "on":
                pytest.skip(
                    "Connected as superuser — RLS bypassed. "
                    "App should use non-superuser role for RLS enforcement. "
                    "Layer 1 (explicit WHERE) is the primary defense."
                )

            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    TENANT_A,
                )
                count = await conn.fetchval(
                    """
                    SELECT count(*) FROM journal_entries
                    WHERE status = 'POSTED'
                      AND journal_date >= $1 AND journal_date <= $2
                """,
                    PERIOD_START,
                    PERIOD_END,
                )
                assert count == 2, f"RLS leak: got {count}, expected 2"


# ---------------------------------------------------------------------------
# Tests: Regression Guards
# ---------------------------------------------------------------------------


class TestTenantIsolationRegression:
    async def test_set_local_persists_in_transaction(self, db_pool, seed_tenants):
        """Regression 2026-02-19: SET LOCAL must persist within transaction."""
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    TENANT_B,
                )
                val = await conn.fetchval(
                    "SELECT current_setting('app.tenant_id', true)"
                )
                assert val == TENANT_B

    async def test_amounts_differ_between_tenants(self, db_pool, seed_tenants):
        """Regression 2026-02-19: original bug showed same total for all tenants."""
        gen = CashFlowGenerator(db_pool)
        report_a = await gen.generate(TENANT_A, PERIOD_START, PERIOD_END)
        report_b = await gen.generate(TENANT_B, PERIOD_START, PERIOD_END)
        assert (
            report_a.ending_cash != report_b.ending_cash
        ), f"CRITICAL: identical ending_cash ({report_a.ending_cash})"

    async def test_sequential_queries_dont_bleed(self, db_pool, seed_tenants):
        """Querying A then B must not leak A's data into B."""
        gen = CashFlowGenerator(db_pool)
        await gen.generate(TENANT_A, PERIOD_START, PERIOD_END)
        report_b = await gen.generate(TENANT_B, PERIOD_START, PERIOD_END)
        exp_b = seed_tenants["expected"][TENANT_B]["cash"]
        assert int(report_b.ending_cash) == exp_b

    async def test_concurrent_queries_dont_bleed(self, db_pool, seed_tenants):
        """Concurrent queries for different tenants must not interfere."""
        gen = CashFlowGenerator(db_pool)
        report_a, report_b = await asyncio.gather(
            gen.generate(TENANT_A, PERIOD_START, PERIOD_END),
            gen.generate(TENANT_B, PERIOD_START, PERIOD_END),
        )
        exp = seed_tenants["expected"]
        assert int(report_a.ending_cash) == exp[TENANT_A]["cash"]
        assert int(report_b.ending_cash) == exp[TENANT_B]["cash"]
