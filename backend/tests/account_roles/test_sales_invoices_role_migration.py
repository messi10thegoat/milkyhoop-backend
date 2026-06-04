"""Tests for sales_invoices.py CoA role-resolver migration (Fase C1.1).

Coverage:
    1. Module imports role_resolver helpers + precondition.
    2. SALES_INVOICE_REQUIRED_ROLES contains the 5 critical roles.
    3. ZERO hardcoded CoA literals in WRITE paths
       (Fase C1.1 addendum V151+V152 promoted REVENUE_DEFERRED to TIER 1
       so the last literal 2-10750 is gone — all CoA references resolve
       via role_resolver).
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
        "REVENUE_DEFERRED",
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
    """ZERO hardcoded CoA literals in sales_invoices.py (Fase C1.1 final DoD).

    Fase C1.1 addendum (V151+V152) promoted REVENUE_DEFERRED to TIER 1,
    eliminating the last literal (2-10750 in _resolve_unearned_revenue).
    Every CoA reference must now go through resolve_account_id_by_role().
    """
    src = open(SI_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


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
                    AccountRole.REVENUE_DEFERRED,
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
def test_vat_output_resolves_to_2_10600_for_all_tenants():
    """V155 Fase D1 contract: VAT_OUTPUT → 2-10600 (PPN Keluaran, dedicated).

    Was interim 2-10300 in Fase C1.1; V155 repointed to dedicated PPN Keluaran
    and backfilled missing accounts in 3 tenants.
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
                assert r["account_code"] == "2-10600", (
                    f"tenant={r['tenant_id']} expected 2-10600 (V155 D1), "
                    f"got {r['account_code']}"
                )
        finally:
            await conn.close()

    _run(body())


# ---------------------------------------------------------------------------
# REVENUE_DEFERRED mapping sanity (Fase C1.1 addendum — TIER 1 promotion)
# ---------------------------------------------------------------------------
def test_revenue_deferred_resolves_to_2_10750_for_all_tenants():
    """Fase C1.1 addendum (V151+V152): REVENUE_DEFERRED -> 2-10750 for every tenant.

    REVENUE_DEFERRED is the core PSAK 72 contract liability of the 3-event
    model (V137): billing credits it, revenue debits it. Promoted from
    FUTURE RESERVATION to TIER 1 to close the last sales_invoices.py
    hardcoded literal.
    """

    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code, ar.is_interim
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'REVENUE_DEFERRED'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "REVENUE_DEFERRED must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "2-10750", (
                    f"tenant={r['tenant_id']} expected 2-10750, "
                    f"got {r['account_code']}"
                )
                assert r["is_interim"] is False, (
                    f"tenant={r['tenant_id']} REVENUE_DEFERRED must NOT be interim "
                    f"(TIER 1 promotion is final)"
                )
        finally:
            await conn.close()

    _run(body())
