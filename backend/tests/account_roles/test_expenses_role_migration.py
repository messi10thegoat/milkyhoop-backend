"""Tests for expenses.py CoA role-resolver migration (Fase D2.1).

First migrated module in the D2 deferred batch. Validates PKP toggle
pattern end-to-end (PKP tenant emits VAT line; non-PKP skips with 422
when tax_amount > 0). Also locks in the WHT_PPH_PAYABLE bug fix
(previous hardcode pointed to wrong account post-D1 split).

Coverage:
    1. Module imports role_resolver + role_precondition helpers.
    2. EXPENSE_REQUIRED_ROLES = [VAT_INPUT, CASH_GENERAL, AP_TRADE].
    3. ZERO hardcoded CoA literals in the file.
    4. _ensure_role_preconditions wired into create_expense + void_expense.
    5. Precondition CLEAN 5/5 for VAT_INPUT, CASH_GENERAL, AP_TRADE.
    6. WHT_PPH_PAYABLE resolves to 2-10320 (not 2-10300, not 2-10310).
    7. PKP toggle behavior: PKP tenant -> VAT_INPUT id; non-PKP -> None.
    8. Synthetic gate failure raises PreconditionFailedError.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
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

EXPENSES_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/expenses.py"

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
    src = open(EXPENSES_PATH).read()
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


def test_expense_required_roles_constant():
    src, tree = _parse()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "EXPENSE_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "EXPENSE_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        )
        roles.add(elt.attr)
    assert roles == {"VAT_INPUT", "CASH_GENERAL", "AP_TRADE"}


def test_no_hardcoded_coa_codes_in_expenses():
    src = open(EXPENSES_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


def test_no_sql_account_code_filter_literals():
    src = open(EXPENSES_PATH).read()
    bad_eq = re.findall(r"account_code\s*=\s*'[0-9]-[0-9]{4,5}'", src)
    bad_like = re.findall(r"account_code\s+LIKE\s+'[0-9]-[0-9]+%?'", src)
    assert not bad_eq, f"account_code = literal remains: {bad_eq}"
    assert not bad_like, f"account_code LIKE literal remains: {bad_like}"


def test_post_paths_call_precondition_gate():
    """create_expense + void_expense must call _ensure_role_preconditions."""
    src, _ = _parse()
    # 2 call sites minimum.
    assert (
        src.count("await _ensure_role_preconditions(pool)") >= 2
    ), "Precondition gate must be wired into create + void handlers"


# ---------------------------------------------------------------------------
# Live precondition CLEAN
# ---------------------------------------------------------------------------
def test_precondition_clean_for_expenses_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "expenses",
                [
                    AccountRole.VAT_INPUT,
                    AccountRole.CASH_GENERAL,
                    AccountRole.AP_TRADE,
                ],
            )
            assert gaps == {}, f"unexpected mapping gaps: {gaps}"
        finally:
            await pool.close()

    _run(body())


def test_precondition_assert_raises_on_unmapped_role():
    async def body():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "expenses_synthetic",
                    [AccountRole.VAT_INPUT, "INVENTORY_WIP"],
                )
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# D1 contract: WHT_PPH_PAYABLE points to 2-10320 (the bug fix this PR locks in)
# ---------------------------------------------------------------------------
def test_wht_pph_payable_resolves_to_2_10320_all_tenants():
    async def go(conn):
        rows = await conn.fetch(
            """
            SELECT ar.tenant_id, ca.account_code
            FROM account_roles ar
            JOIN chart_of_accounts ca ON ca.id = ar.account_id
            WHERE ar.role_key = 'WHT_PPH_PAYABLE'
              AND ar.tenant_id = ANY($1::text[])
            ORDER BY ar.tenant_id
            """,
            TENANTS,
        )
        assert len(rows) == 5, f"expected 5 tenants, got {len(rows)}"
        for r in rows:
            assert r["account_code"] == "2-10320", (
                f"tenant={r['tenant_id']} expected 2-10320, " f"got {r['account_code']}"
            )

    _run(_with_conn(go))


# ---------------------------------------------------------------------------
# PKP toggle (validator pattern this module pilots for D2)
# ---------------------------------------------------------------------------
def test_pkp_toggle_vat_input_returns_id_for_pkp_tenant():
    async def go(conn):
        result = await resolve_account_id_by_role_if_pkp(
            conn, "grapgrap", AccountRole.VAT_INPUT
        )
        assert result is not None, "PKP tenant must resolve VAT_INPUT"

    _run(_with_conn(go))


def test_pkp_toggle_vat_input_returns_none_for_non_pkp_tenant():
    """Flip is_pkp off in a tx, verify VAT_INPUT returns None, ROLLBACK."""

    async def go(conn):
        async with conn.transaction():
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = false WHERE id = $1', "milkytest"
            )
            vat = await resolve_account_id_by_role_if_pkp(
                conn, "milkytest", AccountRole.VAT_INPUT
            )
            # WHT must NOT be affected by PKP toggle.
            wht = await resolve_account_id_by_role(
                conn, "milkytest", AccountRole.WHT_PPH_PAYABLE
            )
            assert vat is None, "Non-PKP VAT_INPUT must return None"
            assert wht is not None, "WHT_PPH_PAYABLE must be unaffected by PKP"
            # Force rollback so PKP flag is not persisted.
            raise _RollbackSentinel()

    try:
        _run(_with_conn(go))
    except _RollbackSentinel:
        pass


class _RollbackSentinel(Exception):
    pass
