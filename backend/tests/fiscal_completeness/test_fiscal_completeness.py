"""
Tests for Fiscal Period Completeness (Class-2 onboarding-completeness invariant).

Each test runs as a synchronous pytest function that drives its own
asyncio.run() — mirrors tests/account_roles/test_account_roles.py to avoid
event-loop fixture entanglement with the rest of the backend test suite.

Invariant under test:
    For every tenant with >= 1 POSTED journal_entry, there must exist a
    fiscal_period whose [start_date, end_date] covers EACH such journal's
    journal_date. A posted journal with no covering period is an
    onboarding-completeness gap (the onboarding flow must provision the
    current fiscal year + 12 monthly periods).

Also asserts the weaker structural floor: any tenant with posted journals
has >= 1 fiscal_year and >= 1 fiscal_period.
"""

import os
import sys
import asyncio
import pytest
import asyncpg

sys.path.insert(0, "/root/milkyhoop-dev/backend/api_gateway")

SUPERUSER_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", ""
)

if not SUPERUSER_DSN:
    pytest.skip(
        "TEST_DATABASE_URL / DATABASE_URL not set; live precondition checks skipped",
        allow_module_level=True,
    )


def _run(coro):
    return asyncio.run(coro)


async def _with_su(callback):
    conn = await asyncpg.connect(SUPERUSER_DSN)
    try:
        return await callback(conn)
    finally:
        await conn.close()


# Tenants intentionally DEFERRED from the completeness invariant (documented).
# grapgrap: complex 2020-2026 pre-go-live history + dangling Mar/Apr 2026 periods
# + hold directive -> backfill deferred per 2026-06-15 decision (see backlog/handover).
# Remove from this set once its fiscal structure is reconciled.
KNOWN_INCOMPLETE_TENANTS = {"grapgrap"}


# -----------------------------------------------------------------------------
# Structural floor — every tenant with posted journals has a fiscal year/period
# -----------------------------------------------------------------------------
def test_tenant_with_posted_journals_has_fiscal_structure():
    async def body(conn):
        rows = await conn.fetch(
            """
            SELECT je.tenant_id,
                   COUNT(*) AS posted_journals,
                   COALESCE(fy.n_years, 0)   AS n_fiscal_years,
                   COALESCE(fp.n_periods, 0) AS n_fiscal_periods
            FROM journal_entries je
            LEFT JOIN (
                SELECT tenant_id, COUNT(*) AS n_years
                FROM fiscal_years GROUP BY tenant_id
            ) fy ON fy.tenant_id = je.tenant_id
            LEFT JOIN (
                SELECT tenant_id, COUNT(*) AS n_periods
                FROM fiscal_periods GROUP BY tenant_id
            ) fp ON fp.tenant_id = je.tenant_id
            WHERE je.status = 'POSTED'
            GROUP BY je.tenant_id, fy.n_years, fp.n_periods
            ORDER BY je.tenant_id
            """
        )
        offenders = [
            {
                "tenant_id": r["tenant_id"],
                "posted_journals": r["posted_journals"],
                "n_fiscal_years": r["n_fiscal_years"],
                "n_fiscal_periods": r["n_fiscal_periods"],
            }
            for r in rows
            if (r["n_fiscal_years"] < 1 or r["n_fiscal_periods"] < 1)
            and r["tenant_id"] not in KNOWN_INCOMPLETE_TENANTS
        ]
        assert not offenders, (
            "Tenants with POSTED journals but NO fiscal year/period "
            f"(onboarding-completeness gap): {offenders}"
        )

    _run(_with_su(body))


# -----------------------------------------------------------------------------
# Coverage invariant — every POSTED journal_date falls inside some period
# -----------------------------------------------------------------------------
def test_every_posted_journal_has_covering_fiscal_period():
    async def body(conn):
        rows = await conn.fetch(
            """
            SELECT je.tenant_id,
                   je.id            AS journal_id,
                   je.journal_date
            FROM journal_entries je
            WHERE je.status = 'POSTED'
              AND NOT EXISTS (
                  SELECT 1 FROM fiscal_periods fp
                  WHERE fp.tenant_id = je.tenant_id
                    AND je.journal_date BETWEEN fp.start_date AND fp.end_date
              )
            ORDER BY je.tenant_id, je.journal_date
            """
        )
        rows = [r for r in rows if r["tenant_id"] not in KNOWN_INCOMPLETE_TENANTS]
        if rows:
            # Summarize per tenant with a sample of offending journal dates.
            by_tenant: dict = {}
            for r in rows:
                acc = by_tenant.setdefault(
                    r["tenant_id"], {"count": 0, "sample_dates": []}
                )
                acc["count"] += 1
                if len(acc["sample_dates"]) < 5:
                    acc["sample_dates"].append(str(r["journal_date"]))
        assert not rows, (
            "POSTED journals with NO covering fiscal_period "
            f"(journal_date outside every [start_date,end_date]): {by_tenant}"
        )

    _run(_with_su(body))
