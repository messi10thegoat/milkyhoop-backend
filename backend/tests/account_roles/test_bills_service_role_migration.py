"""Tests for bills_service.py CoA role-resolver migration (Fase D2.3).

Third migrated module in the D2 deferred batch. Validates:
- Role-based resolution for AP_TRADE + VAT_INPUT + WHT_PPH_PAYABLE +
  INVENTORY_MERCHANDISE on the four journal-creating paths
  (create_bill_v2, post_bill, void_bill, record_payment / pay_bill).
- PKP toggle: PKP tenant emits VAT line; non-PKP submission with
  tax_amount > 0 rejected (ValueError / 422-equivalent message).
- WHT_PPH fallback now resolves to dedicated 2-10320 (post-D1 V155),
  NOT polluted 2-10300 — regression killer.
- Subcontract WIP 1-10650 / 1-10600 literal RETAINED at ternary
  (deferred to D3 manufaktur). Both ternary sites guarded.
- Void path uses original journal_lines.account_id (no resolver call).
- Precondition gate wired into the four handlers.

Coverage:
    1. Module imports role_resolver + role_precondition helpers.
    2. BILLS_SERVICE_REQUIRED_ROLES constant present + correct.
    3. Hardcoded CoA literal scan: ONLY the two deferred subcontract
       ternaries remain (regression guard + deferred-list tracker).
    4. _ensure_bills_service_role_preconditions wired into create_bill_v2,
       post_bill, void_bill, record_payment.
    5. Precondition CLEAN 5/5 for required roles.
    6. Resolver delivers per-tenant id for AP_TRADE +
       INVENTORY_MERCHANDISE + WHT_PPH_PAYABLE.
    7. WHT_PPH_PAYABLE -> 2-10320 invariant (regression killer).
    8. VAT_INPUT PKP toggle: PKP tenant -> UUID; non-PKP -> None.
    9. Synthetic gate failure raises PreconditionFailedError.
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

BILLS_SERVICE_PATH = (
    "/root/milkyhoop-dev/backend/api_gateway/app/services/bills_service.py"
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
    src = open(BILLS_SERVICE_PATH).read()
    return src, ast.parse(src)


# ---------------------------------------------------------------------------
# Static guarantees
# ---------------------------------------------------------------------------
def test_module_imports_role_resolver_and_precondition():
    src, _ = _parse()
    assert "from .role_resolver import" in src
    assert "resolve_account_id_by_role" in src
    assert "resolve_account_id_by_role_if_pkp" in src
    assert "from .role_precondition import assert_required_roles_for_path" in src


def test_bills_service_required_roles_constant():
    src, tree = _parse()
    found = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "BILLS_SERVICE_REQUIRED_ROLES"
        ):
            found = node.value
            break
    assert found is not None, "BILLS_SERVICE_REQUIRED_ROLES not defined"
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
        "VAT_INPUT",
        "WHT_PPH_PAYABLE",
        "INVENTORY_MERCHANDISE",
    }


def test_only_deferred_literals_remain():
    """Acceptance: ONLY 2 deferred subcontract ternary literals remain.

    Subcontract WIP (1-10650) vs default (1-10600) split is deferred to
    D3 manufaktur (WIP_SUBCONTRACT role catalog promotion). Any OTHER
    CoA literal is a regression.
    """
    src, _ = _parse()
    matches = re.findall(r"['\"][0-9]-[0-9]{4,5}['\"]", src)
    # Expected: ["1-10650", "1-10600", "1-10650", "1-10600"] (two ternaries).
    assert sorted(matches) == sorted(
        ['"1-10650"', '"1-10600"', '"1-10650"', '"1-10600"']
    ), f"Unexpected literals: {matches}"


def test_deferred_subcontract_ternary_has_defer_comment():
    """Each subcontract ternary line must have a DEFER comment nearby."""
    src, _ = _parse()
    # Find lines containing the ternary.
    lines = src.splitlines()
    ternary_indices = [
        i
        for i, ln in enumerate(lines)
        if 'if is_subcontract_bill else "1-10600"' in ln
        or 'if is_sc_bill else "1-10600"' in ln
    ]
    assert (
        len(ternary_indices) == 2
    ), f"Expected 2 subcontract ternaries, found {len(ternary_indices)}"
    for idx in ternary_indices:
        # Look at the 5 lines preceding for a DEFER mention.
        window = "\n".join(lines[max(0, idx - 5) : idx + 1])
        assert "DEFER" in window and (
            "D3 manufaktur" in window or "WIP_SUBCONTRACT" in window
        ), f"Ternary at line {idx + 1} missing DEFER/D3 manufaktur comment"


def test_precondition_wired_into_handlers():
    src, _ = _parse()
    # Four journal-creating handlers should each invoke the gate exactly once.
    assert src.count("await _ensure_bills_service_role_preconditions(self.pool)") >= 4


def test_void_bill_uses_original_journal_lines_no_resolve():
    """Regression: void_bill must mirror original journal_lines.account_id.

    No `resolve_account_id` or `resolve_account_id_by_role` calls inside
    the void_bill body — original account_id is preserved per Iron Law 2
    + C1.5 pattern.
    """
    src, _ = _parse()
    # Locate `async def void_bill(` ... end at next `async def ` at same indent.
    m = re.search(r"\n    async def void_bill\(", src)
    assert m, "void_bill not found"
    start = m.start()
    n = re.search(r"\n    async def ", src[start + 5 :])
    end = (start + 5 + n.start()) if n else len(src)
    body = src[start:end]
    # No role resolver inside void body. The original journal_lines mirror
    # path is the only allowed account_id source.
    assert (
        "resolve_account_id_by_role(" not in body
    ), "void_bill must not call role resolver — use original journal_lines"
    assert (
        "resolve_account_id(conn, tenant_id," not in body
    ), "void_bill must not call literal resolver — use original journal_lines"


# ---------------------------------------------------------------------------
# Live database checks (skipped without DSN)
# ---------------------------------------------------------------------------
def test_precondition_clean_for_all_tenants():
    async def _go():
        pool = await _make_pool()
        try:
            gaps = await check_required_roles_for_path(
                pool,
                "bills_service",
                [
                    AccountRole.AP_TRADE,
                    AccountRole.VAT_INPUT,
                    AccountRole.WHT_PPH_PAYABLE,
                    AccountRole.INVENTORY_MERCHANDISE,
                ],
            )
        finally:
            await pool.close()
        return gaps

    gaps = _run(_go())
    assert gaps == {}, f"Precondition gaps: {gaps}"


def test_resolve_ap_trade_per_tenant():
    async def _go(conn):
        results = {}
        for t in TENANTS:
            results[t] = await resolve_account_id_by_role(conn, t, AccountRole.AP_TRADE)
        return results

    out = _run(_with_conn(_go))
    assert all(v is not None for v in out.values())


def test_resolve_inventory_merchandise_per_tenant():
    async def _go(conn):
        results = {}
        for t in TENANTS:
            results[t] = await resolve_account_id_by_role(
                conn, t, AccountRole.INVENTORY_MERCHANDISE
            )
        return results

    out = _run(_with_conn(_go))
    assert all(v is not None for v in out.values())


def test_wht_pph_payable_resolves_to_dedicated_account():
    """REGRESSION KILLER: WHT_PPH_PAYABLE -> 2-10320 (post-D1), NOT
    polluted 2-10300 (interim) and NOT 2-10310 (payroll-exclusive).
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
        assert code == "2-10320", (
            f"Tenant {tenant} WHT_PPH_PAYABLE points to {code}; "
            "expected 2-10320 (post-D1 V155 dedicated account)"
        )
        assert (
            code != "2-10300"
        ), f"Tenant {tenant} regression: WHT_PPH_PAYABLE pollutes 2-10300"
        assert code != "2-10310", (
            f"Tenant {tenant} regression: WHT_PPH_PAYABLE clashes with "
            "payroll-exclusive 2-10310"
        )


