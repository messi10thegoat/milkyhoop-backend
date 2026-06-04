"""Tests for opening_balance.py CoA role-resolver migration (Fase C1.5).

Coverage:
    1. Module imports role_resolver helpers + precondition.
    2. OPENING_BALANCE_REQUIRED_ROLES contains the 4 required roles.
    3. ZERO hardcoded CoA literals in the module (Fase C1.5 DoD).
    4. No `account_code = '...'` filters remain in code (resolver only).
    5. POST/PUT/validate handlers wire the precondition gate on entry.
    6. The reverse/supersede path reads back account_id from
       journal_lines (no role_resolver re-resolve) -- Law 2/4 integrity.
    7. App-level single-OB invariant still present (status='ACTIVE'
       guard at POST entry; DB trigger removed per Law 28).
    8. Precondition util reports CLEAN for the 4 required roles across
       every tenant (live DB).
    9. assert_required_roles_for_path raises when a synthetic gap is
       introduced.
   10. EQUITY_OPENING_BALANCE resolves to 3-50000 (leaf, is_header=false,
       is_active=true) for every tenant.
   11. AR_TRADE resolves to 1-10400 (Piutang Usaha), NOT 1-10300 (Kas
       Kecil) -- regression test for Fase A finding.
   12. INVENTORY_MERCHANDISE resolves to 1-10600 (Persediaan Barang
       Dagangan), NOT 1-10400 (Piutang Usaha) -- regression test for
       Fase A finding.
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


OB_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/opening_balance.py"


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    _require_dsn()
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def _parse_ob():
    src = open(OB_PATH).read()
    return src, ast.parse(src)


def test_module_exports_required_roles_constant():
    """OPENING_BALANCE_REQUIRED_ROLES = [...] in the AST."""
    src, tree = _parse_ob()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "OPENING_BALANCE_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "OPENING_BALANCE_REQUIRED_ROLES not defined"
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
        "EQUITY_OPENING_BALANCE",
        "AR_TRADE",
        "AP_TRADE",
        "INVENTORY_MERCHANDISE",
    }


def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse_ob()
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert "AccountRole" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_handlers_call_precondition_gate():
    """POST, PUT, and POST /validate handlers must wire the gate.

    Expect >= 3 _ensure_role_preconditions() call sites.
    """
    src, _ = _parse_ob()
    assert src.count("_ensure_role_preconditions(") >= 3, (
        "Precondition gate must be wired into POST, PUT, and /validate " "handlers."
    )


def test_no_hardcoded_coa_codes():
    """ZERO hardcoded CoA literals in opening_balance.py (Fase C1.5 DoD).

    Pattern: anything like "3-50000" / '1-10300' that looks like a CoA
    account_code in a string literal.
    """
    src = open(OB_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


def test_no_sql_account_code_filter_literals():
    src = open(OB_PATH).read()
    bad_eq = re.findall(r"account_code\s*=\s*'[0-9]-[0-9]{4,5}'", src)
    bad_like = re.findall(r"account_code\s+LIKE\s+'[0-9]-[0-9]+%?'", src)
    assert not bad_eq, f"account_code = literal remains: {bad_eq}"
    assert not bad_like, f"account_code LIKE literal remains: {bad_like}"


def test_reverse_path_uses_original_journal_lines_no_resolve():
    """The supersede/reverse code path reads back account_id from
    journal_lines (the original posting) and DOES NOT call
    resolve_account_id_by_role for reversal lines.

    This guarantees that if a tenant remaps EQUITY_OPENING_BALANCE
    between original posting and supersede, the reversal still mirrors
    the original posting (Law 2 immutability + Law 4 balance).
    """
    src = open(OB_PATH).read()

    # The supersede code must SELECT account_id FROM journal_lines for
    # the reversal.
    assert re.search(
        r"SELECT\s+account_id,\s*debit,\s*credit,\s*memo\s*\n\s*FROM\s+journal_lines",
        src,
        re.IGNORECASE,
    ), "Reverse path must read account_id from journal_lines"

    # Mention the explicit "no re-resolve" intent in a comment near the
    # reversal block. If a future refactor removes this comment AND
    # adds a resolver call inside the reversal-line build, this guard
    # must fire.
    assert (
        "DO NOT re-resolve" in src or "no re-resolve" in src.lower()
    ), "Reverse path must document that it skips role resolution."


def test_single_ob_invariant_app_guard_present():
    """App-level single-active-OB invariant: POST handler must check
    EXISTS(... WHERE status='ACTIVE') and 400 if found.

    DB trigger guard_opening_balance was removed per Law 28, so the app
    is the sole enforcer.
    """
    src = open(OB_PATH).read()
    assert re.search(
        r"opening_balance_records\s+WHERE\s+tenant_id\s*=\s*\$1\s+AND\s+status\s*=\s*'ACTIVE'",
        src,
    ), "Single-OB invariant guard query missing or rewritten"
    assert (
        "Active opening balance already exists" in src
    ), "Single-OB invariant 400 detail message missing"


# ---------------------------------------------------------------------------
# Live precondition check
# ---------------------------------------------------------------------------
def test_precondition_clean_for_opening_balance_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "opening_balance",
                [
                    AccountRole.EQUITY_OPENING_BALANCE,
                    AccountRole.AR_TRADE,
                    AccountRole.AP_TRADE,
                    AccountRole.INVENTORY_MERCHANDISE,
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
                    "opening_balance_synthetic",
                    [AccountRole.EQUITY_OPENING_BALANCE, "INVENTORY_WIP"],
                )
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# Mapping sanity (regression-killing tests)
# ---------------------------------------------------------------------------
def test_equity_opening_balance_resolves_to_3_50000_leaf_for_all_tenants():
    """EQUITY_OPENING_BALANCE -> 3-50000 (Modal Saldo Awal), leaf
    (is_header=false), active, EQUITY type, for every tenant."""

    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code, ca.is_header,
                       ca.is_active, ca.account_type
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'EQUITY_OPENING_BALANCE'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "EQUITY_OPENING_BALANCE must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "3-50000", (
                    f"tenant={r['tenant_id']} expected 3-50000, "
                    f"got {r['account_code']}"
                )
                assert r["is_header"] is False, (
                    f"tenant={r['tenant_id']} mapped to header account; " "must be leaf"
                )
                assert (
                    r["is_active"] is True
                ), f"tenant={r['tenant_id']} mapped to inactive account"
                assert r["account_type"] == "EQUITY", (
                    f"tenant={r['tenant_id']} mapped to non-EQUITY "
                    f"account_type={r['account_type']}"
                )
        finally:
            await conn.close()

    _run(body())


def test_ar_trade_resolves_to_1_10400_not_1_10300():
    """AR_TRADE -> 1-10400 (Piutang Usaha) for every tenant.

    Fase A finding: opening_balance.py READ filter previously used
    1-10300 = Kas Kecil as the AR control match code. The role mapping
    guarantees the right CoA.
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
            assert rows
            for r in rows:
                assert r["account_code"] == "1-10400", (
                    f"tenant={r['tenant_id']} AR_TRADE expected 1-10400, "
                    f"got {r['account_code']}"
                )
                assert r["account_code"] != "1-10300", (
                    f"REGRESSION: AR_TRADE for {r['tenant_id']} resolves "
                    "to 1-10300 (Kas Kecil). Legacy OB AR filter bug back."
                )
        finally:
            await conn.close()

    _run(body())


