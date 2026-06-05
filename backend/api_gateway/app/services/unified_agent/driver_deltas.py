"""Month-over-month (MoM) driver deltas — journal-derived contributing facts.

READ-ONLY analytics layer. Computes, for a current period P and the prior
period P-1, five financial "drivers" derived 100% from the immutable ledger
(`journal_lines` + `journal_entries`), plus the absolute / percentage delta
between the two periods. This feeds a future "why-question contributing-facts"
feature (e.g. "kenapa laba turun bulan ini?" -> rank drivers by |delta_pct|).

This module does NOT mutate any journal. It is not wired into the orchestrator
(that is a later phase). No restart / commit performed by this module.

Iron Law compliance
--------------------
* Law 1 / 16 (Ledger supremacy): every number derives from `journal_lines`
  filtered by `journal_entries.status = 'POSTED'` with explicit `journal_date`
  bounds. NO read from wrapper/transaction tables (receive_payments,
  bill_payments, bills.amount_paid, bank_accounts.current_balance). The
  point-in-time snapshots (AR/AP/cash) use `SUM(debit) - SUM(credit)` with the
  account's normal balance sign; flows (expense/revenue) sum the period window.
  We compute directly from `journal_lines` (NOT via compute_ar/ap_outstanding)
  because those DB functions are "now"-only — they take only `p_tenant_id` and
  have no as-of date parameter, so they cannot produce point-in-time history.
* Law 9 (Deterministic) + Law 25 (Precision): all internal computation uses
  `Decimal` with `ROUND_HALF_UP`. NO float in computation/storage. Conversion
  to float happens ONLY at the response-DTO edge (`to_response_dict()`).
* Law 24 (Tenant isolation): every query runs under
  `SELECT set_config('app.tenant_id', $1, true)` inside `conn.transaction()`.
* Law 32 (Pool): connections come from the `get_db_pool()` singleton; this
  module never calls `asyncpg.connect()` or creates a pool.
* Law 31 (Route gate): READ-ONLY (no journal mutation) -> Gates 1-5 auto-pass.
  Only Gate 6 (read path = journal-derived) applies, and is honored: see the
  five SQL snapshots/flows below, each anchored on `journal_lines`.

Driver semantics
----------------
* ar_outstanding  (point-in-time, RECEIVABLE, debit-normal):
    SUM(debit) - SUM(credit) for POSTED entries with journal_date <= period_end.
* ap_outstanding  (point-in-time, PAYABLE, credit-normal):
    SUM(credit) - SUM(debit) for POSTED entries with journal_date <= period_end.
* cash_balance    (point-in-time, ASSET + is_cash, debit-normal):
    SUM(debit) - SUM(credit) for POSTED entries with journal_date <= period_end.
    Cash/bank accounts identified by `account_type = 'ASSET' AND is_cash = true`
    (the asset CoA rows linked 1:1 to bank_accounts). No compute_bank_balance()
    helper exists in this DB, and the current_balance cache is DEPRECATED
    (Law 21), so the journal-derived snapshot is the canonical path.
* expense_total   (FLOW, EXPENSE + COGS + OTHER_EXPENSE, debit-normal):
    SUM(debit) - SUM(credit) for POSTED entries with
    journal_date BETWEEN period_start AND period_end.
* revenue_total   (FLOW, REVENUE + OTHER_INCOME, credit-normal):
    SUM(credit) - SUM(debit) for POSTED entries with
    journal_date BETWEEN period_start AND period_end.

Period semantics ("Option A" — partial-month-aware)
----------------------------------------------------
The (period_start, period_end) bounds above are resolved by `_resolve_periods()`
with two branches:

* IN-PROGRESS current month (P is the month containing today AND today is before
  month-end): current window = [month_start, today]; prior window =
  [prior_month_start, prior_month_same_day(today)] (prior same-day clamped to the
  prior month's last day, e.g. today=31 -> 30/29/28). Snapshots (AR/AP/cash) are
  as-of today vs as-of prior_month_same_day (point-in-time; partial-month does
  not apply). Flows (expense/revenue) sum the MTD window vs the prior same-range
  window. Labels are RANGE labels: "1–5 Juni 2026" vs "1–5 Mei 2026".

* COMPLETE period (explicit/past month, fully in the past): full month vs full
  prior month. Labels are MONTH labels: "Mei 2026" vs "April 2026".

Apples-to-apples: a fully in-progress month never inflates a flow against a full
prior month — both sides cover the same number of elapsed days.

The expense/revenue account_type groupings were verified against the live CoA
enum: ASSET, COGS, EQUITY, EXPENSE, LIABILITY, OTHER_EXPENSE, OTHER_INCOME,
PAYABLE, RECEIVABLE, REVENUE (10 values). COGS is folded into expense_total so
the "expenses went up" why-question captures the full cost side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any

from .period_resolver import resolve_period

# Relative "current month" phrases — resolved from the (injectable) `today`
# rather than the global clock so period resolution stays clock-consistent.
_CURRENT_MONTH_RE = re.compile(r"\b(bulan\s+ini|current\s+month|this\s+month)\b")


def _re_search_current_month(text: str):
    return _CURRENT_MONTH_RE.search(text)


def _month_last_day(year: int, month: int) -> date:
    """Last calendar day of (year, month)."""
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    return next_first - timedelta(days=1)


# --------------------------------------------------------------------------- #
# account_type groupings (verified against live chart_of_accounts enum)
# --------------------------------------------------------------------------- #
_EXPENSE_TYPES = ("EXPENSE", "COGS", "OTHER_EXPENSE")
_REVENUE_TYPES = ("REVENUE", "OTHER_INCOME")

_ZERO = Decimal("0")
_PCT_QUANT = Decimal("0.1")  # 1 decimal place for delta_pct
_AMT_QUANT = Decimal("0.01")  # 2 decimal places for amounts (Law 25)

# Indonesian month labels for I5 trace (period labelling)
_MONTHS_ID = [
    "",
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]


def _q_amount(value: Decimal) -> Decimal:
    """Quantize an amount to 2 dp, ROUND_HALF_UP (Law 25)."""
    return Decimal(value).quantize(_AMT_QUANT, rounding=ROUND_HALF_UP)


def compute_delta(current: Decimal, prior: Decimal) -> "Driver":
    """Pure delta math — DB-free, deterministic, unit-testable.

    delta_abs = current - prior
    delta_pct = (current - prior) / prior * 100, ROUND_HALF_UP to 1 dp.
                None when prior == 0 (avoid div-by-zero; renderer says
                "naik dari 0").
    """
    current = _q_amount(current)
    prior = _q_amount(prior)
    delta_abs = _q_amount(current - prior)

    if prior == _ZERO:
        delta_pct: Optional[Decimal] = None
    else:
        raw = (current - prior) / prior * Decimal("100")
        delta_pct = raw.quantize(_PCT_QUANT, rounding=ROUND_HALF_UP)

    return Driver(
        current=current,
        prior=prior,
        delta_abs=delta_abs,
        delta_pct=delta_pct,
    )


@dataclass
class Driver:
    """A single driver's current/prior values and computed deltas (Decimal)."""

    current: Decimal
    prior: Decimal
    delta_abs: Decimal
    delta_pct: Optional[Decimal]  # None when prior == 0

    def to_response_dict(self) -> Dict[str, Any]:
        """Edge DTO conversion: Decimal -> float (Law 25 nuance).

        Decimal inside (compute/storage); float out (response DTO that FE reads).
        delta_pct stays None when prior == 0.
        """
        return {
            "current": float(self.current),
            "prior": float(self.prior),
            "delta_abs": float(self.delta_abs),
            "delta_pct": float(self.delta_pct) if self.delta_pct is not None else None,
        }


