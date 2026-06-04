"""Tests for customer_deposits.py CoA role-resolver migration (Fase C1.4).

Coverage:
    1. Module imports role_resolver helpers + precondition.
    2. CUSTOMER_DEPOSITS_REQUIRED_ROLES contains the 3 required roles.
       BANK_OPERATIONAL deliberately EXCLUDED (fallback-only consistency
       with Fase C1.2/C1.3).
    3. ZERO hardcoded CoA literals in the module (Fase C1.4 DoD).
    4. No `account_code = '...'` filters remain (read filter migrated to
       account_type = 'RECEIVABLE').
    5. Legacy constants AR_ACCOUNT_CODE / CUSTOMER_DEPOSIT_ACCOUNT_CODE
       removed (the 1-10300 = Kas Kecil bug eliminated at source).
    6. Posting handlers wire the precondition gate on entry.
    7. NULL guard 422 fires for missing dep account_id on _post_deposit.
    8. Precondition util reports CLEAN for the 3 required roles across
       every tenant (live DB).
    9. assert_required_roles_for_path raises when a synthetic gap is
       introduced.
   10. CUSTOMER_DEPOSIT_LIABILITY resolves to 2-10500 for every tenant.
   11. AR_TRADE resolves to 1-10400 (Piutang Usaha) -- NOT 1-10300 (Kas
       Kecil) -- proving the legacy bug is dead.

Note: customer_deposits apply-deposit path settles AR_TRADE
(Dr CUSTOMER_DEPOSIT_LIABILITY -> Cr AR_TRADE). It does NOT touch
WHT/VAT (PPN is recorded at invoice posting, not at deposit application).
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


CD_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/customer_deposits.py"


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    _require_dsn()
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def _parse_cd():
    src = open(CD_PATH).read()
    return src, ast.parse(src)


def test_module_exports_required_roles_constant():
    """CUSTOMER_DEPOSITS_REQUIRED_ROLES = [...] in the AST."""
    src, tree = _parse_cd()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "CUSTOMER_DEPOSITS_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "CUSTOMER_DEPOSITS_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        ), f"unexpected element: {ast.dump(elt)}"
        roles.add(elt.attr)
    assert roles == {
        "CUSTOMER_DEPOSIT_LIABILITY",
        "AR_TRADE",
        "CASH_GENERAL",
    }
    assert (
        "BANK_OPERATIONAL" not in roles
    ), "BANK_OPERATIONAL must remain fallback-only (Fase C1.2/C1.3 pattern)."


def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse_cd()
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_handlers_call_precondition_gate():
    """All 4 journal-creating handlers must call _ensure_role_preconditions."""
    src, _ = _parse_cd()
    # Expect >= 4 calls: create (auto_post), post, apply, refund, void.
    assert src.count("_ensure_role_preconditions(") >= 4, (
        "Precondition gate must be wired into every journal-creating handler "
        "(create+auto_post, post, apply, refund, void)"
    )


def test_no_hardcoded_coa_codes():
    """ZERO hardcoded CoA literals in customer_deposits.py (Fase C1.4 DoD)."""
    src = open(CD_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


def test_no_sql_account_code_filter_literals():
    src = open(CD_PATH).read()
    bad_eq = re.findall(r"account_code\s*=\s*'[0-9]-[0-9]{4,5}'", src)
    bad_like = re.findall(r"account_code\s+LIKE\s+'[0-9]-[0-9]+%?'", src)
    assert not bad_eq, f"account_code = literal remains: {bad_eq}"
    assert not bad_like, f"account_code LIKE literal remains: {bad_like}"


def test_legacy_buggy_constant_removed():
    """The legacy AR_ACCOUNT_CODE assignment (1-10300 = Kas Kecil) must
    be deleted. A comment may reference it for migration history, but no
    assignment statement must remain."""
    src, tree = _parse_cd()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id
            in {"AR_ACCOUNT_CODE", "CUSTOMER_DEPOSIT_ACCOUNT_CODE"}
        ):
            raise AssertionError(
                f"Legacy hardcoded-code constant {node.targets[0].id} still "
                "assigned. Fase C1.4 deletes both."
            )


def test_null_guard_raises_422_on_missing_dep_account_id():
    """_post_deposit must raise 422 (not silent-skip) when dep['account_id']
    is NULL. Static check that the 422 raise sits in the post path."""
    src = open(CD_PATH).read()
    assert (
        "Akun kas/bank tidak tersedia untuk customer deposit" in src
    ), "NULL guard 422 raise missing -- Dr Cash may be silent-skipped"


def test_old_resolve_account_id_removed():
    """The legacy resolve_account_id (code-based) must no longer be
    called -- all 4 call sites migrated to resolve_account_id_by_role."""
    src = open(CD_PATH).read()
    # Allow the substring resolve_account_id_by_role; disallow bare call.
    # Pattern: `resolve_account_id(` but NOT followed by `_by_role` markers
    # is a bare legacy call.
    bare = re.findall(r"resolve_account_id\(", src)
    assert not bare, (
        f"Legacy resolve_account_id() call(s) remain: {len(bare)}. "
        "Migrate to resolve_account_id_by_role()."
    )


# ---------------------------------------------------------------------------
# Live precondition check
# ---------------------------------------------------------------------------
def test_precondition_clean_for_customer_deposits_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "customer_deposits",
                [
                    AccountRole.CUSTOMER_DEPOSIT_LIABILITY,
                    AccountRole.AR_TRADE,
                    AccountRole.CASH_GENERAL,
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
                    "customer_deposits_synthetic",
                    [AccountRole.CUSTOMER_DEPOSIT_LIABILITY, "INVENTORY_WIP"],
                )
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# Mapping sanity (the regression-killing tests)
# ---------------------------------------------------------------------------
def test_customer_deposit_liability_resolves_to_2_10500_for_all_tenants():
    """CUSTOMER_DEPOSIT_LIABILITY -> 2-10500 (Uang Muka Pelanggan)."""

    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'CUSTOMER_DEPOSIT_LIABILITY'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "CUSTOMER_DEPOSIT_LIABILITY must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "2-10500", (
                    f"tenant={r['tenant_id']} expected 2-10500, "
                    f"got {r['account_code']}"
                )
        finally:
            await conn.close()

    _run(body())


def test_ar_trade_resolves_to_1_10400_not_1_10300():
    """AR_TRADE -> 1-10400 (Piutang Usaha) for every tenant.

    Regression test for the legacy AR_ACCOUNT_CODE = '1-10300' bug.
    Code 1-10300 is Kas Kecil (petty cash), NOT AR. If any tenant
    resolves AR_TRADE to 1-10300 the legacy bug would still credit
    Kas Kecil on apply-deposit-to-invoice instead of clearing the
    receivable. This test guarantees the role mapping points at the
    correct CoA.
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
                WHERE ar.role_key = 'AR_TRADE'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "AR_TRADE must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "1-10400", (
                    f"tenant={r['tenant_id']} expected 1-10400 (Piutang Usaha), "
                    f"got {r['account_code']}. If this is 1-10300 the "
                    "legacy DEP apply bug is alive."
                )
                assert r["account_code"] != "1-10300", (
                    f"REGRESSION: AR_TRADE for tenant {r['tenant_id']} "
                    "resolves to 1-10300 (Kas Kecil). The legacy "
                    "AR_ACCOUNT_CODE='1-10300' bug is back."
                )
        finally:
            await conn.close()

    _run(body())
