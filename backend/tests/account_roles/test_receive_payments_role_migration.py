"""Tests for receive_payments.py CoA role-resolver migration (Fase D2.4).

Fourth migrated module of the D2 deferred batch (closes D2.1-D2.4).
Validates:
- Role-based resolution for AR_TRADE + CUSTOMER_DEPOSIT_LIABILITY.
- AR_ACCOUNT + CUSTOMER_DEPOSIT_ACCOUNT constants dropped (resolver
  inline at use sites: preview-journal handler + _post_payment helper).
- Read filter (AR outstanding compute CTE) uses account_type='RECEIVABLE'
  per Law 29, not hardcoded `account_code='1-10400'`.
- REVENUE_SALES_DISCOUNT literal "6-10100" RETAINED with DEFER comment
  (role not seeded 5/5 as of D2.4; flip target = D2-wrap micro after
  seed). Pre-check: 0/5 tenants mapped, defer mandatory.
- Void path uses original journal_lines.account_id (no resolver call,
  C1.5 pattern preservation).
- Precondition gate wired into create + post + void handlers.
- 5-artifact ARAP settlement contract preserved (journal + wrapper +
  allocation + bank_tx + cache via compute_ar_outstanding).
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
)

RECEIVE_PAYMENTS_PATH = (
    "/root/milkyhoop-dev/backend/api_gateway/app/routers/receive_payments.py"
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
    src = open(RECEIVE_PAYMENTS_PATH).read()
    return src, ast.parse(src)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse()
    assert "from ..services.role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert (
        "from ..services.role_precondition import assert_required_roles_for_path" in src
    )


def test_receive_payments_required_roles_constant():
    src, tree = _parse()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "RECEIVE_PAYMENTS_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "RECEIVE_PAYMENTS_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        )
        roles.add(elt.attr)
    assert roles == {"AR_TRADE", "CUSTOMER_DEPOSIT_LIABILITY", "REVENUE_SALES_DISCOUNT"}


def test_ar_and_customer_deposit_constants_removed():
    """AR_ACCOUNT + CUSTOMER_DEPOSIT_ACCOUNT must be gone (resolver inline)."""
    src, _ = _parse()
    assert "AR_ACCOUNT" not in src, "AR_ACCOUNT const must be removed after D2.4 flip"
    assert (
        "CUSTOMER_DEPOSIT_ACCOUNT" not in src
    ), "CUSTOMER_DEPOSIT_ACCOUNT const must be removed after D2.4 flip"


def test_no_account_code_literals_remain():
    """Post Fase D2-wrap D: no hardcoded CoA literals in active code.

    Previously '6-10100' was retained as REVENUE_SALES_DISCOUNT fallback while
    role unseeded. V157 seeded 5/5; receive_payments.py now resolves via
    AccountRole.REVENUE_SALES_DISCOUNT. Stray literals tolerated only inside
    comment lines (documentation), so this check excludes comment-only matches.
    """
    src, _ = _parse()
    bad = []
    for m in re.finditer(r"""['"]([0-9]-[0-9]{4,5})['"]""", src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        line_end = src.find("\n", m.end())
        line = src[line_start : line_end if line_end != -1 else len(src)]
        if line.lstrip().startswith("#"):
            continue
        bad.append(m.group(1))
    assert bad == [], f"Hardcoded CoA literals still present in code: {bad}"


def test_revenue_sales_discount_resolver_inline():
    """Post Fase D2-wrap D: discount_account resolved via role, no literal."""
    src, _ = _parse()
    assert re.search(
        r"resolve_account_id_by_role\(\s*conn,\s*ctx\[.tenant_id.\],\s*AccountRole\.REVENUE_SALES_DISCOUNT",
        src,
    ), "REVENUE_SALES_DISCOUNT resolver call missing at discount path"


def test_read_filter_uses_account_type_not_hardcoded_code():
    """Law 29: AR outstanding compute filter uses account_type='RECEIVABLE'."""
    src, _ = _parse()
    assert "coa.account_type = 'RECEIVABLE'" in src
    # No remaining hardcoded read filter on 1-10400 account_code in active SQL.
    assert "coa.account_code = '1-10400'" not in src
    assert 'coa.account_code = "1-10400"' not in src


def test_precondition_wired_into_handlers():
    """create + post + void handlers each invoke the precondition gate."""
    src, _ = _parse()
    assert src.count("await _ensure_receive_payments_role_preconditions(pool)") >= 3


def test_void_receive_payment_uses_original_journal_lines():
    """Regression: void_receive_payment mirrors original journal_lines.

    No resolve_account_id_by_role calls inside void body; original
    account_id is preserved per Iron Law 2 + C1.5 pattern.
    """
    src, _ = _parse()
    m = re.search(r"\nasync def void_receive_payment\(", src)
    assert m, "void_receive_payment not found"
    start = m.start()
    n = re.search(r"\nasync def ", src[start + 5 :])
    end = (start + 5 + n.start()) if n else len(src)
    body = src[start:end]
    assert (
        "resolve_account_id_by_role(" not in body
    ), "void_receive_payment must not call role resolver"
    # Sanity: void body inserts journal_lines copying original account_id.
    assert (
        'line["account_id"]' in body or "line['account_id']" in body
    ), "void path must reinsert lines using original account_id"


# ---------------------------------------------------------------------------
# Live database checks (skipped without DSN)
# ---------------------------------------------------------------------------
def test_precondition_clean_for_all_tenants():
    async def _go():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "receive_payments",
                [
                    AccountRole.AR_TRADE,
                    AccountRole.CUSTOMER_DEPOSIT_LIABILITY,
                    AccountRole.REVENUE_SALES_DISCOUNT,
                ],
            )
        finally:
            await pool.close()
        return gaps

    gaps = _run(_go())
    assert gaps == {}, f"Precondition gaps: {gaps}"


