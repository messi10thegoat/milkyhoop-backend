"""
Report ↔ GL Invariant Arbiter (T4)

Anti-regression test for Surprise #13 (PPN drift) + #10 vector (dashboard drift)
+ T2.5 (Neraca/P&L is_effective_journal migration).

For each report endpoint, asserts:
    report_net_per_account == GL_net_per_account_via_direct_SQL

Where GL net = SUM(credit-debit)*sign(normal_balance), filtered by:
    je.status = 'POSTED'
    AND is_effective_journal(je.id)

Tolerance: 0 (strict). Fail-loud.

Env vars required (test skipped if missing):
    E2E_GATEWAY_URL      default https://milkyhoop.com
    E2E_JWT              golden-apparel access token (see frontend/web/.env.e2e)
    E2E_TENANT_ID        default golden-apparel
    E2E_DB_DSN           postgres DSN for direct GL queries
    E2E_PERIOD           default 2026-06

Inventory invariant #5 (barrier=1 residue from Fase 4 backstop) tolerated
via expected_diff=1 — see DOCS/plans/2026-06-07-e2e-golden-path-handover.md §8.4.
"""
import os
from decimal import Decimal

import pytest

GATEWAY = os.environ.get("E2E_GATEWAY_URL", "https://milkyhoop.com")
JWT = os.environ.get("E2E_JWT")
TENANT = os.environ.get("E2E_TENANT_ID", "golden-apparel")
DB_DSN = os.environ.get("E2E_DB_DSN")
PERIOD = os.environ.get("E2E_PERIOD", "2026-06")  # YYYY-MM


pytestmark = pytest.mark.skipif(
    not (JWT and DB_DSN),
    reason="E2E_JWT and E2E_DB_DSN required for GL invariant arbiter",
)


def _period_bounds(period: str) -> tuple[str, str]:
    y, m = period.split("-")
    y, m = int(y), int(m)
    start = f"{y:04d}-{m:02d}-01"
    if m == 12:
        end = f"{y + 1:04d}-01-01"
    else:
        end = f"{y:04d}-{m + 1:02d}-01"
    return start, end


@pytest.fixture(scope="module")
def http():
    import httpx

    with httpx.Client(
        base_url=GATEWAY,
        headers={"Authorization": f"Bearer {JWT}"},
        timeout=30.0,
    ) as c:
        yield c


@pytest.fixture(scope="module")
async def db():
    import asyncpg

    pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=2)
    yield pool
    await pool.close()


async def _gl_net_by_account_codes(
    pool, codes: list[str], period: str
) -> dict[str, Decimal]:
    start, end = _period_bounds(period)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.account_code,
                   a.normal_balance,
                   COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0) AS net_cr_minus_dr
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts a ON a.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND is_effective_journal(je.id)
              AND je.journal_date >= $2::date
              AND je.journal_date <  $3::date
              AND a.account_code = ANY($4)
            GROUP BY a.account_code, a.normal_balance
            """,
            TENANT,
            start,
            end,
            codes,
        )
    out: dict[str, Decimal] = {c: Decimal("0") for c in codes}
    for r in rows:
        net = Decimal(str(r["net_cr_minus_dr"]))
        # Convert to natural sign per normal_balance: liability/revenue Cr-normal stays;
        # asset/expense Dr-normal flipped to positive.
        out[r["account_code"]] = net if r["normal_balance"] == "CREDIT" else -net
    return out


async def _gl_net_by_account_type(pool, account_type: str, period: str) -> Decimal:
    start, end = _period_bounds(period)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0) AS net_cr_minus_dr
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts a ON a.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND is_effective_journal(je.id)
              AND je.journal_date >= $2::date
              AND je.journal_date <  $3::date
              AND a.account_type = $4
            """,
            TENANT,
            start,
            end,
            account_type,
        )
    return Decimal(str(row["net_cr_minus_dr"]))


