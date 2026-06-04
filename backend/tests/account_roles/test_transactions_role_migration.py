"""Tests for transactions.py CoA role-resolver migration (Fase C1.3).

Coverage:
    1. Module imports role_resolver helpers + precondition.
    2. TRANSACTIONS_REQUIRED_ROLES contains the 4 critical roles
       (BANK_OPERATIONAL is deliberately excluded — fallback-only).
    3. ZERO hardcoded CoA literals in the module (Fase C1.3 DoD).
    4. No `account_code = '...'` filters remain.
    5. NULL guard 422 fires instead of silent-skip on unresolvable kas/bank.
    6. POS helper wires the precondition gate on entry.
    7. Precondition util reports CLEAN for the 4 required roles across
       every tenant (live DB).
    8. assert_required_roles_for_path raises when a synthetic gap is
       introduced.
    9. CASH_GENERAL resolves to 1-10100 for every tenant.

Note: POS cash sale (transactions._create_pos_inventory_and_journals) does
NOT touch AR_TRADE — this is a direct cash-or-bank POS path
(Dr Kas/Bank -> Cr Penjualan; Dr COGS -> Cr Inventory). It also does NOT
include VAT_OUTPUT in the POS helper today (POS does not break out PPN
per the current data shape); when PPN-aware POS lands it will join the
required-roles list.
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


TX_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/transactions.py"


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    _require_dsn()
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def _parse_tx():
    src = open(TX_PATH).read()
    return src, ast.parse(src)


def test_module_exports_required_roles_constant():
    """Find TRANSACTIONS_REQUIRED_ROLES = [...] in the AST."""
    src, tree = _parse_tx()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "TRANSACTIONS_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "TRANSACTIONS_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        ), f"unexpected element: {ast.dump(elt)}"
        roles.add(elt.attr)
    # BANK_OPERATIONAL deliberately excluded (fallback-only pattern).
    # AR_TRADE deliberately excluded (POS = cash/bank sale, no AR).
    # VAT_OUTPUT deliberately excluded (POS helper does not break out PPN).
    assert roles == {
        "CASH_GENERAL",
        "REVENUE_SALES_GOODS",
        "COGS_SALES",
        "INVENTORY_MERCHANDISE",
    }
    assert "BANK_OPERATIONAL" not in roles, (
        "BANK_OPERATIONAL must remain fallback-only; bank posting uses "
        "user-picked bank_account_id (or POS payment_method) as primary."
    )
    assert (
        "AR_TRADE" not in roles
    ), "transactions POS is cash/bank sale path — AR_TRADE must not appear."


def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse_tx()
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_pos_helper_calls_precondition_gate():
    """_create_pos_inventory_and_journals must call _ensure_role_preconditions."""
    src, _ = _parse_tx()
    assert (
        src.count("_ensure_role_preconditions(") >= 1
    ), "Precondition gate must be wired into POS journal helper"


def test_no_hardcoded_coa_codes():
    """ZERO hardcoded CoA literals in transactions.py (Fase C1.3 DoD)."""
    src = open(TX_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


def test_no_sql_account_code_filter_literals():
    src = open(TX_PATH).read()
    bad_eq = re.findall(r"account_code\s*=\s*'[0-9]-[0-9]{4,5}'", src)
    bad_like = re.findall(r"account_code\s+LIKE\s+'[0-9]-[0-9]+%?'", src)
    assert not bad_eq, f"account_code = literal remains: {bad_eq}"
    assert not bad_like, f"account_code LIKE literal remains: {bad_like}"


def test_null_guard_raises_422_on_unresolvable_cash():
    """Latent Law-4 bug fix: kas_acct_id unresolved -> HTTPException 422,
    NOT silent skip of the Dr Kas line.

    We verify by static inspection that the cash resolution block ends
    with a 422 raise rather than the old `if kas_acct_id:` guard around
    the Dr Kas line.
    """
    src = open(TX_PATH).read()
    assert (
        "Akun kas/bank tidak tersedia" in src
    ), "NULL guard 422 raise missing — Dr Kas may still be silent-skipped"
    # Unconditional DR Cash/Bank marker should be present.
    assert "DR Cash/Bank" in src
    # The old silent-skip guards around journal_lines INSERTs must be
    # gone (only the comment referencing the old pattern remains).
    # Match the actual guard syntax, not substrings inside comments.
    bad = re.findall(
        r"^\s*if\s+(kas_acct_id|penjualan_acct_id|hpp_acct_id|inv_acct_id):\s*$",
        src,
        re.M,
    )
    assert not bad, f"Old silent-skip guards still present: {bad}"
    # Positive: the NULL-guard 422 raise must use `if not kas_acct_id:`.
    assert re.search(
        r"^\s*if\s+not\s+kas_acct_id\s*:", src, re.M
    ), "Expected `if not kas_acct_id:` NULL guard not found"


# ---------------------------------------------------------------------------
# Live precondition check
# ---------------------------------------------------------------------------
def test_precondition_clean_for_transactions_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "transactions",
                [
                    AccountRole.CASH_GENERAL,
                    AccountRole.REVENUE_SALES_GOODS,
                    AccountRole.COGS_SALES,
                    AccountRole.INVENTORY_MERCHANDISE,
                ],
            )
            assert gaps == {}, f"unexpected mapping gaps: {gaps}"
        finally:
            await pool.close()

    _run(body())


def test_precondition_assert_raises_on_unmapped_role():
    """Sanity: the gate fails loud if any required role is unmapped."""

    async def body():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "transactions_synthetic",
                    [AccountRole.CASH_GENERAL, "INVENTORY_WIP"],
                )
        finally:
            await pool.close()

    _run(body())


# ---------------------------------------------------------------------------
# Mapping sanity
# ---------------------------------------------------------------------------
def test_cash_general_resolves_to_1_10100_for_all_tenants():
    """CASH_GENERAL -> 1-10100 (Kas) for every tenant (Fase B seed)."""

    async def body():
        _require_dsn()
        conn = await asyncpg.connect(SUPERUSER_DSN)
        try:
            rows = await conn.fetch(
                """
                SELECT ar.tenant_id, ca.account_code
                FROM account_roles ar
                JOIN chart_of_accounts ca ON ca.id = ar.account_id
                WHERE ar.role_key = 'CASH_GENERAL'
                ORDER BY ar.tenant_id
                """
            )
            assert rows, "CASH_GENERAL must be mapped for every tenant"
            for r in rows:
                assert r["account_code"] == "1-10100", (
                    f"tenant={r['tenant_id']} expected 1-10100, "
                    f"got {r['account_code']}"
                )
        finally:
            await conn.close()

    _run(body())