def test_inventory_merchandise_resolves_to_1_10600_not_1_10400():
    """INVENTORY_MERCHANDISE -> 1-10600 (Persediaan Barang Dagangan).

    Fase A finding: opening_balance.py READ filter previously used
    1-10400 = Piutang Usaha as the inventory control match code -- the
    inventory subledger reconciliation warning was silently dead.
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
                WHERE ar.role_key = 'INVENTORY_MERCHANDISE'
                ORDER BY ar.tenant_id
                """
            )
            assert rows
            for r in rows:
                assert r["account_code"] == "1-10600", (
                    f"tenant={r['tenant_id']} INVENTORY_MERCHANDISE expected "
                    f"1-10600, got {r['account_code']}"
                )
                assert r["account_code"] != "1-10400", (
                    f"REGRESSION: INVENTORY_MERCHANDISE for {r['tenant_id']} "
                    "resolves to 1-10400 (Piutang Usaha). Legacy OB "
                    "inventory filter bug back."
                )
        finally:
            await conn.close()

    _run(body())


def test_ap_trade_resolves_to_2_10100_for_all_tenants():
    """AP_TRADE -> 2-10100 (Hutang Usaha) for every tenant."""

    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'AP_TRADE'
                ORDER BY ar.tenant_id
                """
            )
            assert rows
            for r in rows:
                assert r["account_code"] == "2-10100", (
                    f"tenant={r['tenant_id']} AP_TRADE expected 2-10100, "
                    f"got {r['account_code']}"
                )
        finally:
            await conn.close()

    _run(body())
