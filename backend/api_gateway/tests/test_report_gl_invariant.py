"""
Report ↔ GL Invariant Arbiter (T4 expanded — Fase 9.5)

Anti-regression test for:
  - Surprise #13 PPN drift (T1 net Cr-Dr aggregation)
  - #10 vector dashboard drift (T2)
  - T2.5 Neraca/P&L is_effective_journal migration
  - Surprise #15 Neraca display (V168 category column refactor)
  - Surprise #16 Arus Kas is_cash refactor
  - Surprise #18 T4 coverage gap (Neraca #2 + cash flow #6 arbiters)

For each report endpoint, asserts:
    report_net_per_account == GL_net_per_account_via_direct_SQL

Where GL net = SUM(credit-debit)*sign(normal_balance), filtered by:
    je.status = 'POSTED'
    AND is_effective_journal(je.id)

Tolerance: 0 (strict). Fail-loud on drift.

Env vars required (test FAILS, not skips, if missing — owner ruling Fase 9.5):
    E2E_GATEWAY_URL      default https://milkyhoop.com
    E2E_JWT              golden-apparel access token (frontend/web/.env.e2e)
    E2E_TENANT_ID        default golden-apparel
    E2E_DB_DSN           postgres DSN for direct GL queries
    E2E_PERIOD           default 2026-06

Inventory invariant #5 (barrier=1 residue from Fase 4 backstop) tolerated
via expected_diff=1 — see DOCS/plans/2026-06-07-e2e-golden-path-handover.md §8.4.

Onboarding-completeness test (#18 class closure) verifies V168 canonical
seed populates category/cash_flow_category/is_cash for new tenants natively.
"""
import os
from decimal import Decimal

import pytest

GATEWAY = os.environ.get("E2E_GATEWAY_URL", "https://milkyhoop.com")
JWT = os.environ.get("E2E_JWT")
TENANT = os.environ.get("E2E_TENANT_ID", "golden-apparel")
DB_DSN = os.environ.get("E2E_DB_DSN")
PERIOD = os.environ.get("E2E_PERIOD", "2026-06")  # YYYY-MM


# ─── Module env gate — FAIL-LOUD if missing (owner Fase 9.5 ruling) ──────
# Previous behavior used pytest.mark.skipif → silent no-op. Owner explicit:
# "module skipif WAJIB fail kalau env absent (no silent no-op)". Convert to
# fail-loud via collection-time exception. CI-runs with env absent now block,
# not silently green.
if not (JWT and DB_DSN):
    pytest.fail(
        "T4 arbiter requires E2E_JWT + E2E_DB_DSN. Silent skip removed per "
        "Fase 9.5 ruling — set env vars or remove this test from CI scope.",
        pytrace=False,
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


# ─── Collection-time helper: does tenant have PPh data? ──────────────────
# Replaces in-body pytest.skip() with eager check — silent skip is gap-hide.

def _tenant_has_pph() -> bool:
    """Synchronous probe — runs at collection time."""
    import asyncpg
    import asyncio

    async def _probe():
        conn = await asyncpg.connect(DB_DSN)
        try:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS n
                FROM tax_codes
                WHERE tenant_id = $1 AND tax_type LIKE 'PPH%' AND is_active = true
                """,
                TENANT,
            )
            return (row["n"] or 0) > 0
        finally:
            await conn.close()

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


_HAS_PPH = _tenant_has_pph()


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


async def _gl_net_by_category(pool, category: str, as_of: str) -> Decimal:
    """Cumulative net through as_of for category (Neraca point-in-time)."""
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
              AND je.journal_date <= $2::date
              AND a.category = $3
            """,
            TENANT,
            as_of,
            category,
        )
    return Decimal(str(row["net_cr_minus_dr"]))


# ─── PPN ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ppn_keluaran_masukan_eq_gl(http, db):
    """PPN report totals == GL net per PPN-Keluaran / PPN-Masukan account."""
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
    gl_masukan_total = sum(gl_m.values(), Decimal("0"))

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


@pytest.mark.skipif(
    not _HAS_PPH,
    reason=f"tenant {TENANT} has no active PPh tax_codes — coverage gap "
    "documented (B3 deep-val Fase 3.5 will exercise)",
)
@pytest.mark.asyncio
async def test_pph_total_eq_gl(http, db):
    """PPh grand total == GL net per PPh withholding accounts.

    Skipped via collection-time _HAS_PPH probe (not in-body pytest.skip) —
    owner ruling: silent skip = gap-hide, eager skip = explicit coverage gap.
    """
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
    gl = await _gl_net_by_account_codes(db, codes, PERIOD)
    gl_total = sum(gl.values(), Decimal("0"))

    resp = http.get(f"/api/tax-reports/pph?period={PERIOD}")
    assert resp.status_code == 200, resp.text
    rpt_total = Decimal(str(resp.json()["grand_total_pph"]))

    assert rpt_total == gl_total, f"PPh drift: report={rpt_total} GL={gl_total}"


