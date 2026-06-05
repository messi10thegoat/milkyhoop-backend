"""Unit + integration tests for driver_deltas (MoM contributing-facts layer).

Pure-math tests are DB-free and deterministic. One integration smoke runs
READ-only against the live dev DB (grapgrap tenant) for a fixed historical
period ("Mei 2026").

Run (bypassing the auth-scoped coverage gate in pytest.ini):
    pytest tests/chat/test_driver_deltas.py -o addopts="" -p no:cacheprovider -v
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.unified_agent.driver_deltas import (
    Driver,
    DriverDeltaSet,
    compute_delta,
    compute_driver_deltas,
    _resolve_periods,
)


# --------------------------------------------------------------------------- #
# Pure-math unit tests (no DB, deterministic)
# --------------------------------------------------------------------------- #
class TestComputeDelta:
    def test_basic_increase(self):
        d = compute_delta(Decimal("150"), Decimal("100"))
        assert d.current == Decimal("150.00")
        assert d.prior == Decimal("100.00")
        assert d.delta_abs == Decimal("50.00")
        assert d.delta_pct == Decimal("50.0")

    def test_basic_decrease(self):
        d = compute_delta(Decimal("80"), Decimal("100"))
        assert d.delta_abs == Decimal("-20.00")
        assert d.delta_pct == Decimal("-20.0")

    def test_negative_delta_when_current_below_prior(self):
        d = compute_delta(Decimal("0"), Decimal("250"))
        assert d.delta_abs == Decimal("-250.00")
        assert d.delta_pct == Decimal("-100.0")

    def test_prior_zero_returns_none_pct(self):
        # Avoid div-by-zero; renderer will say "naik dari 0".
        d = compute_delta(Decimal("500"), Decimal("0"))
        assert d.delta_pct is None
        assert d.delta_abs == Decimal("500.00")

    def test_both_zero_returns_none_pct(self):
        d = compute_delta(Decimal("0"), Decimal("0"))
        assert d.delta_pct is None
        assert d.delta_abs == Decimal("0.00")

    def test_round_half_up_pct(self):
        # (115 - 100) / 100 * 100 = 15.0 -> trivial; use a .x5 boundary
        # current=100.15, prior=100 -> 0.15% -> 0.2 at 1dp (HALF_UP on .15)
        d = compute_delta(Decimal("100.15"), Decimal("100"))
        assert d.delta_pct == Decimal("0.2")

    def test_round_half_up_amount(self):
        # 0.005 should round up to 0.01 (HALF_UP), not down/banker's.
        d = compute_delta(Decimal("1.005"), Decimal("1.000"))
        assert d.current == Decimal("1.01")
        assert d.delta_abs == Decimal("0.01")

    def test_round_half_up_pct_exact_half(self):
        # current=2, prior=8 -> -75.0% exact, but craft a .x5 case:
        # current=10.5, prior=8 -> (2.5/8)*100 = 31.25 -> 31.3 (HALF_UP on .25? -> 31.2/31.3)
        # 31.25 quantized to 1dp HALF_UP -> 31.3
        d = compute_delta(Decimal("10.5"), Decimal("8"))
        assert d.delta_pct == Decimal("31.3")

    def test_large_decimal_no_float_drift(self):
        # No float drift: 83281000 vs 0 -> None pct, exact abs.
        d = compute_delta(Decimal("83281000.00"), Decimal("0"))
        assert d.delta_abs == Decimal("83281000.00")
        assert d.delta_pct is None


class TestDriverDTO:
    def test_to_response_dict_floats(self):
        d = compute_delta(Decimal("150"), Decimal("100"))
        out = d.to_response_dict()
        assert out == {
            "current": 150.0,
            "prior": 100.0,
            "delta_abs": 50.0,
            "delta_pct": 50.0,
        }
        assert all(isinstance(out[k], float) for k in ("current", "prior", "delta_abs"))

    def test_to_response_dict_none_pct(self):
        d = compute_delta(Decimal("10"), Decimal("0"))
        out = d.to_response_dict()
        assert out["delta_pct"] is None


class TestRanking:
    def _set(self) -> DriverDeltaSet:
        return DriverDeltaSet(
            ar_outstanding=compute_delta(Decimal("110"), Decimal("100")),  # +10%
            ap_outstanding=compute_delta(Decimal("200"), Decimal("100")),  # +100%
            cash_balance=compute_delta(Decimal("70"), Decimal("100")),  # -30%
            expense_total=compute_delta(Decimal("105"), Decimal("100")),  # +5%
            revenue_total=compute_delta(Decimal("50"), Decimal("0")),  # None pct
            current_label="Mei 2026",
            prior_label="April 2026",
        )

    def test_ranked_by_abs_pct_order(self):
        ranked = self._set().ranked_by_abs_pct()
        names = [name for name, _ in ranked]
        # |100| > |30| > |10| > |5|, None-pct driver sorts last.
        assert names == [
            "ap_outstanding",
            "cash_balance",
            "ar_outstanding",
            "expense_total",
            "revenue_total",
        ]

    def test_none_pct_sorts_last(self):
        ranked = self._set().ranked_by_abs_pct()
        assert ranked[-1][0] == "revenue_total"
        assert ranked[-1][1].delta_pct is None

    def test_set_response_dict_shape(self):
        out = self._set().to_response_dict()
        assert set(out["drivers"].keys()) == {
            "ar_outstanding",
            "ap_outstanding",
            "cash_balance",
            "expense_total",
            "revenue_total",
        }
        assert out["period"]["current_label"] == "Mei 2026"
        assert out["period"]["prior_label"] == "April 2026"


class TestPeriodResolution:
    """Option A: complete (full-vs-full) vs in-progress (MTD vs same-range).

    All cases inject an explicit `today` so the branch is deterministic and the
    suite never depends on the real clock.
    """

    # ── COMPLETE branch (explicit/past month -> full vs full prior) ──
    def test_named_month_with_year_is_deterministic(self):
        # Mei 2026 is fully in the past relative to today=2026-06-05 -> COMPLETE.
        p = _resolve_periods("Mei 2026", today=date(2026, 6, 5))
        assert p["in_progress"] is False
        assert p["current_label"] == "Mei 2026"
        assert p["prior_label"] == "April 2026"
        assert p["current_start"].isoformat() == "2026-05-01"
        assert p["current_end"].isoformat() == "2026-05-31"
        assert p["prior_start"].isoformat() == "2026-04-01"
        assert p["prior_end"].isoformat() == "2026-04-30"

    def test_january_prior_crosses_year(self):
        p = _resolve_periods("Januari 2026", today=date(2026, 6, 5))
        assert p["in_progress"] is False
        assert p["prior_label"] == "Desember 2025"
        assert p["prior_start"].isoformat() == "2025-12-01"
        assert p["prior_end"].isoformat() == "2025-12-31"

    def test_complete_month_when_today_is_last_day(self):
        # On the month's last day the month is effectively complete -> full vs full
        # (today < cur_end is False), NOT MTD.
        p = _resolve_periods("bulan ini", today=date(2026, 6, 30))
        assert p["in_progress"] is False
        assert p["current_label"] == "Juni 2026"
        assert p["prior_label"] == "Mei 2026"
        assert p["current_start"].isoformat() == "2026-06-01"
        assert p["current_end"].isoformat() == "2026-06-30"
        assert p["prior_start"].isoformat() == "2026-05-01"
        assert p["prior_end"].isoformat() == "2026-05-31"

    # ── IN-PROGRESS branch (current month, today < month-end -> MTD vs same-range) ──
    def test_in_progress_bulan_ini_mtd_vs_same_range(self):
        p = _resolve_periods("bulan ini", today=date(2026, 6, 5))
        assert p["in_progress"] is True
        # current MTD window = [Jun 1, Jun 5]
        assert p["current_start"].isoformat() == "2026-06-01"
        assert p["current_end"].isoformat() == "2026-06-05"
        # prior same-range = [May 1, May 5]
        assert p["prior_start"].isoformat() == "2026-05-01"
        assert p["prior_end"].isoformat() == "2026-05-05"
        # range labels (I5 trace)
        assert p["current_label"] == "1–5 Juni 2026"
        assert p["prior_label"] == "1–5 Mei 2026"

    def test_in_progress_named_current_month_is_mtd(self):
        # Explicit "Juni 2026" while today is mid-June -> still in-progress MTD.
        p = _resolve_periods("Juni 2026", today=date(2026, 6, 5))
        assert p["in_progress"] is True
        assert p["current_end"].isoformat() == "2026-06-05"
        assert p["prior_end"].isoformat() == "2026-05-05"
        assert p["current_label"] == "1–5 Juni 2026"
        assert p["prior_label"] == "1–5 Mei 2026"

    def test_in_progress_prior_month_shorter_clamps_day(self):
        # today=30 March (March has 31 days so today < month-end -> in-progress);
        # prior month February (2026, non-leap) has 28 days -> prior same-day
        # clamps from 30 to Feb 28.
        p = _resolve_periods("bulan ini", today=date(2026, 3, 30))
        assert p["in_progress"] is True
        assert p["current_start"].isoformat() == "2026-03-01"
        assert p["current_end"].isoformat() == "2026-03-30"
        assert p["prior_start"].isoformat() == "2026-02-01"
        assert p["prior_end"].isoformat() == "2026-02-28"  # clamped (Feb 28 < 30)
        assert p["current_label"] == "1–30 Maret 2026"
        assert p["prior_label"] == "1–28 Februari 2026"

    def test_in_progress_prior_month_no_clamp_when_same_length(self):
        # today=15 May; prior month April (30 days) has day 15 -> no clamp.
        p = _resolve_periods("bulan ini", today=date(2026, 5, 15))
        assert p["in_progress"] is True
        assert p["prior_end"].isoformat() == "2026-04-15"  # no clamp
        assert p["prior_label"] == "1–15 April 2026"

    def test_month_end_31_is_complete_not_in_progress(self):
        # today=31 March IS the last day of March -> month complete -> full vs
        # full prior (NOT MTD). Documents the month-end edge explicitly.
        p = _resolve_periods("bulan ini", today=date(2026, 3, 31))
        assert p["in_progress"] is False
        assert p["current_label"] == "Maret 2026"
        assert p["prior_label"] == "Februari 2026"
        assert p["current_end"].isoformat() == "2026-03-31"
        assert p["prior_start"].isoformat() == "2026-02-01"
        assert p["prior_end"].isoformat() == "2026-02-28"

    def test_in_progress_january_prior_crosses_year(self):
        # today mid-Jan -> prior = December previous year, same-day, no clamp.
        p = _resolve_periods("bulan ini", today=date(2026, 1, 15))
        assert p["in_progress"] is True
        assert p["current_end"].isoformat() == "2026-01-15"
        assert p["prior_start"].isoformat() == "2025-12-01"
        assert p["prior_end"].isoformat() == "2025-12-15"
        assert p["current_label"] == "1–15 Januari 2026"
        assert p["prior_label"] == "1–15 Desember 2025"

    def test_in_progress_first_day_of_month(self):
        # today = day 1; MTD window = [1, 1], prior = [1, 1].
        p = _resolve_periods("bulan ini", today=date(2026, 6, 1))
        assert p["in_progress"] is True
        assert p["current_start"].isoformat() == "2026-06-01"
        assert p["current_end"].isoformat() == "2026-06-01"
        assert p["prior_start"].isoformat() == "2026-05-01"
        assert p["prior_end"].isoformat() == "2026-05-01"
        assert p["current_label"] == "1–1 Juni 2026"
        assert p["prior_label"] == "1–1 Mei 2026"

    def test_unrecognized_defaults_to_this_month(self):
        # Should not raise; falls back to "bulan ini".
        p = _resolve_periods("zzz nonsense zzz", today=date(2026, 6, 5))
        assert p["current_label"]
        assert p["prior_label"]
        # bulan ini mid-month -> in-progress
        assert p["in_progress"] is True


# --------------------------------------------------------------------------- #
# Integration smoke (READ-only, live dev DB, grapgrap tenant)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.integration
async def test_compute_driver_deltas_grapgrap_mei_2026():
    """compute_driver_deltas returns a well-formed set for a fixed period.

    READ-only: no journal writes. Verifies all 5 drivers present, all values
    Decimal, period labels populated (Mei 2026 vs April 2026).
    """
    result = await compute_driver_deltas("grapgrap", period_text="Mei 2026")

    assert isinstance(result, DriverDeltaSet)
    assert result.current_label == "Mei 2026"
    assert result.prior_label == "April 2026"
    assert result.current_start == "2026-05-01"
    assert result.current_end == "2026-05-31"
    assert result.prior_start == "2026-04-01"
    assert result.prior_end == "2026-04-30"

    drivers = result.drivers()
    assert set(drivers.keys()) == {
        "ar_outstanding",
        "ap_outstanding",
        "cash_balance",
        "expense_total",
        "revenue_total",
    }

    for name, drv in drivers.items():
        assert isinstance(drv, Driver), name
        assert isinstance(drv.current, Decimal), name
        assert isinstance(drv.prior, Decimal), name
        assert isinstance(drv.delta_abs, Decimal), name
        # delta_pct is Decimal or None (prior == 0)
        assert drv.delta_pct is None or isinstance(drv.delta_pct, Decimal), name
        # delta_abs must equal current - prior (deterministic identity)
        assert drv.delta_abs == (drv.current - drv.prior).quantize(
            Decimal("0.01")
        ), name

    # Ranking is well-formed and stable.
    ranked = result.ranked_by_abs_pct()
    assert len(ranked) == 5

    # Edge DTO converts cleanly to float/None.
    dto = result.to_response_dict()
    assert len(dto["drivers"]) == 5
    for d in dto["drivers"].values():
        assert isinstance(d["current"], float)
        assert d["delta_pct"] is None or isinstance(d["delta_pct"], float)

    print("[SMOKE] grapgrap Mei 2026 drivers:", dto["drivers"])