def test_ar_trade_resolves_to_1_10400():
    """REGRESSION GUARD: AR_TRADE -> 1-10400 across all 5 tenants."""

    async def _go(conn):
        results = {}
        for t in TENANTS:
            acct_id = await resolve_account_id_by_role(conn, t, AccountRole.AR_TRADE)
            row = await conn.fetchrow(
                "SELECT account_code FROM chart_of_accounts WHERE id = $1",
                acct_id,
            )
            results[t] = row["account_code"]
        return results

    codes = _run(_with_conn(_go))
    for tenant, code in codes.items():
        assert (
            code == "1-10400"
        ), f"Tenant {tenant} AR_TRADE -> {code}; expected 1-10400"


def test_customer_deposit_liability_resolves_to_2_10500():
    """REGRESSION GUARD: CUSTOMER_DEPOSIT_LIABILITY -> 2-10500."""

    async def _go(conn):
        results = {}
        for t in TENANTS:
            acct_id = await resolve_account_id_by_role(
                conn, t, AccountRole.CUSTOMER_DEPOSIT_LIABILITY
            )
            row = await conn.fetchrow(
                "SELECT account_code FROM chart_of_accounts WHERE id = $1",
                acct_id,
            )
            results[t] = row["account_code"]
        return results

    codes = _run(_with_conn(_go))
    for tenant, code in codes.items():
        assert code == "2-10500", (
            f"Tenant {tenant} CUSTOMER_DEPOSIT_LIABILITY -> {code}; "
            f"expected 2-10500"
        )


def test_revenue_sales_discount_resolves_to_4_10200():
    """REGRESSION GUARD (Fase D2-wrap D / V157):
    REVENUE_SALES_DISCOUNT -> 4-10200 (Diskon Penjualan) across 5/5 tenants.
    """

    async def _go(conn):
        results = {}
        for t in TENANTS:
            acct_id = await resolve_account_id_by_role(
                conn, t, AccountRole.REVENUE_SALES_DISCOUNT
            )
            row = await conn.fetchrow(
                "SELECT account_code FROM chart_of_accounts WHERE id = $1",
                acct_id,
            )
            results[t] = row["account_code"]
        return results

    codes = _run(_with_conn(_go))
    for tenant, code in codes.items():
        assert (
            code == "4-10200"
        ), f"Tenant {tenant} REVENUE_SALES_DISCOUNT -> {code}; expected 4-10200"


def test_synthetic_precondition_failure_raises():
    """Pass a role not mapped for any tenant -> PreconditionFailedError."""

    async def _go():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "receive_payments_synthetic",
                    [AccountRole.IC_SALES],  # TIER 3 reserved, unseeded
                )
        finally:
            await pool.close()

    _run(_go())
