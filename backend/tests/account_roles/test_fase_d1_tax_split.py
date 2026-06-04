"""
Tests for Fase D1 — Tax Split (V154 + V155).

Covers:
  - 4 tax accounts seeded 5/5 with correct type
  - 4 role mappings 5/5, is_interim=false
  - VAT_OUTPUT repointed from interim 2-10300 -> dedicated 2-10600 (regression killer)
  - 2-10310 Utang PPh 21 untouched (payroll boundary guard)
  - WHT_PPH_PAYABLE never points to 2-10310 (payroll boundary)
  - WHT_PPH_PREPAID is ASSET; WHT_PPH_PAYABLE is LIABILITY
  - PKP toggle: resolve_account_id_by_role_if_pkp returns None for non-PKP VAT, id otherwise
  - Python catalog includes 4 promoted roles + reserved WHT granular
  - Precondition CLEAN for 4 deferred modules
  - Idempotency (re-running seed_default_account_roles inserts 0)
"""

import os
import sys
import asyncio
import asyncpg

sys.path.insert(0, "/root/milkyhoop-dev/backend/api_gateway")

from app.services.role_resolver import (  # noqa: E402
    AccountRole,
    is_valid_role,
    resolve_account_id_by_role_if_pkp,
)
from app.services.role_precondition import (  # noqa: E402
    check_required_roles_for_path,
)

SUPERUSER_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL",
    "",  # set TEST_DATABASE_URL env var
)

TENANTS = ["grapgrap", "milkytest", "anthonius-iwan", "ponte-publishing", "potus-id"]

TAX_ACCOUNTS = {
    "2-10600": ("PPN Keluaran", "LIABILITY"),
    "1-10800": ("PPN Masukan", "ASSET"),
    "2-10320": ("Hutang PPh Transaksi", "LIABILITY"),
    "1-10820": ("PPh Dibayar Dimuka", "ASSET"),
}

TAX_ROLES = {
    "VAT_OUTPUT": "2-10600",
    "VAT_INPUT": "1-10800",
    "WHT_PPH_PAYABLE": "2-10320",
    "WHT_PPH_PREPAID": "1-10820",
}


def _run(coro):
    return asyncio.run(coro)


async def _with_conn(callback):
    conn = await asyncpg.connect(SUPERUSER_DSN)
    try:
        return await callback(conn)
    finally:
        await conn.close()


# -----------------------------------------------------------------------------
# Python catalog sync
# -----------------------------------------------------------------------------
def test_catalog_includes_promoted_roles():
    assert is_valid_role(AccountRole.VAT_OUTPUT)
    assert is_valid_role(AccountRole.VAT_INPUT)
    assert is_valid_role(AccountRole.WHT_PPH_PAYABLE)
    assert is_valid_role(AccountRole.WHT_PPH_PREPAID)


def test_catalog_retains_wht_granular_reservation():
    # Forward-compat per Q2 — granular still in catalog even though not mapped.
    assert is_valid_role(AccountRole.WHT_PPH21)
    assert is_valid_role(AccountRole.WHT_PPH23)
    assert is_valid_role(AccountRole.WHT_PPH4_2)
    assert is_valid_role(AccountRole.WHT_PPH22)


# -----------------------------------------------------------------------------
# DB seed verification (5/5)
# -----------------------------------------------------------------------------
def test_tax_accounts_seeded_5_of_5():
    async def go(conn):
        for code, (name, atype) in TAX_ACCOUNTS.items():
            rows = await conn.fetch(
                "SELECT tenant_id, name, account_type, is_header, is_active "
                "FROM chart_of_accounts WHERE account_code = $1 "
                "  AND tenant_id = ANY($2::text[])",
                code,
                TENANTS,
            )
            assert len(rows) == 5, f"{code}: expected 5 tenants, got {len(rows)}"
            for r in rows:
                assert (
                    r["account_type"] == atype
                ), f"{code}/{r['tenant_id']}: type mismatch"
                assert r["is_header"] is False, f"{code}: is_header must be false"
                assert r["is_active"] is True, f"{code}: must be active"

    _run(_with_conn(go))


def test_tax_roles_mapped_5_of_5_not_interim():
    async def go(conn):
        for role, expected_code in TAX_ROLES.items():
            rows = await conn.fetch(
                "SELECT ar.tenant_id, ca.account_code, ar.is_interim "
                "FROM account_roles ar JOIN chart_of_accounts ca ON ca.id = ar.account_id "
                "WHERE ar.role_key = $1 AND ar.tenant_id = ANY($2::text[])",
                role,
                TENANTS,
            )
            assert len(rows) == 5, f"{role}: expected 5 tenants, got {len(rows)}"
            for r in rows:
                assert r["account_code"] == expected_code, (
                    f"{role}/{r['tenant_id']}: points to {r['account_code']}, "
                    f"expected {expected_code}"
                )
                assert (
                    r["is_interim"] is False
                ), f"{role}/{r['tenant_id']}: is_interim must be false"

    _run(_with_conn(go))


def test_vat_output_repointed_not_10300():
    """Regression killer — VAT_OUTPUT must NEVER point to 2-10300 anymore."""

    async def go(conn):
        rows = await conn.fetch(
            "SELECT ar.tenant_id FROM account_roles ar "
            "JOIN chart_of_accounts ca ON ca.id = ar.account_id "
            "WHERE ar.role_key = 'VAT_OUTPUT' AND ca.account_code = '2-10300'"
        )
        assert (
            rows == []
        ), f"VAT_OUTPUT still points to 2-10300 in: {[r['tenant_id'] for r in rows]}"

    _run(_with_conn(go))