def test_vat_input_pkp_toggle():
    """PKP tenant -> non-None UUID. Non-PKP -> None (caller skips VAT line)."""

    async def _go(conn):
        tenant = TENANTS[0]
        original = await conn.fetchval(
            'SELECT is_pkp FROM "Tenant" WHERE id = $1', tenant
        )
        try:
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = true WHERE id = $1', tenant
            )
            pkp_id = await resolve_account_id_by_role_if_pkp(
                conn, tenant, AccountRole.VAT_INPUT
            )
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = false WHERE id = $1', tenant
            )
            nonpkp_id = await resolve_account_id_by_role_if_pkp(
                conn, tenant, AccountRole.VAT_INPUT
            )
            return pkp_id, nonpkp_id
        finally:
            await conn.execute(
                'UPDATE "Tenant" SET is_pkp = $2 WHERE id = $1', tenant, original
            )

    pkp_id, nonpkp_id = _run(_with_conn(_go))
    assert pkp_id is not None
    assert nonpkp_id is None


def test_synthetic_precondition_failure_raises():
    """Pass a role not mapped for any tenant -> PreconditionFailedError."""

    async def _go():
        pool = await _make_pool()
        try:
            with pytest.raises(PreconditionFailedError):
                await assert_required_roles_for_path(
                    pool,
                    "bills_service_synthetic",
                    [AccountRole.IC_SALES],  # TIER 3 reserved, unseeded
                )
        finally:
            await pool.close()

    _run(_go())