@dataclass
class DriverDeltaSet:
    """Full MoM driver set for period P vs P-1, plus period labels for I5 trace."""

    ar_outstanding: Driver
    ap_outstanding: Driver
    cash_balance: Driver
    expense_total: Driver
    revenue_total: Driver

    # Resolved period labels (e.g. "Mei 2026" vs "April 2026")
    current_label: str = ""
    prior_label: str = ""
    current_start: Optional[str] = None  # ISO date
    current_end: Optional[str] = None
    prior_start: Optional[str] = None
    prior_end: Optional[str] = None

    # Ordered driver name list for stable iteration / ranking
    _DRIVER_NAMES = (
        "ar_outstanding",
        "ap_outstanding",
        "cash_balance",
        "expense_total",
        "revenue_total",
    )

    def drivers(self) -> Dict[str, Driver]:
        """Return drivers keyed by name (stable order)."""
        return {name: getattr(self, name) for name in self._DRIVER_NAMES}

    def ranked_by_abs_pct(self) -> list[tuple[str, Driver]]:
        """Drivers ranked by |delta_pct| descending.

        Drivers with delta_pct == None (prior == 0) sort last, then by
        |delta_abs| descending as a deterministic tiebreaker.
        """

        def sort_key(item: tuple[str, Driver]):
            _name, d = item
            has_pct = d.delta_pct is not None
            pct_mag = abs(d.delta_pct) if has_pct else _ZERO
            return (has_pct, pct_mag, abs(d.delta_abs))

        return sorted(self.drivers().items(), key=sort_key, reverse=True)

    def to_response_dict(self) -> Dict[str, Any]:
        """Edge DTO: all drivers as float dicts + period metadata (Law 25)."""
        return {
            "period": {
                "current_label": self.current_label,
                "prior_label": self.prior_label,
                "current_start": self.current_start,
                "current_end": self.current_end,
                "prior_start": self.prior_start,
                "prior_end": self.prior_end,
            },
            "drivers": {
                name: drv.to_response_dict() for name, drv in self.drivers().items()
            },
        }