# -----------------------------------------------------------------------------
# Payroll boundary guards
# -----------------------------------------------------------------------------
def test_payroll_account_2_10310_untouched():
    """2-10310 Utang PPh 21 must remain payroll-exclusive (V154 must not modify)."""

    async def go(conn):
        rows = await conn.fetch(
            "SELECT tenant_id, name FROM chart_of_accounts WHERE account_code = '2-10310'"
        )
        # Pre-existing tenants (4 had it; potus-id did not). V154 must NOT have
        # created/seeded/modified this account.
        for r in rows:
            assert (
                r["name"] == "Utang PPh 21"
            ), f"{r['tenant_id']}: 2-10310 name changed to {r['name']!r}"

    _run(_with_conn(go))


def test_wht_pph_payable_never_points_to_10310():
    """WHT_PPH_PAYABLE must point to 2-10320 (transaction), never 2-10310 (payroll)."""

    async def go(conn):
        rows = await conn.fetch(
            "SELECT ar.tenant_id FROM account_roles ar "
            "JOIN chart_of_accounts ca ON ca.id = ar.account_id "
            "WHERE ar.role_key = 'WHT_PPH_PAYABLE' AND ca.account_code = '2-10310'"
        )
        assert rows == [], (
            f"WHT_PPH_PAYABLE leaks to payroll account 2-10310 in: "
            f"{[r['tenant_id'] for r in rows]}"
        )

    _run(_with_conn(go))


# -----------------------------------------------------------------------------
# PKP toggle (resolve_account_id_by_role_if_pkp)
# -----------------------------------------------------------------------------
def test_pkp_toggle_vat_returns_id_for_pkp_tenant():
    async def go(conn):
        # grapgrap is PKP=true (default)
        result = await resolve_account_id_by_role_if_pkp(conn, "grapgrap", "VAT_OUTPUT")
        assert result is not None

    _run(_with_conn(go))


def test_pkp_toggle_vat_returns_none_for_non_pkp_tenant():
    async def go(conn):
        async with conn.transaction():
            # Flip is_pkp off temporarily, then ROLLBACK
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = false WHERE id = $1', "milkytest"
            )
            result_vat_out = await resolve_account_id_by_role_if_pkp(
                conn, "milkytest", "VAT_OUTPUT"
            )
            result_vat_in = await resolve_account_id_by_role_if_pkp(
                conn, "milkytest", "VAT_INPUT"
            )
            # WHT roles NOT affected by PKP toggle
            result_wht = await resolve_account_id_by_role_if_pkp(
                conn, "milkytest", "WHT_PPH_PAYABLE"
            )
            result_ar = await resolve_account_id_by_role_if_pkp(
                conn, "milkytest", "AR_TRADE"
            )
            assert result_vat_out is None, "Non-PKP VAT_OUTPUT must return None"
            assert result_vat_in is None, "Non-PKP VAT_INPUT must return None"
            assert result_wht is not None, "WHT must NOT be affected by PKP toggle"
            assert result_ar is not None, "AR_TRADE must NOT be affected by PKP toggle"
            raise asyncpg.exceptions.QueryCanceledError("rollback")

    try:
        _run(_with_conn(go))
    except asyncpg.exceptions.QueryCanceledError:
        pass


# -----------------------------------------------------------------------------
# Precondition CLEAN for 4 deferred modules
# -----------------------------------------------------------------------------
def test_precondition_bills_service_clean():
    async def go():
        pool = await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "bills_service",
                ["AP_TRADE", "VAT_INPUT", "WHT_PPH_PAYABLE", "INVENTORY_MERCHANDISE"],
            )
        finally:
            await pool.close()
        assert gaps == {}, f"bills_service gaps: {gaps}"

    _run(go())


def test_precondition_vendor_credits_clean():
    async def go():
        pool = await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "vendor_credits",
                ["AP_TRADE", "VAT_INPUT", "COGS_PURCHASE_RETURN"],
            )
        finally:
            await pool.close()
        assert gaps == {}, f"vendor_credits gaps: {gaps}"

    _run(go())


def test_precondition_receive_payments_clean():
    async def go():
        pool = await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "receive_payments",
                ["AR_TRADE", "CASH_GENERAL", "WHT_PPH_PREPAID"],
            )
        finally:
            await pool.close()
        assert gaps == {}, f"receive_payments gaps: {gaps}"

    _run(go())


def test_precondition_expenses_clean():
    async def go():
        pool = await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "expenses",
                ["VAT_INPUT", "CASH_GENERAL", "AP_TRADE"],
            )
        finally:
            await pool.close()
        assert gaps == {}, f"expenses gaps: {gaps}"

    _run(go())


# -----------------------------------------------------------------------------
# Idempotency
# -----------------------------------------------------------------------------
def test_seed_idempotent_zero_new_inserts():
    """Re-running seed_default_account_roles must insert 0 (already-mapped)."""

    async def go(conn):
        for tid in TENANTS:
            n = await conn.fetchval("SELECT seed_default_account_roles($1)", tid)
            assert n == 0, f"{tid}: re-seed inserted {n} new rows (should be 0)"

    _run(_with_conn(go))
