"""Tests for customers.py + vendors.py per-entity OB CoA role-resolver
migration (Fase C1.6 micro).

Coverage:
    1. Both modules import role_resolver helpers + precondition.
    2. CUSTOMERS_REQUIRED_ROLES = [AR_TRADE, EQUITY_OPENING_BALANCE].
    3. VENDORS_REQUIRED_ROLES   = [AP_TRADE, EQUITY_OPENING_BALANCE].
    4. ZERO hardcoded CoA literals in either module (Fase C1.6 DoD).
    5. POST opening-balance handlers wire the precondition gate on
       entry (>=1 _ensure_role_preconditions call site per module).
    6. customers.py reverse path reads back account_id from
       journal_lines (no role_resolver re-resolve) -- Law 2/4
       integrity. This guarantees that even though customers OB equity
       leg has FLIPPED from 3-10100 to 3-50000 go-forward, historical
       reversals continue to mirror the original posting.
    7. The legacy resolve_account / resolve_account_id imports are gone
       (both modules now exclusively use role-based resolution).
    8. Precondition util reports CLEAN for both paths across every
       tenant (live DB).
    9. assert_required_roles_for_path raises when a synthetic gap is
       introduced.
   10. AR_TRADE -> 1-10400, AP_TRADE -> 2-10100, EQUITY_OPENING_BALANCE
       -> 3-50000 (leaf, active, correct account_type) for every
       tenant.
   11. REGRESSION-KILLER: customer OB equity leg now resolves via
       EQUITY_OPENING_BALANCE (3-50000), NOT 3-10100 (Modal Pemilik).
       This is the semantic bug fix that motivated Fase C1.6 micro --
       customers.py historically credited Modal Pemilik for AR opening
       balance which is conceptually wrong (OB should hit Modal Saldo
       Awal so trial balance OB sits in a single equity account
       konsisten dengan vendors.py per-vendor OB).
"""

from __future__ import annotations

import asyncio
import ast
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

SUPERUSER_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL", ""
)


def _require_dsn():
    if not SUPERUSER_DSN:
        pytest.skip(
            "TEST_DATABASE_URL / DATABASE_URL not set; live precondition "
            "checks skipped in this environment"
        )


CUSTOMERS_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/customers.py"
VENDORS_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/vendors.py"


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    _require_dsn()
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# Static guarantees -- customers.py
# ---------------------------------------------------------------------------
def _parse(path):
    src = open(path).read()
    return src, ast.parse(src)


def _required_roles_const(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return node.value
    return None


def test_customers_exports_required_roles_constant():
    src, tree = _parse(CUSTOMERS_PATH)
    found = _required_roles_const(tree, "CUSTOMERS_REQUIRED_ROLES")
    assert found is not None, "CUSTOMERS_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        ), f"unexpected element: {ast.dump(elt)}"
        roles.add(elt.attr)
    assert roles == {"AR_TRADE", "EQUITY_OPENING_BALANCE"}


def test_vendors_exports_required_roles_constant():
    src, tree = _parse(VENDORS_PATH)
    found = _required_roles_const(tree, "VENDORS_REQUIRED_ROLES")
    assert found is not None, "VENDORS_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        ), f"unexpected element: {ast.dump(elt)}"
        roles.add(elt.attr)
    assert roles == {"AP_TRADE", "EQUITY_OPENING_BALANCE"}


def test_customers_imports_role_resolver_and_precondition():
    src, _ = _parse(CUSTOMERS_PATH)
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert "AccountRole" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_vendors_imports_role_resolver_and_precondition():
    src, _ = _parse(VENDORS_PATH)
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert "AccountRole" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_customers_handler_calls_precondition_gate():
    src, _ = _parse(CUSTOMERS_PATH)
    assert src.count("_ensure_role_preconditions(") >= 1, (
        "Precondition gate must be wired into the per-customer OB POST " "handler."
    )


def test_vendors_handler_calls_precondition_gate():
    src, _ = _parse(VENDORS_PATH)
    assert src.count("_ensure_role_preconditions(") >= 1, (
        "Precondition gate must be wired into the per-vendor OB POST " "handler."
    )


def test_customers_no_hardcoded_coa_codes():
    """ZERO hardcoded CoA literals in customers.py (Fase C1.6 DoD)."""
    src = open(CUSTOMERS_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


def test_vendors_no_hardcoded_coa_codes():
    """ZERO hardcoded CoA literals in vendors.py (Fase C1.6 DoD)."""
    src = open(VENDORS_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


def test_legacy_resolve_account_imports_gone():
    """Both modules must NOT import the legacy resolve_account/_id helper
    anymore (sole resolver is role_resolver)."""
    src_c = open(CUSTOMERS_PATH).read()
    src_v = open(VENDORS_PATH).read()
    assert (
        "from ..services.resolve_account import" not in src_c
    ), "customers.py still imports legacy resolve_account helper"
    assert (
        "from ..services.resolve_account import" not in src_v
    ), "vendors.py still imports legacy resolve_account helper"


def test_customers_reverse_path_uses_original_journal_lines_no_resolve():
    """The customer-OB reverse handler must read account_id from
    journal_lines (the original posting) and DOES NOT call
    resolve_account_id_by_role for reversal lines.

    Critical given the Fase C1.6 micro flip: original journals exist
    in BOTH 3-10100 (historical) and 3-50000 (go-forward) -- the
    reversal MUST mirror whichever account the original posting used.
    """
    src = open(CUSTOMERS_PATH).read()
    # The reverse handler SELECTs account_id, debit, credit, memo
    # FROM journal_lines.
    assert re.search(
        r"SELECT\s+account_id,\s*debit,\s*credit,\s*memo\s+FROM\s+journal_lines",
        src,
        re.IGNORECASE,
    ), "Reverse path must read account_id from journal_lines"

    # And the reverse handler must NOT re-resolve via the role helper.
    # Locate the reverse handler block by its decorator and assert no
    # resolve_account_id_by_role call appears inside.
    m = re.search(
        r"@router\.post\(\"/\{customer_id\}/opening-balance/reverse\"\)"
        r".*?(?=@router\.|\Z)",
        src,
        re.DOTALL,
    )
    assert m, "Could not locate customer OB reverse handler block"
    block = m.group(0)
    assert "resolve_account_id_by_role" not in block, (
        "Reverse path must NOT re-resolve role -- account_id must come "
        "from the original journal_lines row."
    )


# ---------------------------------------------------------------------------
# Live precondition check
# ---------------------------------------------------------------------------
def test_precondition_clean_for_customers_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "customers",
                [
                    AccountRole.AR_TRADE,
                    AccountRole.EQUITY_OPENING_BALANCE,
                ],
            )
            assert gaps == {}, f"unexpected mapping gaps (customers): {gaps}"
        finally:
            await pool.close()

    _run(body())


def test_precondition_clean_for_vendors_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "vendors",
                [
                    AccountRole.AP_TRADE,
                    AccountRole.EQUITY_OPENING_BALANCE,
                ],
            )
            assert gaps == {}, f"unexpected mapping gaps (vendors): {gaps}"
        finally:
            await pool.close()

    _run(body())


def test_precondition_assert_raises_on_unmapped_role_customers():
    async def body():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "customers_synthetic",
                    [AccountRole.AR_TRADE, "INVENTORY_WIP"],
                )
        finally:
            await pool.close()

    _run(body())


