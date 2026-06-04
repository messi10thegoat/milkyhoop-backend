"""Tests for sales_invoices.py CoA role-resolver migration (Fase C1.1).

Coverage:
    1. Module imports role_resolver helpers + precondition.
    2. SALES_INVOICE_REQUIRED_ROLES contains the 5 critical roles.
    3. No hardcoded CoA literals in WRITE paths
       (the only allowed remaining literal is the 2-10750 unearned-revenue
       helper, which lives in read-style account lookup that is out of
       scope of Fase C1.1 because REVENUE_DEFERRED is a FUTURE role
       not yet seeded in account_roles).
    4. No `account_code = '1-104xx'` or `LIKE '1-104%'` filters remain.
    5. Precondition util reports CLEAN for the 5 required roles across
       every tenant (live DB).
    6. assert_required_roles_for_path raises when a synthetic gap is
       introduced (mocked via a deliberately-unmapped role).
"""

from __future__ import annotations

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
from app.services.role_resolver import AccountRole  # noqa: E402

# NOTE: cannot import app.routers.sales_invoices at test-collection time
# because the router has a deferred mid-file import of weasyprint which
# is not installed in the test environment. We assert the migration by
# parsing the file's AST + source text instead.
import ast  # noqa: E402

# Local dev / CI must export TEST_DATABASE_URL. Sibling test
# test_role_precondition.py uses the same env var.
SUPERUSER_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", ""
)


def _require_dsn():
    if not SUPERUSER_DSN:
        pytest.skip(
            "TEST_DATABASE_URL / DATABASE_URL not set; live precondition "
            "checks skipped in this environment"
        )


SI_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/sales_invoices.py"


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    _require_dsn()
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def _parse_si():
    src = open(SI_PATH).read()
    return src, ast.parse(src)


def test_module_exports_required_roles_constant():
    """Find SALES_INVOICE_REQUIRED_ROLES = [...] in the AST."""
    src, tree = _parse_si()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "SALES_INVOICE_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "SALES_INVOICE_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    # Each element is AccountRole.X
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        ), f"unexpected element: {ast.dump(elt)}"
        roles.add(elt.attr)
    assert roles == {
        "AR_TRADE",
        "REVENUE_SALES_GOODS",
        "COGS_SALES",
        "INVENTORY_MERCHANDISE",
        "VAT_OUTPUT",
    }


def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse_si()
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_post_path_calls_precondition_gate():
    """The 3 posting handlers must call _ensure_role_preconditions."""
    src, _ = _parse_si()
    # Three call sites: create_invoice, post_invoice, record_payment.
    assert (
        src.count("await _ensure_role_preconditions(pool)") >= 3
    ), "Precondition gate must be wired into create/post/payment handlers"


def test_no_hardcoded_coa_codes_in_write_paths():
    """Only one literal allowed: 2-10750 inside _resolve_unearned_revenue.

    REVENUE_DEFERRED is reserved as a FUTURE role but not yet seeded into
    account_roles, so the unearned-revenue resolution stays as direct CoA
    lookup until Fase D. Every other CoA literal must be gone.
    """
    src = open(SI_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    # Exactly one occurrence allowed and it must be 2-10750.
    assert literals.count("2-10750") == 1, (
        f"Expected 1 occurrence of 2-10750 (unearned-revenue helper), "
        f"got {literals.count('2-10750')}"
    )
    other = [c for c in literals if c != "2-10750"]
    assert not other, f"Unexpected hardcoded CoA literals: {other}"


def test_no_sql_account_code_filter_literals():
    """READ paths must filter by account_type, not account_code='1-10xxx'."""
    src = open(SI_PATH).read()
    # Direct equality literal in SQL
    bad_eq = re.findall(r"account_code\s*=\s*'[0-9]-[0-9]{4,5}'", src)
    # LIKE pattern for AR family
    bad_like = re.findall(r"account_code\s+LIKE\s+'[0-9]-[0-9]+%?'", src)
    assert not bad_eq, f"account_code = literal remains: {bad_eq}"
    assert not bad_like, f"account_code LIKE literal remains: {bad_like}"


# ---------------------------------------------------------------------------
# Live precondition check
# ---------------------------------------------------------------------------
def test_precondition_clean_for_sales_invoice_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "sales_invoices",
                [
                    AccountRole.AR_TRADE,
                    AccountRole.REVENUE_SALES_GOODS,
                    AccountRole.COGS_SALES,
                    AccountRole.INVENTORY_MERCHANDISE,
                    AccountRole.VAT_OUTPUT,
                ],
            )
            assert gaps == {}, f"unexpected mapping gaps: {gaps}"
        finally:
            await pool.close()

    _run(body())


def test_precondition_assert_raises_on_unmapped_role():
    """Sanity: the gate fails loud if any required role is unmapped.

    We deliberately ask for INVENTORY_WIP (FUTURE, never seeded) to
    confirm assert_required_roles_for_path raises PreconditionFailedError
    rather than silently degrading.
    """

    async def body():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "sales_invoices_synthetic",
                    [AccountRole.AR_TRADE, "INVENTORY_WIP"],
                )
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# VAT_OUTPUT mapping sanity (the migration's interim contract)
# ---------------------------------------------------------------------------
def test_vat_output_resolves_to_2_10300_for_all_tenants():
    """Fase C1.1 interim contract: VAT_OUTPUT → 2-10300 (Hutang Pajak).

    Old hardcode used 2-10600 which did NOT exist for 3/5 tenants
    (anthonius-iwan, ponte-publishing, potus-id). The role-based mapping
    fixes that latent posting failure.
    """

    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'VAT_OUTPUT'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "VAT_OUTPUT must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "2-10300", (
                    f"tenant={r['tenant_id']} expected 2-10300 (interim), "
                    f"got {r['account_code']}"
                )
        finally:
            await conn.close()

    _run(body())