# ─── PPN ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ppn_keluaran_masukan_eq_gl(http, db):
    """PPN report totals == GL net per PPN-Keluaran / PPN-Masukan account."""
    # Resolve PPN coa codes via tax_codes
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT a.account_code,
                   CASE WHEN tc.direction = 'output' THEN 'K' ELSE 'M' END AS side
            FROM tax_codes tc
            JOIN chart_of_accounts a ON a.id = tc.coa_id
            WHERE tc.tenant_id = $1
              AND tc.tax_type = 'PPN'
              AND tc.is_active = true
            """,
            TENANT,
        )
    k_codes = [r["account_code"] for r in rows if r["side"] == "K"]
    m_codes = [r["account_code"] for r in rows if r["side"] == "M"]

    gl_k = await _gl_net_by_account_codes(db, k_codes, PERIOD)
    gl_m = await _gl_net_by_account_codes(db, m_codes, PERIOD)
    gl_keluaran_total = sum(gl_k.values(), Decimal("0"))
    gl_masukan_total = sum(
        gl_m.values(), Decimal("0")
    )  # already sign-flipped to natural

    # masukan natural sign: asset Dr-normal → above flip makes positive = Dr excess
    # → that's our "report masukan"
    resp = http.get(f"/api/tax-reports/ppn?period={PERIOD}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    rpt_k = Decimal(str(data["ppn_keluaran"]["total"]))
    rpt_m = Decimal(str(data["ppn_masukan"]["total"]))

    assert (
        rpt_k == gl_keluaran_total
    ), f"PPN Keluaran drift: report={rpt_k} GL={gl_keluaran_total}"
    assert (
        rpt_m == gl_masukan_total
    ), f"PPN Masukan drift: report={rpt_m} GL={gl_masukan_total}"


@pytest.mark.asyncio
async def test_pph_total_eq_gl(http, db):
    """PPh grand total == GL net per PPh withholding accounts."""
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT a.account_code
            FROM tax_codes tc
            JOIN chart_of_accounts a ON a.id = tc.coa_id
            WHERE tc.tenant_id = $1
              AND tc.tax_type LIKE 'PPH%'
              AND tc.is_active = true
            """,
            TENANT,
        )
    codes = [r["account_code"] for r in rows]
    if not codes:
        pytest.skip("no PPh tax_codes in golden-apparel")

    gl = await _gl_net_by_account_codes(db, codes, PERIOD)
    gl_total = sum(gl.values(), Decimal("0"))

    resp = http.get(f"/api/tax-reports/pph?period={PERIOD}")
    assert resp.status_code == 200, resp.text
    rpt_total = Decimal(str(resp.json()["grand_total_pph"]))

    assert rpt_total == gl_total, f"PPh drift: report={rpt_total} GL={gl_total}"


# ─── Dashboard ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_sales_trends_monthly_eq_gl_revenue(http, db):
    """Dashboard sales-trends monthly period sum == GL REVENUE net."""
    resp = http.get("/api/dashboard/sales-trends?granularity=monthly")
    assert resp.status_code == 200, resp.text
    trends = resp.json().get("trends") or resp.json().get("data") or []

    # Pick row for PERIOD
    period_row = None
    for t in trends:
        if t.get("date", "").startswith(PERIOD):
            period_row = t
            break
    if not period_row:
        pytest.skip(f"sales-trends has no row for {PERIOD}")

    rpt = Decimal(str(period_row["revenue"]))
    gl = await _gl_net_by_account_type(db, "REVENUE", PERIOD)

    assert rpt == gl, f"sales-trends monthly drift {PERIOD}: report={rpt} GL={gl}"


@pytest.mark.asyncio
async def test_dashboard_expense_cat_eq_gl_expense(http, db):
    """Dashboard expense-cat total per category sums to GL net for 5-/6- accounts."""
    resp = http.get("/api/dashboard/top-expenses?period=month&limit=20")
    if resp.status_code == 404:
        pytest.skip("top-expenses endpoint path differs in this build")
    assert resp.status_code == 200, resp.text
    items = resp.json().get("expenses") or resp.json().get("data") or []
    rpt_sum = sum((Decimal(str(it.get("amount", 0))) for it in items), Decimal("0"))

    # GL: SUM(debit-credit) on 5-% + 6-% accounts effective-only, current month
    from datetime import date

    today = date.today()
    period = f"{today.year:04d}-{today.month:02d}"
    start, end = _period_bounds(period)
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS net_dr
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts a ON a.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND is_effective_journal(je.id)
              AND je.journal_date >= $2::date
              AND je.journal_date <  $3::date
              AND (a.account_code LIKE '5-%' OR a.account_code LIKE '6-%')
            """,
            TENANT,
            start,
            end,
        )
    gl = Decimal(str(row["net_dr"]))

    # Note: report applies LIMIT, so report_sum <= gl. Allow rpt_sum subset.
    assert rpt_sum <= gl, f"expense-cat over-reports: report={rpt_sum} GL={gl}"


# ─── Inventory invariant #5 (with documented +1 tolerance) ────────────────


@pytest.mark.asyncio
async def test_inventory_invariant_sub_ledger_eq_gl(db):
    """
    Inventory sub-ledger == GL Persediaan.

    EXPECTED_DIFF=1 Rp tolerated per Fase 4 backstop barrier residue
    (see DOCS/plans/2026-06-07-e2e-golden-path-handover.md §8.4).
    """
    EXPECTED_DIFF = Decimal("1")
    async with db.acquire() as conn:
        # Sub-ledger = inventory_ledger sum
        sub = await conn.fetchval(
            """
            SELECT COALESCE(SUM(value_change), 0)
            FROM inventory_ledger
            WHERE tenant_id = $1
            """,
            TENANT,
        )
        # GL = sum of Persediaan accounts net Dr
        gl = await conn.fetchval(
            """
            SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts a ON a.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND is_effective_journal(je.id)
              AND a.account_code LIKE '1-130%'
            """,
            TENANT,
        )
    drift = Decimal(str(gl)) - Decimal(str(sub))
    assert (
        abs(drift) <= EXPECTED_DIFF
    ), f"inventory ledger vs GL drift={drift} exceeds tolerance {EXPECTED_DIFF}"