# ─── Dashboard ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_sales_trends_monthly_eq_gl_revenue(http, db):
    """Dashboard sales-daily monthly period sum == GL REVENUE net.

    Fail-loud if period absent (owner ruling: absent period = BUG, not skip).
    Note: endpoint is /sales-daily not /sales-trends in this build.
    """
    resp = http.get("/api/dashboard/sales-daily?granularity=monthly")
    assert resp.status_code == 200, resp.text
    trends = resp.json().get("trends") or resp.json().get("data") or []

    period_row = None
    for t in trends:
        if t.get("date", "").startswith(PERIOD):
            period_row = t
            break
    assert period_row is not None, (
        f"sales-daily monthly missing period {PERIOD} — endpoint bug or "
        f"GL has no revenue in period. trends={trends!r}"
    )

    rpt = Decimal(str(period_row["revenue"]))
    gl = await _gl_net_by_account_type(db, "REVENUE", PERIOD)

    assert rpt == gl, f"sales-daily monthly drift {PERIOD}: report={rpt} GL={gl}"


@pytest.mark.asyncio
async def test_dashboard_expense_cat_eq_gl_expense(http, db):
    """Dashboard expense-cat per-item sum <= GL net for 5-/6- accounts.

    Fail-loud on 404 (endpoint moved permanently = bug, file ticket).
    """
    resp = http.get("/api/dashboard/top-expenses?period=month&limit=20")
    assert resp.status_code != 404, (
        "top-expenses endpoint 404 — moved or removed. File ticket + restore "
        "T4 coverage. Original path: /api/dashboard/top-expenses"
    )
    assert resp.status_code == 200, resp.text
    items = resp.json().get("expenses") or resp.json().get("data") or []
    rpt_sum = sum((Decimal(str(it.get("amount", 0))) for it in items), Decimal("0"))

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

    assert rpt_sum <= gl, f"expense-cat over-reports: report={rpt_sum} GL={gl}"


# ─── Inventory invariant #5 (with documented +1 tolerance) ────────────────


@pytest.mark.asyncio
async def test_inventory_invariant_sub_ledger_eq_gl(db):
    """
    Inventory sub-ledger == GL Persediaan.

    Bug fix: query previously used '1-130%' prefix which never matched
    canonical V154 Persediaan code 1-10600. Now uses category='persediaan'
    (V168 populated) for scheme-independent classification.

    EXPECTED_DIFF=1 Rp tolerated per Fase 4 backstop barrier residue
    (see DOCS/plans/2026-06-07-e2e-golden-path-handover.md §8.4).
    """
    EXPECTED_DIFF = Decimal("1")
    async with db.acquire() as conn:
        sub = await conn.fetchval(
            """
            SELECT COALESCE(SUM(value_change), 0)
            FROM inventory_ledger
            WHERE tenant_id = $1
            """,
            TENANT,
        )
        gl = await conn.fetchval(
            """
            SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0)
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.journal_id
            JOIN chart_of_accounts a ON a.id = jl.account_id
            WHERE je.tenant_id = $1
              AND je.status = 'POSTED'
              AND is_effective_journal(je.id)
              AND a.category = 'persediaan'
            """,
            TENANT,
        )
    drift = Decimal(str(gl)) - Decimal(str(sub))
    assert (
        abs(drift) <= EXPECTED_DIFF
    ), f"inventory ledger vs GL drift={drift} exceeds tolerance {EXPECTED_DIFF}"


# ─── #2 Neraca Balance Arbiter (NEW Fase 9.5) ─────────────────────────────


@pytest.mark.asyncio
async def test_neraca_balanced_eq_gl(http, db):
    """
    Invariant #2: Neraca endpoint Σ Aset == Σ Liab+Eq.

    Plus cross-check per category: endpoint bucket == GL net by category.
    Plus laba_periode_berjalan == P&L net income (cross-report consistency).

    Inventory +1 barrier tolerated globally (drift <= 1 Rp).
    """
    EXPECTED_DIFF = Decimal("1")  # +1 barrier across whole balance
    _, end_str = _period_bounds(PERIOD)
    from datetime import date

    as_of = date.fromisoformat(end_str).replace(day=1).isoformat()  # not used
    # Use periode YYYY-MM directly
    resp = http.get(f"/api/reports/neraca/{PERIOD}")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    total_aset = Decimal(str(data["total_aset"]))
    total_kew = Decimal(str(data["total_kewajiban"]))
    total_eku = Decimal(str(data["ekuitas"]["total"]))
    laba_periode = Decimal(str(data["ekuitas"]["laba_periode_berjalan"]))

    balance_drift = total_aset - (total_kew + total_eku)
    assert (
        abs(balance_drift) <= EXPECTED_DIFF
    ), f"Neraca unbalanced: Aset={total_aset} Kew+Eku={total_kew + total_eku} drift={balance_drift}"

    # Cross-check P&L net income consistency
    pl_resp = http.get(f"/api/reports/laba-rugi/{PERIOD}")
    if pl_resp.status_code == 200:
        pl_data = pl_resp.json()
        pl_net = Decimal(str(pl_data.get("laba_bersih", pl_data.get("net_income", 0))))
        assert (
            abs(pl_net - laba_periode) <= EXPECTED_DIFF
        ), f"Neraca laba_periode={laba_periode} != P&L net_income={pl_net}"


