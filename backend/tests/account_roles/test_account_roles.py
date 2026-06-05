"""
Tests for Account Role Mapping Layer (Fase B).

Each test runs as a synchronous pytest function that drives its own
asyncio.run() — avoids event-loop fixture entanglement with the rest of
the backend test suite.

Covers:
    - Resolver: mapped, unmapped, invalid role_key
    - RLS isolation (milkyadmin user)
    - Seed idempotency
    - VAT_OUTPUT is_interim=true
    - TIER 3 roles NOT seeded
    - Law 18 header guard (cannot map to is_header=true)
"""

import os
import sys
import asyncio
import pytest
import asyncpg

sys.path.insert(0, "/root/milkyhoop-dev/backend/api_gateway")

from app.services.role_resolver import (  # noqa: E402
    AccountRole,
    AccountRoleUnmappedError,
    is_valid_role,
    resolve_account_id_by_role,
)

SUPERUSER_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", ""
)

if not SUPERUSER_DSN:
    pytest.skip(
        "TEST_DATABASE_URL / DATABASE_URL not set; live precondition checks skipped",
        allow_module_level=True,
    )
TENANT_A = "milkytest"
TENANT_B = "grapgrap"


def _run(coro):
    return asyncio.run(coro)


async def _with_su(callback):
    conn = await asyncpg.connect(SUPERUSER_DSN)
    try:
        return await callback(conn)
    finally:
        await conn.close()


async def _with_rls(callback):
    """Connect as superuser then SET ROLE milkyadmin (NOBYPASSRLS) to
    exercise FORCE ROW LEVEL SECURITY without needing milkyadmin's password."""
    conn = await asyncpg.connect(SUPERUSER_DSN)
    try:
        await conn.execute("SET ROLE milkyadmin")
        return await callback(conn)
    finally:
        try:
            await conn.execute("RESET ROLE")
        except Exception:
            pass
        await conn.close()


# -----------------------------------------------------------------------------
# Catalog / validation (sync)
# -----------------------------------------------------------------------------
def test_is_valid_role_tier1():
    assert is_valid_role(AccountRole.AR_TRADE)
    assert is_valid_role(AccountRole.CASH_GENERAL)


def test_is_valid_role_tier3_reserved():
    assert is_valid_role(AccountRole.VAT_INPUT)
    assert is_valid_role(AccountRole.WHT_PPH21)


def test_is_valid_role_typo():
    assert not is_valid_role("AR_TRDE")
    assert not is_valid_role("")


# -----------------------------------------------------------------------------
# Resolver
# -----------------------------------------------------------------------------
def test_resolver_returns_account_id_for_mapped_role():
    async def body(conn):
        acct_id = await resolve_account_id_by_role(conn, TENANT_A, AccountRole.AR_TRADE)
        assert acct_id is not None
        code = await conn.fetchval(
            "SELECT account_code FROM chart_of_accounts WHERE id = $1",
            acct_id,
        )
        assert code == "1-10400"

    _run(_with_su(body))


def test_resolver_raises_for_unmapped_tier3_role():
    # V155 Fase D1: VAT_INPUT promoted to TIER 1 (mapped 5/5). Use a still-reserved
    # role for this test (granular WHT, kept as reservation per Q2).
    async def body(conn):
        with pytest.raises(AccountRoleUnmappedError):
            await resolve_account_id_by_role(conn, TENANT_A, AccountRole.WHT_PPH21)

    _run(_with_su(body))


def test_resolver_raises_value_error_for_typo():
    async def body(conn):
        with pytest.raises(ValueError):
            await resolve_account_id_by_role(conn, TENANT_A, "AR_TRDE")

    _run(_with_su(body))


def test_resolver_raises_value_error_for_empty():
    async def body(conn):
        with pytest.raises(ValueError):
            await resolve_account_id_by_role(conn, TENANT_A, "")

    _run(_with_su(body))