def test_precondition_assert_raises_on_unmapped_role_vendors():
    async def body():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "vendors_synthetic",
                    [AccountRole.AP_TRADE, "INVENTORY_WIP"],
                )
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# Mapping sanity (regression-killing tests)
# ---------------------------------------------------------------------------
def test_ar_trade_resolves_to_1_10400_for_all_tenants():
    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code, ca.is_active,
                       ca.is_header, ca.account_type
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'AR_TRADE'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "AR_TRADE must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "1-10400", (
                    f"tenant={r['tenant_id']} AR_TRADE expected 1-10400, "
                    f"got {r['account_code']}"
                )
                assert r["is_header"] is False
                assert r["is_active"] is True
                assert r["account_type"] == "RECEIVABLE"
        finally:
            await conn.close()

    _run(body())


def test_ap_trade_resolves_to_2_10100_for_all_tenants():
    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code, ca.is_active,
                       ca.is_header, ca.account_type
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'AP_TRADE'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "AP_TRADE must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "2-10100", (
                    f"tenant={r['tenant_id']} AP_TRADE expected 2-10100, "
                    f"got {r['account_code']}"
                )
                assert r["is_header"] is False
                assert r["is_active"] is True
                assert r["account_type"] == "PAYABLE"
        finally:
            await conn.close()

    _run(body())


def test_equity_opening_balance_resolves_to_3_50000_for_all_tenants():
    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code, ca.is_active,
                       ca.is_header, ca.account_type
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'EQUITY_OPENING_BALANCE'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "EQUITY_OPENING_BALANCE must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "3-50000", (
                    f"tenant={r['tenant_id']} expected 3-50000, got "
                    f"{r['account_code']}"
                )
                assert r["is_header"] is False
                assert r["is_active"] is True
                assert r["account_type"] == "EQUITY"
        finally:
            await conn.close()

    _run(body())


def test_customer_ob_equity_resolves_to_3_50000_not_3_10100():
    """REGRESSION KILLER (Fase C1.6 micro semantic bug fix).

    Customer per-entity OB previously hardcoded the credit leg to
    '3-10100' (Modal Pemilik), which is semantically wrong -- per-entity
    opening balances should hit Modal Saldo Awal (3-50000) so the trial
    balance OB sits in a single equity account konsisten dengan
    vendors.py per-vendor OB.

    Owner decision Opsi A: flip go-forward to EQUITY_OPENING_BALANCE
    role (3-50000). Historical 3-10100 journals are NOT migrated --
    reclassify = tiket terpisah (pola DEP-2604-0001, CN-2604-0005).

    This test enforces the flip: every tenant's
    EQUITY_OPENING_BALANCE role MUST resolve to 3-50000 (NEVER
    3-10100).
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
                WHERE ar.role_key = 'EQUITY_OPENING_BALANCE'
                """
            )
            assert rows, "EQUITY_OPENING_BALANCE must be mapped"
            for r in rows:
                assert r["account_code"] != "3-10100", (
                    f"REGRESSION: tenant={r['tenant_id']} customer OB "
                    "equity reverted to 3-10100 (Modal Pemilik). "
                    "Must stay 3-50000 (Modal Saldo Awal) per Fase "
                    "C1.6 micro Opsi A."
                )
                assert r["account_code"] == "3-50000"
        finally:
            await conn.close()

    _run(body())