# --------------------------------------------------------------------------- #
# Period resolution helpers
# --------------------------------------------------------------------------- #
def _label_for_month(d: date) -> str:
    """'Mei 2026' style label for a date's month."""
    return f"{_MONTHS_ID[d.month]} {d.year}"


def _label_for_range(start: date, end: date) -> str:
    """'1–5 Juni 2026' style label for a partial-month range (same month).

    Used for the in-progress (MTD) branch so the I5 trace reflects the ACTUAL
    window compared, not the full calendar month. Assumes start/end share the
    same month (always true for MTD and the prior same-range window).
    """
    return f"{start.day}–{end.day} {_MONTHS_ID[start.month]} {start.year}"


def _prior_month_first(start: date) -> date:
    """First day of the month immediately preceding `start`'s month."""
    py, pm = (start.year, start.month - 1) if start.month > 1 else (start.year - 1, 12)
    return date(py, pm, 1)


def _prior_month_bounds(start: date) -> tuple[date, date]:
    """Given a month's start date, return (start, end) of the prior month."""
    prior_start = _prior_month_first(start)
    # end of prior month = day before current month's start
    prior_end = start - timedelta(days=1)
    return prior_start, prior_end


def _clamp_to_month(year: int, month: int, day: int) -> date:
    """Build date(year, month, day), clamping `day` to the month's last day.

    Handles the prior-month same-day edge: today=31 but the prior month only
    has 30 (or 28/29) days -> clamp to the prior month's last day. Deterministic,
    no exceptions.
    """
    # First day of the NEXT month, minus one day = last day of (year, month).
    if month == 12:
        next_first = date(year + 1, 1, 1)
    else:
        next_first = date(year, month + 1, 1)
    last_day = (next_first - timedelta(days=1)).day
    return date(year, month, min(day, last_day))


def _is_in_progress_month(cur_start: date, cur_end: date, today: date) -> bool:
    """True when period P is the month containing `today` AND today < month-end.

    "Option A" gate: an in-progress current month compares MTD-vs-same-range;
    a fully-past month compares full-vs-full-prior.
    """
    return (
        cur_start.year == today.year
        and cur_start.month == today.month
        and today < cur_end
    )


