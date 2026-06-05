"""Unit tests for the Phase 3 polished renderer (_render_contributing_facts)
and the Phase 4 InsightEngine driver->insight mapping (_build_driver_insights).

Pure / DB-free: feeds constructed DriverDeltaSet objects (no DB, no network,
no LLM). Run (bypassing the auth-scoped coverage gate in pytest.ini):

    pytest tests/chat/test_contributing_facts.py -o addopts="" \
        -p no:cacheprovider -v
"""
from __future__ import annotations

from decimal import Decimal


from app.services.unified_agent.driver_deltas import (
    DriverDeltaSet,
    compute_delta,
)
from app.services.unified_agent.orchestrator import UnifiedAgent


def _agent():
    # Skip __init__ (it builds an LLMRouter from env); the render/mapping methods
    # only touch pure class attributes + a lazy insight_engine import.
    return UnifiedAgent.__new__(UnifiedAgent)


def _delta_set(
    ar=("0", "0"),
    ap=("0", "0"),
    cash=("0", "0"),
    expense=("0", "0"),
    revenue=("0", "0"),
    current_label="1–5 Juni 2026",
    prior_label="1–5 Mei 2026",
):
    """Build a DriverDeltaSet from (current, prior) string pairs per driver."""
    return DriverDeltaSet(
        ar_outstanding=compute_delta(Decimal(ar[0]), Decimal(ar[1])),
        ap_outstanding=compute_delta(Decimal(ap[0]), Decimal(ap[1])),
        cash_balance=compute_delta(Decimal(cash[0]), Decimal(cash[1])),
        expense_total=compute_delta(Decimal(expense[0]), Decimal(expense[1])),
        revenue_total=compute_delta(Decimal(revenue[0]), Decimal(revenue[1])),
        current_label=current_label,
        prior_label=prior_label,
    )


# --------------------------------------------------------------------------- #
# Phase 3 — renderer
# --------------------------------------------------------------------------- #
class TestRenderContributingFacts:
    def test_non_causal_line_present(self):
        ds = _delta_set(revenue=("100", "200"))
        out = _agent()._render_contributing_facts(ds)
        assert "fakta kontributor" in out
        assert "bukan sebab-akibat" in out

    def test_trace_markers_present(self):
        # has_trace() requires a SOURCE word + a PERIOD token. "Berdasarkan
        # jurnal" supplies the source; the period labels supply month+year.
        ds = _delta_set(revenue=("100", "200"))
        out = _agent()._render_contributing_facts(ds)
        assert "Berdasarkan jurnal" in out or "journal-derived" in out
        assert "1–5 Juni 2026" in out
        assert "1–5 Mei 2026" in out
        assert "vs" in out

    def test_ranking_by_abs_pct(self):
        # revenue +100% (100->200... wait, build a clear ordering):
        #   revenue: 50 -> 150  = +200%
        #   expense: 100 -> 110 = +10%
        ds = _delta_set(revenue=("150", "50"), expense=("110", "100"))
        out = _agent()._render_contributing_facts(ds)
        # The biggest |delta_pct| mover must appear before the smaller one.
        rev_pos = out.index("Pendapatan")
        exp_pos = out.index("Pengeluaran")
        assert rev_pos < exp_pos

    def test_nearzero_suppressed_into_stabil_tail(self):
        # cash moves +0.2% (<0.5 threshold) -> folded into "(stabil: ...)".
        ds = _delta_set(
            revenue=("150", "50"),  # +200% mover
            cash=("1002", "1000"),  # +0.2% near-zero
        )
        out = _agent()._render_contributing_facts(ds)
        assert "(stabil:" in out
        assert "Saldo Kas & Bank" in out.split("(stabil:")[1]
        # The near-zero mover must NOT be in the numbered movers section.
        movers_section = out.split("(stabil:")[0]
        assert "Saldo Kas & Bank" not in movers_section

    def test_none_pct_phrasing_naik_dari_0(self):
        # prior == 0, current > 0 -> "naik dari 0"
        ds = _delta_set(revenue=("500", "0"))
        out = _agent()._render_contributing_facts(ds)
        assert "naik dari 0" in out

    def test_none_pct_phrasing_turun_ke_0(self):
        # prior == 0, current < 0 -> "turun ke 0"
        ds = _delta_set(cash=("-500", "0"))
        out = _agent()._render_contributing_facts(ds)
        assert "turun ke 0" in out

    def test_idr_formatting_dot_thousands(self):
        ds = _delta_set(revenue=("1500000", "1000000"))
        out = _agent()._render_contributing_facts(ds)
        assert "Rp 1.500.000" in out
        assert "Rp 1.000.000" in out

    def test_all_five_drivers_accounted_for(self):
        # Give every driver a clear non-zero move so none are suppressed.
        ds = _delta_set(
            ar=("300", "100"),
            ap=("250", "100"),
            cash=("400", "100"),
            expense=("110", "100"),
            revenue=("150", "50"),
        )
        out = _agent()._render_contributing_facts(ds)
        for label in (
            "Piutang Outstanding",
            "Hutang Outstanding",
            "Saldo Kas & Bank",
            "Pengeluaran",
            "Pendapatan",
        ):
            assert label in out


# --------------------------------------------------------------------------- #
# Phase 4 — InsightEngine mapping (floor-gated)
# --------------------------------------------------------------------------- #
class TestDriverInsights:
    def test_expense_spike_fires_with_large_base(self):
        # expense 5M -> 10M = +100% (>50) AND base 10M >= 1M floor -> spike fires.
        ds = _delta_set(expense=("10000000", "5000000"))
        insights = _agent()._build_driver_insights(ds)
        types = {i.insight_type for i in insights}
        assert "expense_spike" in types

    def test_expense_spike_suppressed_below_floor(self):
        # expense 50k -> 10k = +400% (>50) BUT base 50k < 1M floor -> NO spike.
        ds = _delta_set(expense=("50000", "10000"))
        insights = _agent()._build_driver_insights(ds)
        types = {i.insight_type for i in insights}
        assert "expense_spike" not in types

    def test_flat_yields_facts_only(self):
        # everything stable, zero burn, zero balance -> no rule should fire.
        ds = _delta_set(
            ar=("100", "100"),
            ap=("100", "100"),
            cash=("100", "100"),
            expense=("0", "0"),
            revenue=("100", "100"),
        )
        insights = _agent()._build_driver_insights(ds)
        assert insights == []

    def test_negative_cash_fires_cashflow_risk(self):
        # negative cash balance -> "Saldo Negatif" high-severity rule fires.
        ds = _delta_set(cash=("-500000", "100000"), expense=("0", "0"))
        insights = _agent()._build_driver_insights(ds)
        types = {i.insight_type for i in insights}
        assert "cashflow_risk" in types

    def test_fired_insight_surfaces_in_render(self):
        ds = _delta_set(expense=("10000000", "5000000"))
        out = _agent()._render_contributing_facts(ds)
        # The spike insight title (or emoji-tagged severity) must appear.
        assert "Pengeluaran Melonjak" in out
        # facts still present below the insight (non-causal facts intact).
        assert "fakta kontributor" in out

    def test_facts_only_when_no_rule(self):
        # tiny expense base, no negative cash, no burn-driven runway risk:
        # render should contain NO insight marker line, only facts.
        ds = _delta_set(
            revenue=("150", "50"),
            expense=("0", "0"),
            cash=("0", "0"),
        )
        out = _agent()._render_contributing_facts(ds)
        assert "[HIGH]" not in out
        assert "[MEDIUM]" not in out
        assert "fakta kontributor" in out
