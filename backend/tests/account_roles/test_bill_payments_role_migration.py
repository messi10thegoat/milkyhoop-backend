"""Tests for bill_payments.py CoA role-resolver migration (Fase D2.3).

Third migrated module (paired with bills_service.py in the coordinated
D2.3 commit). Validates:
- Role-based resolution for AP_TRADE + CASH_GENERAL + WHT_PPH_PAYABLE.
- Coordinated PPh flip: WHT_PPH_PAYABLE resolves to dedicated 2-10320
  (post-D1 V155), closing the AP-PPh pollution source — regression
  killer. Pairs with bills_service.py PPh flip in same commit.
- AP_ACCOUNT constant dropped (resolver inline).
- Read filters use account_roles JOIN (role_key='AP_TRADE'), not
  hardcoded `account_code='2-10100'`.
- Vendor deposit literal (1-10500) RETAINED with DEFER comment
  (deferred to D2-wrap — naming investigation AP_PREPAID vs
  VENDOR_DEPOSIT).
- Purchase discount const (5-10200) RETAINED with DEFER comment
  (deferred to D2-wrap — role PURCHASE_DISCOUNT not yet in catalog).
- Void path uses original journal_lines.account_id (no resolver call).
- Precondition gate wired into create + post + void handlers.

Coverage:
    1. Module imports role_resolver + role_precondition helpers.
    2. BILL_PAYMENTS_REQUIRED_ROLES constant present + correct.
    3. Hardcoded CoA literal scan: ONLY 3 deferred literals remain
       (5-10200 + two 1-10500).
    4. Each deferred literal has DEFER comment within 5 preceding lines.
    5. _ensure_bill_payments_role_preconditions wired into create + post
       + void handlers.
    6. AP_ACCOUNT constant fully removed.
    7. Read filters use ar.role_key (no `account_code = '2-10100'`).
    8. Precondition CLEAN 5/5 for required roles.
    9. WHT_PPH_PAYABLE -> 2-10320 invariant (regression killer pair).
    10. void_bill_payment uses original journal_lines, no role resolver.
    11. Synthetic gate failure raises PreconditionFailedError.
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

BILL_PAYMENTS_PATH = (
    "/root/milkyhoop-dev/backend/api_gateway/app/routers/bill_payments.py"
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
    src = open(BILL_PAYMENTS_PATH).read()
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


def test_bill_payments_required_roles_constant():
    src, tree = _parse()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "BILL_PAYMENTS_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "BILL_PAYMENTS_REQUIRED_ROLES not defined"
    assert isinstance(found, ast.List)
    roles = set()
    for elt in found.elts:
        assert (
            isinstance(elt, ast.Attribute)
            and isinstance(elt.value, ast.Name)
            and elt.value.id == "AccountRole"
        )
        roles.add(elt.attr)
    assert roles == {
        "AP_TRADE",
        "CASH_GENERAL",
        "WHT_PPH_PAYABLE",
        "AP_PREPAID",
        "PURCHASE_DISCOUNT",
    }


def test_ap_account_constant_removed():
    """AP_ACCOUNT constant must be gone (resolver inline)."""
    src, _ = _parse()
    assert (
        "AP_ACCOUNT" not in src
    ), "AP_ACCOUNT constant must be removed after D2.3 flip"


def test_only_deferred_literals_remain():
    """Acceptance: D2-wrap B closes PURCHASE_DISCOUNT + vendor_deposit deferrals.

    Both `"5-10200"` and `"1-10500"` literals MUST be gone — flipped to
    runtime role resolution (AccountRole.PURCHASE_DISCOUNT and
    AccountRole.AP_PREPAID respectively, V156).
    """
    src, _ = _parse()
    # Strip line-comments to allow literal mentions in commentary/docstrings.
    no_comments = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
    matches = re.findall(r"['\"][0-9]-[0-9]{4,5}['\"]", no_comments)
    assert matches == [], (
        f"D2-wrap B should have removed all hardcoded CoA literals "
        f"from bill_payments.py (found: {matches})"
    )


def test_deferred_literals_have_defer_comment():
    """Each deferred literal must carry a DEFER comment within 5 lines."""
    src, _ = _parse()
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if not re.search(r"['\"][0-9]-[0-9]{4,5}['\"]", ln):
            continue
        window = "\n".join(lines[max(0, i - 6) : i + 1])
        assert (
            "DEFER" in window
        ), f"Literal at line {i + 1} ({ln.strip()}) missing DEFER comment"


def test_purchase_discount_resolved_via_role():
    """D2-wrap B: PURCHASE_DISCOUNT now resolved via role, not literal."""
    src, _ = _parse()
    assert (
        "AccountRole.PURCHASE_DISCOUNT" in src
    ), "PURCHASE_DISCOUNT role reference missing — D2-wrap B flip incomplete"
    assert (
        "PURCHASE_DISCOUNT_ACCOUNT" not in src
    ), "Legacy PURCHASE_DISCOUNT_ACCOUNT constant must be removed"


def test_vendor_deposit_resolved_via_ap_prepaid_role():
    """D2-wrap B: vendor advance now resolved via AP_PREPAID role.

    The legacy 1-10500 (AR_OTHER) literal was semantically wrong — vendor
    advance is an asset (Uang Muka Pembelian, 1-10550), not a receivable.
    """
    src, _ = _parse()
    assert "AccountRole.AP_PREPAID" in src, (
        "AP_PREPAID role reference missing — D2-wrap B vendor-deposit flip "
        "incomplete"
    )


def test_read_filter_uses_role_join_not_hardcoded_code():
    src, _ = _parse()
    # Role-based read filter present.
    assert "ar.role_key = 'AP_TRADE'" in src
    # No remaining hardcoded read filter on 2-10100 account_code.
    assert "account_code = '2-10100'" not in src
    assert 'account_code = "2-10100"' not in src


def test_precondition_wired_into_handlers():
    src, _ = _parse()
    # create_bill_payment + post_bill_payment + void_bill_payment.
    assert src.count("await _ensure_bill_payments_role_preconditions(pool)") >= 3


def test_void_bill_payment_uses_original_journal_lines():
    """Regression: void_bill_payment mirrors original journal_lines.

    No `resolve_account_id_by_role` calls inside void body; original
    account_id is preserved per Iron Law 2 + C1.5 pattern.
    """
    src, _ = _parse()
    m = re.search(r"\nasync def void_bill_payment\(", src)
    assert m, "void_bill_payment not found"
    start = m.start()
    n = re.search(r"\nasync def ", src[start + 5 :])
    end = (start + 5 + n.start()) if n else len(src)
    body = src[start:end]
    assert (
        "resolve_account_id_by_role(" not in body
    ), "void_bill_payment must not call role resolver"


# ---------------------------------------------------------------------------
# Live database checks (skipped without DSN)
# ---------------------------------------------------------------------------
def test_precondition_clean_for_all_tenants():
    async def _go():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "bill_payments",
                [
                    AccountRole.AP_TRADE,
                    AccountRole.CASH_GENERAL,
                    AccountRole.WHT_PPH_PAYABLE,
                ],
            )
        finally:
            await pool.close()
        return gaps

    gaps = _run(_go())
    assert gaps == {}, f"Precondition gaps: {gaps}"


def test_wht_pph_payable_resolves_to_2_10320():
    """REGRESSION KILLER (pair with bills_service test).

    Coordinated D2.3 flip: WHT_PPH_PAYABLE resolves to 2-10320 dedicated
    account, closing AP-PPh pollution at the bill_payments source.
    """

    async def _go(conn):
        results = {}
        for t in TENANTS:
            acct_id = await resolve_account_id_by_role(
                conn, t, AccountRole.WHT_PPH_PAYABLE
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
            code == "2-10320"
        ), f"Tenant {tenant} WHT_PPH_PAYABLE -> {code}; expected 2-10320"
        assert (
            code != "2-10300"
        ), f"Tenant {tenant} regression: WHT_PPH_PAYABLE still pollutes 2-10300"


def test_resolve_cash_general_per_tenant():
    """CASH_GENERAL fallback (user-picked bank_account_id has primary)."""

    async def _go(conn):
        results = {}
        for t in TENANTS:
            results[t] = await resolve_account_id_by_role(
                conn, t, AccountRole.CASH_GENERAL
            )
        return results

    out = _run(_with_conn(_go))
    assert all(v is not None for v in out.values())


def test_synthetic_precondition_failure_raises():
    """Pass a role not mapped for any tenant -> PreconditionFailedError."""

    async def _go():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "bill_payments_synthetic",
                    [AccountRole.IC_SALES],  # TIER 3 reserved, unseeded
                )
        finally:
            await pool.close()

    _run(_go())
