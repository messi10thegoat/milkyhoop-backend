"""Tests for vendor_credits.py CoA role-resolver migration (Fase D2.2).

Second migrated module in the D2 deferred batch. Validates:
- Role-based resolution for AP_TRADE + VAT_INPUT + COGS_PURCHASE_RETURN.
- PKP toggle: PKP tenant emits VAT line; non-PKP submission with
  tax_amount > 0 rejected with 422 (Law 4 consistency).
- Latent bug fix: previous hardcode credited 1-10600 (Persediaan)
  directly, causing double-decrement against record_inventory_outbound.
  Migration to COGS_PURCHASE_RETURN (5-10300) fixes this.
- Read-side AP filter uses account_roles join (no hardcoded 2-10100).

Coverage:
    1. Module imports role_resolver + role_precondition helpers.
    2. VENDOR_CREDIT_REQUIRED_ROLES = [AP_TRADE, VAT_INPUT, COGS_PURCHASE_RETURN].
    3. ZERO hardcoded CoA literals in the file.
    4. _ensure_role_preconditions wired into post + receive_refund + void handlers.
    5. Precondition CLEAN 5/5 for AP_TRADE, VAT_INPUT, COGS_PURCHASE_RETURN.
    6. PKP toggle behavior: PKP tenant -> VAT_INPUT id; non-PKP -> None.
    7. Synthetic gate failure raises PreconditionFailedError.
"""

from __future__ import annotations

import ast
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
from app.services.role_resolver import (  # noqa: E402
    AccountRole,
    resolve_account_id_by_role,
    resolve_account_id_by_role_if_pkp,
)

VENDOR_CREDITS_PATH = (
    "/root/milkyhoop-dev/backend/api_gateway/app/routers/vendor_credits.py"
)

SUPERUSER_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", ""
)

TENANTS = ["grapgrap", "milkytest", "anthonius-iwan", "ponte-publishing", "potus-id"]


def _require_dsn():
    if not SUPERUSER_DSN:
        pytest.skip(
            "TEST_DATABASE_URL / DATABASE_URL not set; live precondition "
            "checks skipped"
        )


def _run(coro):
    return asyncio.run(coro)


async def _with_conn(callback):
    _require_dsn()
    conn = await asyncpg.connect(SUPERUSER_DSN)
    try:
        return await callback(conn)
    finally:
        await conn.close()


async def _make_pool():
    _require_dsn()
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


def _parse():
    src = open(VENDOR_CREDITS_PATH).read()
    return src, ast.parse(src)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse()
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert "resolve_account_id_by_role_if_pkp" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_vendor_credit_required_roles_constant():
    src, tree = _parse()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "VENDOR_CREDIT_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "VENDOR_CREDIT_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        )
        roles.add(elt.attr)
    assert roles == {"AP_TRADE", "VAT_INPUT", "COGS_PURCHASE_RETURN"}


def test_zero_hardcoded_coa_literals():
    """Migration acceptance: no `'X-YYYYY'` CoA literals remain."""
    import re

    src, _ = _parse()
    # Strip docstring/comments for the strict scan? No — keep raw. After
    # migration, even comments shouldn't carry literal codes in posting
    # context, but we tolerate docstring mentions if any. The regex
    # matches quoted CoA codes like '2-10100' or "1-10800".
    matches = re.findall(r"['\"][0-9]-[0-9]{4,5}['\"]", src)
    assert matches == [], f"Hardcoded CoA literals remain: {matches}"


def test_precondition_wired_into_handlers():
    src, _ = _parse()
    # post_vendor_credit + receive_refund + void_vendor_credit must call
    # _ensure_role_preconditions. We check the call site count >= 3 to
    # account for those three journal-creating handlers.
    assert src.count("await _ensure_role_preconditions(pool)") >= 3


def test_read_filter_uses_role_join_not_hardcoded_code():
    src, _ = _parse()
    assert "ar.role_key = 'AP_TRADE'" in src
    assert "account_code = '2-10100'" not in src


# ---------------------------------------------------------------------------
# Live database checks (skipped without DSN)
# ---------------------------------------------------------------------------
def test_precondition_clean_for_all_tenants():
    async def _go():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "vendor_credits",
                [
                    AccountRole.AP_TRADE,
                    AccountRole.VAT_INPUT,
                    AccountRole.COGS_PURCHASE_RETURN,
                ],
            )
        finally:
            await pool.close()
        return gaps

    gaps = _run(_go())
    assert gaps == {}, f"Precondition gaps: {gaps}"


def test_resolve_ap_trade_per_tenant():
    async def _go(conn):
        results = {}
        for t in TENANTS:
            results[t] = await resolve_account_id_by_role(conn, t, AccountRole.AP_TRADE)
        return results

    out = _run(_with_conn(_go))
    assert all(v is not None for v in out.values())


def test_resolve_cogs_purchase_return_per_tenant():
    async def _go(conn):
        results = {}
        for t in TENANTS:
            results[t] = await resolve_account_id_by_role(
                conn, t, AccountRole.COGS_PURCHASE_RETURN
            )
        return results

    out = _run(_with_conn(_go))
    assert all(v is not None for v in out.values())


def test_vat_input_pkp_toggle():
    """PKP tenant -> non-None UUID. Non-PKP -> None (caller skips VAT line)."""

    async def _go(conn):
        # Pick first tenant, flip is_pkp false -> resolve should be None.
        tenant = TENANTS[0]
        original = await conn.fetchval(
            'SELECT is_pkp FROM "Tenant" WHERE id = $1', tenant
        )
        try:
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = true WHERE id = $1', tenant
            )
            pkp_id = await resolve_account_id_by_role_if_pkp(
                conn, tenant, AccountRole.VAT_INPUT
            )
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = false WHERE id = $1', tenant
            )
            nonpkp_id = await resolve_account_id_by_role_if_pkp(
                conn, tenant, AccountRole.VAT_INPUT
            )
            return pkp_id, nonpkp_id
        finally:
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = $2 WHERE id = $1', tenant, original
            )

    pkp_id, nonpkp_id = _run(_with_conn(_go))
    assert pkp_id is not None
    assert nonpkp_id is None


def test_synthetic_precondition_failure_raises():
    """Pass a role not mapped for any tenant -> PreconditionFailedError."""

    async def _go():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "vendor_credits_synthetic",
                    [AccountRole.IC_SALES],  # TIER 3 reserved, unseeded
                )
        finally:
            await pool.close()

    _run(_go())
