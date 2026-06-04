"""Tests for role_precondition (Fase C0)."""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
import pytest

sys.path.insert(0, "/root/milkyhoop-dev/backend/api_gateway")

from app.services.role_precondition import (  # noqa: E402
    PreconditionFailedError,
    assert_required_roles_for_path,
    check_required_roles_for_path,
)

SUPERUSER_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "",  # set TEST_DATABASE_URL env var
)


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# V150 idempotency — re-apply does not create duplicates.
# ---------------------------------------------------------------------------
def test_v150_idempotent_no_duplicate_2_10500():
    async def body():
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            # Should be 5 (one per tenant) — no duplicates from V150 re-apply.
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM chart_of_accounts WHERE account_code='2-10500'"
            )
            assert n >= 5, f"expected >=5 rows of 2-10500, got {n}"

            dupes = await conn.fetch(
                "SELECT tenant_id, COUNT(*) AS c FROM chart_of_accounts "
                "WHERE account_code='2-10500' GROUP BY tenant_id HAVING COUNT(*) > 1"
            )
            assert not dupes, f"duplicate 2-10500 rows: {dupes!r}"
        finally:
            await conn.close()

    _run(body())


def test_v150_customer_deposit_mapping_complete():
    """All tenants should now have CUSTOMER_DEPOSIT_LIABILITY mapped."""

    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool, "customer_deposits", ["CUSTOMER_DEPOSIT_LIABILITY"]
            )
            assert gaps == {}, f"unexpected gaps: {gaps!r}"
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# Precondition util — clean path
# ---------------------------------------------------------------------------
def test_precondition_clean_for_sales_invoices_tier1():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "sales_invoices",
                [
                    "AR_TRADE",
                    "REVENUE_SALES_GOODS",
                    "COGS_SALES",
                    "INVENTORY_MERCHANDISE",
                ],
            )
            assert gaps == {}, f"unexpected gaps: {gaps!r}"
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# Precondition util — gap path (TIER 3 INVENTORY_WIP not seeded)
# ---------------------------------------------------------------------------
def test_precondition_reports_gap_for_unseeded_role():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool, "manufacturing_wip", ["INVENTORY_WIP"]
            )
            assert "INVENTORY_WIP" in gaps
            # Every active tenant should appear in the gap list.
            n_tenants = len(gaps["INVENTORY_WIP"])
            assert n_tenants >= 1, "expected at least one tenant gap"
        finally:
            await pool.close()

    _run(body())


def test_assert_raises_precondition_failed():
    async def body():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError) as ei:
                await assert_required_roles_for_path(
                    pool, "manufacturing_wip", ["INVENTORY_WIP"]
                )
            assert ei.value.path_name == "manufacturing_wip"
            assert "INVENTORY_WIP" in ei.value.gaps
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# Unknown role rejected (typo guard).
# ---------------------------------------------------------------------------
def test_precondition_rejects_unknown_role():
    async def body():
        pool = await _make_pool()
        try:
            with pytest.raises(ValueError):
                await check_required_roles_for_path(pool, "bogus", ["AR_TRDE"])
        finally:
            await pool.close()

    _run(body())