# -----------------------------------------------------------------------------
# Seed function — idempotent + VAT_OUTPUT interim + TIER 3 not seeded
# -----------------------------------------------------------------------------
def test_seed_is_idempotent():
    async def body(conn):
        before = await conn.fetchval(
            "SELECT COUNT(*) FROM account_roles WHERE tenant_id = $1",
            TENANT_A,
        )
        await conn.fetchval("SELECT seed_default_account_roles($1)", TENANT_A)
        await conn.fetchval("SELECT seed_default_account_roles($1)", TENANT_A)
        after = await conn.fetchval(
            "SELECT COUNT(*) FROM account_roles WHERE tenant_id = $1",
            TENANT_A,
        )
        assert before == after

    _run(_with_su(body))


def test_vat_output_is_not_interim_post_d1():
    # V155 Fase D1: VAT_OUTPUT repointed from interim 2-10300 to dedicated 2-10600.
    async def body(conn):
        row = await conn.fetchrow(
            "SELECT is_interim FROM account_roles "
            "WHERE tenant_id = $1 AND role_key = $2",
            TENANT_A,
            AccountRole.VAT_OUTPUT,
        )
        assert row is not None
        assert row["is_interim"] is False, "Post-D1: VAT_OUTPUT must not be interim"

    _run(_with_su(body))


def test_tier3_not_seeded():
    # V155 Fase D1: VAT_INPUT, WHT_PPH_PAYABLE, WHT_PPH_PREPAID promoted to TIER 1.
    # Granular WHT_PPH21/23/4_2/22 retained as reservation (forward-compat, NOT mapped).
    async def body(conn):
        for role in [
            AccountRole.WHT_PPH21,
            AccountRole.INVENTORY_WIP,
            AccountRole.MFG_DIRECT_LABOR,
        ]:
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM account_roles "
                "WHERE tenant_id = $1 AND role_key = $2",
                TENANT_A,
                role,
            )
            assert cnt == 0, f"{role} must NOT be seeded in Fase B"

    _run(_with_su(body))


def test_tier1_seeded_for_all_tenants():
    async def body(conn):
        rows = await conn.fetch(
            "SELECT tenant_id, COUNT(*) AS n FROM account_roles " "GROUP BY tenant_id"
        )
        assert len(rows) >= 1
        for r in rows:
            assert r["n"] >= 12, f"tenant {r['tenant_id']} has only {r['n']} mappings"

    _run(_with_su(body))


# -----------------------------------------------------------------------------
# Law 18 — cannot map to header account
# -----------------------------------------------------------------------------
def test_cannot_map_to_header_account():
    async def body(conn):
        header_id = await conn.fetchval(
            "SELECT id FROM chart_of_accounts "
            "WHERE tenant_id = $1 AND is_header = true LIMIT 1",
            TENANT_A,
        )
        if header_id is None:
            pytest.skip("No header account found for test tenant")
        # Cleanup any prior leftover from a failed run
        await conn.execute(
            "DELETE FROM account_roles WHERE tenant_id=$1 AND role_key=$2",
            TENANT_A,
            AccountRole.AR_ALLOWANCE,
        )
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await conn.execute(
                "INSERT INTO account_roles (tenant_id, role_key, account_id) "
                "VALUES ($1, $2, $3)",
                TENANT_A,
                AccountRole.AR_ALLOWANCE,
                header_id,
            )

    _run(_with_su(body))


# -----------------------------------------------------------------------------
# RLS isolation — tenant A cannot see tenant B's mappings
# -----------------------------------------------------------------------------
def test_rls_isolates_tenants():
    async def body(conn):
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
            visible = await conn.fetch("SELECT DISTINCT tenant_id FROM account_roles")
            tenant_ids = {r["tenant_id"] for r in visible}
            assert tenant_ids <= {
                TENANT_A
            }, f"RLS leak: tenant_A session saw {tenant_ids}"
            assert TENANT_B not in tenant_ids

    _run(_with_rls(body))