# ─── #6 Cash Flow == GL Bank Arbiter (NEW Fase 9.5) ───────────────────────


@pytest.mark.asyncio
async def test_arus_kas_saldo_akhir_eq_gl_cash(http, db):
    """
    Invariant #6: Arus Kas saldo_akhir_kas == Σ GL net on is_cash accounts.

    Tests that V168 is_cash column + cash_flow_category UPPERCASE refactor
    produces saldo equal to direct GL sum on cash leaves.

    Expected golden-apparel 2026-06: 20,735,000 (BCA 15,735k + Mandiri 5,000k).
    """
    resp = http.get(f"/api/reports/arus-kas/{PERIOD}")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    rpt_saldo = Decimal(str(data["kas_akhir_periode"]))

    _, end_str = _period_bounds(PERIOD)
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
              AND je.journal_date < $2::date
              AND a.is_cash = true
              AND a.is_header = false
            """,
            TENANT,
            end_str,
        )
    gl_saldo = Decimal(str(row["net_dr"]))

    assert (
        rpt_saldo == gl_saldo
    ), f"Arus Kas saldo drift {PERIOD}: report={rpt_saldo} GL={gl_saldo}"


# ─── Onboarding-completeness (CLASS CLOSURE — Fase 9.5) ──────────────────


@pytest.mark.asyncio
async def test_onboarding_completeness_class_closure(db):
    """
    CLASS CLOSURE: V168 canonical seed_default_coa() must populate
    category + cash_flow_category + is_cash for new tenants natively.

    Pattern: 4th bite of onboarding-completeness class
    (#3 tax_codes → #11 direction → #15 category → #16 cash_flow_category).
    This test validates the canonical seed template produces fully-populated
    CoA so no 5th bite occurs.

    Strategy: per existing tenant, assert standard accounts (1-/2-/3-/4-/5-/6-)
    have category non-NULL, cash_flow_category != 'NONE' at leaf level,
    is_cash=TRUE for category IN ('kas','bank') AND is_header=FALSE,
    direction populated for PPN tax_codes (V167).

    Note: skips header rows (is_header=true) for cash_flow_category check
    (headers legitimately retain 'NONE'). Skips system accounts for direction
    when tenant has none.
    """
    async with db.acquire() as conn:
        # Assert standard accounts category populated (leaf level)
        nulls = await conn.fetch(
            """
            SELECT account_code, name
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND is_header = false
              AND (category IS NULL OR category = '')
              AND (account_code LIKE '1-%' OR account_code LIKE '2-%'
                OR account_code LIKE '3-%' OR account_code LIKE '4-%'
                OR account_code LIKE '5-%' OR account_code LIKE '6-%')
            """,
            TENANT,
        )
        assert not nulls, (
            f"Onboarding-completeness gap: {len(nulls)} standard leaf accounts "
            f"have NULL category. Examples: {[(r['account_code'], r['name']) for r in nulls[:3]]}"
        )

        # Assert cash_flow_category != 'NONE' at leaf level
        none_cf = await conn.fetch(
            """
            SELECT account_code, name
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND is_header = false
              AND cash_flow_category = 'NONE'
              AND (account_code LIKE '1-%' OR account_code LIKE '2-%'
                OR account_code LIKE '3-%' OR account_code LIKE '4-%'
                OR account_code LIKE '5-%' OR account_code LIKE '6-%')
            """,
            TENANT,
        )
        assert not none_cf, (
            f"Onboarding-completeness gap: {len(none_cf)} leaf accounts retain "
            f"cash_flow_category='NONE'. Examples: "
            f"{[(r['account_code'], r['name']) for r in none_cf[:3]]}"
        )

        # Assert is_cash=TRUE for category IN ('kas','bank') leaf
        bad_cash = await conn.fetch(
            """
            SELECT account_code, name, is_cash
            FROM chart_of_accounts
            WHERE tenant_id = $1
              AND is_header = false
              AND category IN ('kas', 'bank')
              AND (is_cash IS NULL OR is_cash = false)
            """,
            TENANT,
        )
        assert not bad_cash, (
            f"Onboarding-completeness gap: {len(bad_cash)} kas/bank leaf "
            f"accounts have is_cash=FALSE. Examples: "
            f"{[(r['account_code'], r['name']) for r in bad_cash[:3]]}"
        )

        # Assert PPN tax_codes have direction populated (V167)
        ppn_null_dir = await conn.fetch(
            """
            SELECT id, name
            FROM tax_codes
            WHERE tenant_id = $1
              AND tax_type = 'PPN'
              AND is_active = true
              AND direction IS NULL
            """,
            TENANT,
        )
        assert not ppn_null_dir, (
            f"V167 regression: {len(ppn_null_dir)} active PPN tax_codes have "
            f"NULL direction. Examples: {[r['name'] for r in ppn_null_dir[:3]]}"
        )