def _resolve_periods(period_text: str, today: Optional[date] = None) -> Dict[str, Any]:
    """Resolve current period P and prior period P-1 from a phrase ("Option A").

    Uses the shared `resolve_period()` (unified_agent.period_resolver) to get
    period P. Two branches:

    * IN-PROGRESS current month (P == month containing today, today < month-end):
        - current window = [month_start, today]
        - prior window   = [prior_month_start, prior_month_same_day(today)]
          (prior same-day clamped to the prior month's length, e.g. today=31 ->
          prior month with 30 days clamps to the 30th).
        - labels are RANGE labels ("1–5 Juni 2026" vs "1–5 Mei 2026").
        - snapshots use as-of `today` vs as-of `prior_month_same_day`; flows sum
          the MTD window vs the prior same-range window. (The SQL is identical;
          only the date bounds differ, so the same query functions apply.)

    * COMPLETE period (explicit/past month, fully in the past):
        - full month vs full prior month (legacy behaviour).
        - labels are MONTH labels ("Mei 2026" vs "April 2026").

    `today` is injectable for deterministic unit tests; defaults to
    `date.today()` (real server clock — this is backend code, not a workflow
    script). Defaults to "bulan ini" when the phrase is unrecognized.
    """
    if today is None:
        today = date.today()

    # `resolve_period()` always uses the global clock for RELATIVE phrases
    # ("bulan ini" / "current month") and for the unrecognized fallback. That
    # would desync from an injected test `today`. For the current-month case we
    # therefore build the month bounds from `today` directly, so resolution and
    # the in-progress check share one clock. Explicit/named months
    # ("Mei 2026") are absolute and resolve identically either way.
    _txt = (period_text or "").strip().lower()
    _is_relative_current = (
        (not _txt)
        or bool(_re_search_current_month(_txt))
        or (resolve_period(period_text) is None)  # unrecognized -> current month
    )
    if _is_relative_current:
        cur_month_start = date(today.year, today.month, 1)
        cur_month_end = _month_last_day(today.year, today.month)
    else:
        resolved = resolve_period(period_text) or resolve_period("bulan ini")
        cur_month_start = date.fromisoformat(resolved["start_date"])
        cur_month_end = date.fromisoformat(resolved["end_date"])

    if _is_in_progress_month(cur_month_start, cur_month_end, today):
        # ── IN-PROGRESS (MTD vs prior same-range) ──
        cur_start = cur_month_start
        cur_end = today  # as-of today / MTD window end

        prior_month_start = _prior_month_first(cur_month_start)
        prior_same_day = _clamp_to_month(
            prior_month_start.year, prior_month_start.month, today.day
        )
        prior_start = prior_month_start
        prior_end = prior_same_day

        return {
            "current_start": cur_start,
            "current_end": cur_end,
            "current_label": _label_for_range(cur_start, cur_end),
            "prior_start": prior_start,
            "prior_end": prior_end,
            "prior_label": _label_for_range(prior_start, prior_end),
            "in_progress": True,
        }

    # ── COMPLETE period (full vs full prior) ──
    cur_start = cur_month_start
    cur_end = cur_month_end
    prior_start, prior_end = _prior_month_bounds(cur_month_start)

    return {
        "current_start": cur_start,
        "current_end": cur_end,
        "current_label": _label_for_month(cur_start),
        "prior_start": prior_start,
        "prior_end": prior_end,
        "prior_label": _label_for_month(prior_start),
        "in_progress": False,
    }


# --------------------------------------------------------------------------- #
# Journal-derived snapshot / flow queries (Gate 6: every number from ledger)
# --------------------------------------------------------------------------- #

# Point-in-time snapshot: net balance of a debit-normal account_type set
# as-of an end date (cumulative, POSTED only). debit - credit.
_SNAPSHOT_DEBIT_NORMAL_SQL = """
    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS net
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = $1
      AND je.status = 'POSTED'
      AND je.journal_date <= $2
      AND coa.account_type = ANY($3::text[])
"""

# Point-in-time snapshot: net balance of a credit-normal account_type set
# as-of an end date (cumulative, POSTED only). credit - debit.
_SNAPSHOT_CREDIT_NORMAL_SQL = """
    SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) AS net
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = $1
      AND je.status = 'POSTED'
      AND je.journal_date <= $2
      AND coa.account_type = ANY($3::text[])
"""

# Cash/bank snapshot: ASSET accounts flagged is_cash, debit-normal, as-of date.
_SNAPSHOT_CASH_SQL = """
    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS net
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = $1
      AND je.status = 'POSTED'
      AND je.journal_date <= $2
      AND coa.account_type = 'ASSET'
      AND coa.is_cash = true
"""

# Flow: net over a period window for a debit-normal account_type set.
_FLOW_DEBIT_NORMAL_SQL = """
    SELECT COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS net
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = $1
      AND je.status = 'POSTED'
      AND je.journal_date BETWEEN $2 AND $3
      AND coa.account_type = ANY($4::text[])
"""

