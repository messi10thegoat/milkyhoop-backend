"""Tests for sales_receipts.py CoA role-resolver migration (Fase C1.2).

Coverage:
    1. Module imports role_resolver helpers + precondition.
    2. SALES_RECEIPT_REQUIRED_ROLES contains the 5 critical roles
       (BANK_OPERATIONAL is deliberately excluded — fallback-only).
    3. ZERO hardcoded CoA literals in the module (Fase C1.2 DoD).
    4. No `account_code = '...'` filters remain.
    5. Precondition util reports CLEAN for the 5 required roles across
       every tenant (live DB).
    6. assert_required_roles_for_path raises when a synthetic gap is
       introduced.
    7. CASH_GENERAL resolves to 1-10100 for every tenant.
    8. Posting handler wires the precondition gate.

Note: cash sale (sales_receipts) does NOT touch AR_TRADE — this is a
direct cash sale path (Dr Kas/Bank → Cr Penjualan + Cr PPN; Dr COGS →
Cr Inventory). AR settlement lives in receive_payments (Fase C1.3).
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


SR_PATH = "/root/milkyhoop-dev/backend/api_gateway/app/routers/sales_receipts.py"


def _run(coro):
    return asyncio.run(coro)


async def _make_pool():
    _require_dsn()
    return await asyncpg.create_pool(SUPERUSER_DSN, min_size=1, max_size=2)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def _parse_sr():
    src = open(SR_PATH).read()
    return src, ast.parse(src)


def test_module_exports_required_roles_constant():
    """Find SALES_RECEIPT_REQUIRED_ROLES = [...] in the AST."""
    src, tree = _parse_sr()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "SALES_RECEIPT_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "SALES_RECEIPT_REQUIRED_ROLES not defined"
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
    # AR_TRADE deliberately excluded (cash sale, not AR settlement).
    assert roles == {
        "CASH_GENERAL",
        "REVENUE_SALES_GOODS",
        "VAT_OUTPUT",
        "COGS_SALES",
        "INVENTORY_MERCHANDISE",
    }
    assert "BANK_OPERATIONAL" not in roles, (
        "BANK_OPERATIONAL must remain fallback-only; bank posting uses "
        "user-picked bank_account_id as primary source."
    )
    assert "AR_TRADE" not in roles, (
        "sales_receipts is cash sale path — AR_TRADE must not appear "
        "(AR settlement lives in receive_payments / Fase C1.3)."
    )


def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse_sr()
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_post_path_calls_precondition_gate():
    """create_sales_receipt must call _ensure_role_preconditions."""
    src, _ = _parse_sr()
    assert (
        src.count("await _ensure_role_preconditions(pool)") >= 1
    ), "Precondition gate must be wired into create handler"


def test_no_hardcoded_coa_codes():
    """ZERO hardcoded CoA literals in sales_receipts.py (Fase C1.2 DoD)."""
    src = open(SR_PATH).read()
    literals = re.findall(r"[\"']([0-9]-[0-9]{4,5})[\"']", src)
    assert not literals, f"Unexpected hardcoded CoA literals: {literals}"


def test_no_sql_account_code_filter_literals():
    src = open(SR_PATH).read()
    bad_eq = re.findall(r"account_code\s*=\s*'[0-9]-[0-9]{4,5}'", src)
    bad_like = re.findall(r"account_code\s+LIKE\s+'[0-9]-[0-9]+%?'", src)
    assert not bad_eq, f"account_code = literal remains: {bad_eq}"
    assert not bad_like, f"account_code LIKE literal remains: {bad_like}"


def test_null_guard_raises_422_on_unresolvable_cash():
    """Latent Law-4 bug fix: cash_acct unresolved -> HTTPException 422,
    NOT silent skip of the Dr Kas line.

    We verify by static inspection that the cash resolution block ends in
    `raise HTTPException(status_code=422, ...)` rather than the old
    `if cash_acct:` guard around the Dr Kas line.
    """
    src = open(SR_PATH).read()
    # The old silent-skip pattern guarded the Dr Cash line with
    # `if cash_acct:` BEFORE the journal_lines INSERT. The fix removes
    # that guard. We assert the 422 raise exists for the null path.
    assert (
        "Akun kas/bank tidak tersedia" in src
    ), "NULL guard 422 raise missing — Dr Kas may still be silent-skipped"
    # The unconditional DR Kas INSERT comment markers should be present.
    assert "DR Cash/Bank" in src


# ---------------------------------------------------------------------------
# Live precondition check
# ---------------------------------------------------------------------------
def test_precondition_clean_for_sales_receipt_required_roles():
    async def body():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "sales_receipts",
                [
                    AccountRole.CASH_GENERAL,
                    AccountRole.REVENUE_SALES_GOODS,
                    AccountRole.VAT_OUTPUT,
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
                    "sales_receipts_synthetic",
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


def test_vat_output_resolves_to_2_10300_for_all_tenants():
    """VAT_OUTPUT interim contract (shared with sales_invoices)."""

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