# Flow: net over a period window for a credit-normal account_type set.
_FLOW_CREDIT_NORMAL_SQL = """
    SELECT COALESCE(SUM(jl.credit) - SUM(jl.debit), 0) AS net
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = $1
      AND je.status = 'POSTED'
      AND je.journal_date BETWEEN $2 AND $3
      AND coa.account_type = ANY($4::text[])
"""


def _to_decimal(value: Any) -> Decimal:
    """Coerce an asyncpg numeric (Decimal/None) to a quantized Decimal."""
    if value is None:
        return _ZERO
    return _q_amount(Decimal(value))


async def _ar_snapshot(conn, tenant_id: str, as_of: date) -> Decimal:
    row = await conn.fetchval(
        _SNAPSHOT_DEBIT_NORMAL_SQL, tenant_id, as_of, ["RECEIVABLE"]
    )
    return _to_decimal(row)


async def _ap_snapshot(conn, tenant_id: str, as_of: date) -> Decimal:
    row = await conn.fetchval(
        _SNAPSHOT_CREDIT_NORMAL_SQL, tenant_id, as_of, ["PAYABLE"]
    )
    return _to_decimal(row)


async def _cash_snapshot(conn, tenant_id: str, as_of: date) -> Decimal:
    row = await conn.fetchval(_SNAPSHOT_CASH_SQL, tenant_id, as_of)
    return _to_decimal(row)


async def _expense_flow(conn, tenant_id: str, start: date, end: date) -> Decimal:
    row = await conn.fetchval(
        _FLOW_DEBIT_NORMAL_SQL, tenant_id, start, end, list(_EXPENSE_TYPES)
    )
    return _to_decimal(row)


async def _revenue_flow(conn, tenant_id: str, start: date, end: date) -> Decimal:
    row = await conn.fetchval(
        _FLOW_CREDIT_NORMAL_SQL, tenant_id, start, end, list(_REVENUE_TYPES)
    )
    return _to_decimal(row)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
async def compute_driver_deltas(
    tenant_id: str, period_text: str = "bulan ini"
) -> DriverDeltaSet:
    """Compute MoM driver deltas for period P (period_text) vs P-1 (prior month).

    READ-ONLY. Every number is journal-derived (Gate 6). Internal math is
    Decimal; convert to float only via `DriverDeltaSet.to_response_dict()`.

    Args:
        tenant_id: tenant identifier (text PK, e.g. "grapgrap").
        period_text: Indonesian period phrase; defaults to "bulan ini".

    Returns:
        DriverDeltaSet with 5 drivers + resolved period labels.
    """
    from ..db_pool import get_db_pool  # Law 32: singleton pool

    periods = _resolve_periods(period_text)
    cur_start = periods["current_start"]
    cur_end = periods["current_end"]
    prior_start = periods["prior_start"]
    prior_end = periods["prior_end"]

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Law 24: RLS context (asyncpg cannot bind SET LOCAL params).
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            # --- point-in-time snapshots (as-of end of each period) ---
            ar_cur = await _ar_snapshot(conn, tenant_id, cur_end)
            ar_pri = await _ar_snapshot(conn, tenant_id, prior_end)

            ap_cur = await _ap_snapshot(conn, tenant_id, cur_end)
            ap_pri = await _ap_snapshot(conn, tenant_id, prior_end)

            cash_cur = await _cash_snapshot(conn, tenant_id, cur_end)
            cash_pri = await _cash_snapshot(conn, tenant_id, prior_end)

            # --- flows (over each period window) ---
            exp_cur = await _expense_flow(conn, tenant_id, cur_start, cur_end)
            exp_pri = await _expense_flow(conn, tenant_id, prior_start, prior_end)

            rev_cur = await _revenue_flow(conn, tenant_id, cur_start, cur_end)
            rev_pri = await _revenue_flow(conn, tenant_id, prior_start, prior_end)

    return DriverDeltaSet(
        ar_outstanding=compute_delta(ar_cur, ar_pri),
        ap_outstanding=compute_delta(ap_cur, ap_pri),
        cash_balance=compute_delta(cash_cur, cash_pri),
        expense_total=compute_delta(exp_cur, exp_pri),
        revenue_total=compute_delta(rev_cur, rev_pri),
        current_label=periods["current_label"],
        prior_label=periods["prior_label"],
        current_start=cur_start.isoformat(),
        current_end=cur_end.isoformat(),
        prior_start=prior_start.isoformat(),
        prior_end=prior_end.isoformat(),
    )
