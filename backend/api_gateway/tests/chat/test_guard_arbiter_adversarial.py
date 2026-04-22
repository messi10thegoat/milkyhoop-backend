"""Adversarial unit tests for GuardArbiter (P2 batch 2).

Unit-fallback harness: calls GuardArbiter.decide() directly with crafted inputs.
Trade-off: does NOT exercise orchestrator wiring, classify_query_intent regex,
or telemetry persistence. Those surfaces covered separately by:
  - test_intent_regex_boundary.py (regex)
  - integration smoke (orchestrator → telemetry)

Scope matches audit matrix:
  - ARAP_GUARD       (5 cases)
  - ARAP_SUMMARY     (5 cases)
  - LIST_GUARD       (5 cases)
  - REFORMAT_GUARD   (5 cases)
  - DRILL_GUARD      (5 cases)
  - QUERY_BOOST      (5 cases)
  - CALC_GUARD       (5 cases)
  - MFG_GUARD        (5 cases)
  - DE_ESCALATE      (6 cases)
  - Cross-cutting    (N cases)

Ref: docs/plans/2026-04-22-p2-guard-audit-matrix.md v1.0
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from app.services.unified_agent.guard_arbiter import (
    GuardArbiter,
    GuardMatch,
    ArbitrationDecision,
)


# ---------- helpers -----------------------------------------------------------


def _arb():
    return GuardArbiter()


def _decide(
    arb: GuardArbiter,
    *,
    llm_intent: str = "query",
    llm_confidence: float = 0.5,
    llm_needs_escalation: bool = False,
    guard_matches: dict | None = None,
    session_state: dict | None = None,
    user_text: str = "tampilkan daftar lengkap sekarang juga",
    context_hint: bool = False,
) -> ArbitrationDecision:
    return arb.decide(
        llm_intent=llm_intent,
        llm_confidence=llm_confidence,
        llm_domain=None,
        llm_needs_escalation=llm_needs_escalation,
        guard_matches=guard_matches or {},
        session_state=session_state,
        user_text=user_text,
        context_hint=context_hint,
    )


def _pending_state(active: bool = True, ttl_seconds: int = 300):
    if not active:
        return {}
    return {
        "pending_clarification": {
            "slot_type": "period",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat(),
        }
    }


# ---------- ARAP_GUARD (CONFIDENCE_AWARE 0.85) --------------------------------

ARAP_CASES = [
    # (id, llm_intent, llm_conf, qci_intent, same_family, expected_winner, expected_final)
    # Note: "same intent" case still technically 'ARAP_GUARD wins' per policy (guard.final == llm), behaviorally a no-op.
    (
        "arap-1-same-intent",
        "calc_rank_customers_by_ar",
        0.92,
        "calc_rank_customers_by_ar",
        True,
        "ARAP_GUARD",
        "calc_rank_customers_by_ar",
    ),
    (
        "arap-2-llm-wins",
        "calc_rank_expense_accounts",
        0.93,
        "query_ar_outstanding",
        False,
        "LLM",
        "calc_rank_expense_accounts",
    ),
    (
        "arap-3-summary-win",
        "query_vendor_ap",
        0.55,
        "query_ap_outstanding",
        False,
        "ARAP_SUMMARY_GUARD",
        "query_ap_outstanding",
    ),
    (
        "arap-4-same-family",
        "query_ar_outstanding",
        0.70,
        "query_customer_ar",
        True,
        "ARAP_GUARD",
        "query_customer_ar",
    ),
    (
        "arap-5-low-conf-override",
        "query_ap_outstanding",
        0.60,
        "query_customer_ar",
        False,
        "ARAP_GUARD",
        "query_customer_ar",
    ),
]


@pytest.mark.parametrize(
    "tid,llm_intent,llm_conf,qci,same_family,winner,final", ARAP_CASES
)
def test_arap_guard(tid, llm_intent, llm_conf, qci, same_family, winner, final):
    arb = _arb()
    matches = {
        "ARAP_GUARD": GuardMatch(
            "ARAP_GUARD", qci, metadata={"same_family": same_family}
        )
    }
    # case 3 requires ARAP_SUMMARY to be active — entity intent + no name
    if tid == "arap-3-summary-win":
        matches["ARAP_SUMMARY_GUARD"] = GuardMatch("ARAP_SUMMARY_GUARD", qci)
    d = _decide(
        arb, llm_intent=llm_intent, llm_confidence=llm_conf, guard_matches=matches
    )
    assert (
        d.winner == winner
    ), f"{tid}: winner {d.winner} != {winner} (reason={d.reason})"
    assert d.final_intent == final, f"{tid}: final_intent {d.final_intent} != {final}"


# ---------- ARAP_SUMMARY (ALWAYS_WIN nested) ----------------------------------

# summary_fires=True means guard was constructed (entity-intent + no name);
# False means real orchestrator would not have built ARAP_SUMMARY (name present),
# so here we simulate by passing same_family=True so ARAP yields.
ARAP_SUMMARY_CASES = [
    ("summary-1-no-name", "query_vendor_ap", "query_ap_outstanding", True),
    ("summary-2-with-name", "query_vendor_ap", "query_ap_outstanding", False),
    ("summary-3-rec-resolved", "query_vendor_ap", "query_ap_outstanding", False),
    ("summary-4-customer-summary", "query_customer_ar", "query_ar_outstanding", True),
    ("summary-5-empty-extraction", "query_customer_ar", "query_ar_outstanding", True),
]


@pytest.mark.parametrize("tid,llm_intent,qci,summary_fires", ARAP_SUMMARY_CASES)
def test_arap_summary_guard(tid, llm_intent, qci, summary_fires):
    arb = _arb()
    # When summary doesn't fire (name present), legacy keeps entity intent.
    # Modeled: high LLM conf (0.90) >= threshold 0.85, different intent,
    # not same_family → CONFIDENCE_AWARE ARAP yields to LLM.
    matches = {
        "ARAP_GUARD": GuardMatch("ARAP_GUARD", qci, metadata={"same_family": False}),
    }
    if summary_fires:
        matches["ARAP_SUMMARY_GUARD"] = GuardMatch("ARAP_SUMMARY_GUARD", qci)
    d = _decide(arb, llm_intent=llm_intent, llm_confidence=0.90, guard_matches=matches)
    if summary_fires:
        assert d.winner == "ARAP_SUMMARY_GUARD", f"{tid}: {d.winner}"
        assert d.final_intent == qci
    else:
        # ARAP with same_family=True at high conf → yields to LLM, entity intent retained
        assert d.winner == "LLM", f"{tid}: {d.winner}"
        assert d.final_intent == llm_intent


# ---------- LIST_GUARD (ALWAYS_WIN) -------------------------------------------

LIST_CASES = [
    (
        "list-1-overdue-to-list",
        "query_customers_with_overdue",
        "query_customers_list",
        "LIST_GUARD",
        "query_customers_list",
    ),
    (
        "list-2-asymmetric-no-fire",
        "query_customers_list",
        None,
        "LLM_OR_NO_GUARD",
        "query_customers_list",
    ),
    (
        "list-3-both-list",
        "query_vendors_list",
        None,
        "LLM_OR_NO_GUARD",
        "query_vendors_list",
    ),
    (
        "list-4-both-overdue",
        "query_customers_with_overdue",
        None,
        "LLM_OR_NO_GUARD",
        "query_customers_with_overdue",
    ),
    (
        "list-5-vendors-mapping",
        "query_vendors_with_overdue",
        "query_vendors_list",
        "LIST_GUARD",
        "query_vendors_list",
    ),
]


@pytest.mark.parametrize("tid,llm_intent,list_match,winner,final", LIST_CASES)
def test_list_guard(tid, llm_intent, list_match, winner, final):
    arb = _arb()
    matches = {}
    if list_match:
        matches["LIST_GUARD"] = GuardMatch("LIST_GUARD", list_match)
    d = _decide(arb, llm_intent=llm_intent, llm_confidence=0.80, guard_matches=matches)
    if winner == "LIST_GUARD":
        assert d.winner == "LIST_GUARD"
        assert d.final_intent == final
    else:
        assert d.winner in ("LLM", "NO_GUARD")
        assert d.final_intent == final


# ---------- REFORMAT_GUARD (ALWAYS_WIN, REC-exempt) ---------------------------

REFORMAT_CASES = [
    (
        "reformat-1-basic",
        "query_ar_outstanding",
        0.6,
        "tampilkan dalam tabel sekarang",
        None,
        "REFORMAT_GUARD",
    ),
    (
        "reformat-2-short-rec",
        "chitchat",
        0.9,
        "tabel dong",
        {"last_domain": "ar"},
        "REFORMAT_GUARD",
    ),
    (
        "reformat-3-chit-high",
        "chitchat",
        0.95,
        "tampilkan tabel silakan",
        None,
        "REFORMAT_GUARD",
    ),
    (
        "reformat-4-empty-ctx",
        "query_ar_outstanding",
        0.8,
        "format tabel bro",
        None,
        "REFORMAT_GUARD",
    ),
    (
        "reformat-5-pending-clar",
        "chitchat",
        0.9,
        "tabel",
        _pending_state(True),
        "PENDING_CLAR",
    ),
]


@pytest.mark.parametrize(
    "tid,llm_intent,llm_conf,user_text,session_state,winner", REFORMAT_CASES
)
def test_reformat_guard(tid, llm_intent, llm_conf, user_text, session_state, winner):
    arb = _arb()
    matches = {"REFORMAT_GUARD": GuardMatch("REFORMAT_GUARD", "reformat_as_table")}
    d = _decide(
        arb,
        llm_intent=llm_intent,
        llm_confidence=llm_conf,
        guard_matches=matches,
        session_state=session_state,
        user_text=user_text,
    )
    assert d.winner == winner, f"{tid}: winner={d.winner} reason={d.reason}"


# ---------- DRILL_GUARD (CONTEXT_AWARE) ---------------------------------------

DRILL_CASES = [
    (
        "drill-1-ctx-ok",
        True,
        "query_ar_outstanding",
        0.4,
        "detailnya dong",
        "DRILL_GUARD",
    ),
    ("drill-2-no-ctx", False, "chitchat", 0.5, "detailnya", "LLM"),
    ("drill-3-bad-last", False, "chitchat", 0.5, "rinciannya", "LLM"),
    (
        "drill-4-drill+reformat",
        True,
        "query_ar_outstanding",
        0.6,
        "tabel detailnya dong",
        "REFORMAT_GUARD",
    ),
    ("drill-5-pending-clar", True, "chitchat", 0.5, "detailnya", "PENDING_CLAR"),
]


@pytest.mark.parametrize("tid,ctx_ok,llm_intent,llm_conf,user_text,winner", DRILL_CASES)
def test_drill_guard(tid, ctx_ok, llm_intent, llm_conf, user_text, winner):
    arb = _arb()
    matches = {
        "DRILL_GUARD": GuardMatch(
            "DRILL_GUARD", "contextual_drill_down", metadata={"context_ok": ctx_ok}
        )
    }
    session_state = None
    if tid == "drill-4-drill+reformat":
        matches["REFORMAT_GUARD"] = GuardMatch("REFORMAT_GUARD", "reformat_as_table")
    if tid == "drill-5-pending-clar":
        session_state = _pending_state(True)
    d = _decide(
        arb,
        llm_intent=llm_intent,
        llm_confidence=llm_conf,
        guard_matches=matches,
        session_state=session_state,
        user_text=user_text,
    )
    assert d.winner == winner, f"{tid}: {d.winner}"


# ---------- QUERY_BOOST (WEAK_FALLBACK) ---------------------------------------

QUERY_BOOST_CASES = [
    ("qb-1-ambig-boost", "ambiguous", 0.3, False, False, "QUERY_BOOST"),
    (
        "qb-2-high-chitchat",
        "chitchat",
        0.95,
        False,
        False,
        "QUERY_BOOST",
    ),  # chitchat triggers weak-fallback regardless of conf
    (
        "qb-3-strong-llm",
        "query_items_list",
        0.6,
        False,
        False,
        "LLM",
    ),  # not ambiguous, conf>=0.5
    ("qb-4-context-hint", "ambiguous", 0.3, False, True, "LLM"),
    ("qb-5-escalate", "query_ar_outstanding", 0.6, True, False, "QUERY_BOOST"),
]


@pytest.mark.parametrize(
    "tid,llm_intent,llm_conf,esc,ctx_hint,winner", QUERY_BOOST_CASES
)
def test_query_boost(tid, llm_intent, llm_conf, esc, ctx_hint, winner):
    arb = _arb()
    matches = {"QUERY_BOOST": GuardMatch("QUERY_BOOST", "query_items_list")}
    d = _decide(
        arb,
        llm_intent=llm_intent,
        llm_confidence=llm_conf,
        llm_needs_escalation=esc,
        guard_matches=matches,
        context_hint=ctx_hint,
    )
    assert d.winner == winner, f"{tid}: {d.winner} reason={d.reason}"


# ---------- CALC_GUARD (CONFIDENCE_AWARE 0.85) --------------------------------

CALC_CASES = [
    # Same intent → CALC fires but is behaviorally no-op (final == llm_intent)
    (
        "calc-1-same-intent",
        "calc_rank_customers_by_ar",
        0.90,
        "calc_rank_customers_by_ar",
        "CALC_GUARD",
        "calc_rank_customers_by_ar",
    ),
    (
        "calc-2-both-match",
        "calc_rank_expense_accounts",
        0.85,
        "calc_rank_expense_accounts",
        "CALC_GUARD",
        "calc_rank_expense_accounts",
    ),
    (
        "calc-3-llm-wins",
        "calc_top_customers_by_frequency",
        0.90,
        "calc_rank_customers_by_ar",
        "LLM",
        "calc_top_customers_by_frequency",
    ),
    (
        "calc-4-calc-wins-low",
        "query_items_list",
        0.70,
        "calc_rank_items_by_stock",
        "CALC_GUARD",
        "calc_rank_items_by_stock",
    ),
    (
        "calc-5-no-regex",
        "calc_rank_items_by_sales",
        0.70,
        None,
        "LLM_OR_NO_GUARD",
        "calc_rank_items_by_sales",
    ),
]


@pytest.mark.parametrize("tid,llm_intent,llm_conf,qci,winner,final", CALC_CASES)
def test_calc_guard(tid, llm_intent, llm_conf, qci, winner, final):
    arb = _arb()
    matches = {}
    if qci:
        matches["CALC_GUARD"] = GuardMatch(
            "CALC_GUARD", qci, metadata={"same_family": False}
        )
    d = _decide(
        arb, llm_intent=llm_intent, llm_confidence=llm_conf, guard_matches=matches
    )
    if winner == "LLM_OR_NO_GUARD":
        assert d.winner in ("LLM", "NO_GUARD")
    else:
        assert d.winner == winner, f"{tid}: {d.winner} (reason={d.reason})"
    assert d.final_intent == final


# ---------- MFG_GUARD (ALWAYS_WIN) --------------------------------------------

MFG_CASES = [
    (
        "mfg-1-items-collision",
        "query_items_list",
        0.9,
        "query_bom_list",
        "MFG_GUARD",
        "query_bom_list",
    ),
    (
        "mfg-2-chitchat",
        "chitchat",
        0.3,
        "query_bom_detail",
        "MFG_GUARD",
        "query_bom_detail",
    ),
    (
        "mfg-3-diff-mfg",
        "query_work_order_list",
        0.8,
        "query_production_active",
        "MFG_GUARD",
        "query_production_active",
    ),
    ("mfg-4-no-regex", "create_bill", 0.85, None, "LLM_OR_NO_GUARD", "create_bill"),
    (
        "mfg-5-pending-clar",
        "chitchat",
        0.9,
        "query_bom_list",
        "PENDING_CLAR",
        "clarification_response",
    ),
]


@pytest.mark.parametrize("tid,llm_intent,llm_conf,qci,winner,final", MFG_CASES)
def test_mfg_guard(tid, llm_intent, llm_conf, qci, winner, final):
    arb = _arb()
    matches = {}
    if qci:
        matches["MFG_GUARD"] = GuardMatch("MFG_GUARD", qci)
    state = _pending_state(True) if tid == "mfg-5-pending-clar" else None
    d = _decide(
        arb,
        llm_intent=llm_intent,
        llm_confidence=llm_conf,
        guard_matches=matches,
        session_state=state,
    )
    if winner == "LLM_OR_NO_GUARD":
        assert d.winner in ("LLM", "NO_GUARD")
        assert d.final_intent == final
    else:
        assert d.winner == winner, f"{tid}: {d.winner}"
        assert d.final_intent == final


# ---------- DE_ESCALATE (post-processor helper) -------------------------------

DE_ESC_CASES = [
    ("de-1-query-no-rec", "query_ar_outstanding", True, False, True, False, True),
    ("de-2-rec-context", "query_ar_outstanding", True, True, True, True, False),
    ("de-3-create", "create_sales_invoice", True, False, True, True, False),
    ("de-4-not-pipeline", "query_fancy_new", True, False, False, True, False),
    ("de-5-pending-clar", "query_ar_outstanding", True, True, True, True, False),
    ("de-6-no-escalation", "query_ar_outstanding", False, False, True, False, False),
]


@pytest.mark.parametrize(
    "tid,intent,needs_esc,context_hint,pipeline_enabled,expected_esc,expected_fired",
    DE_ESC_CASES,
)
def test_de_escalate(
    tid, intent, needs_esc, context_hint, pipeline_enabled, expected_esc, expected_fired
):
    arb = _arb()

    def pipe(_i):
        return pipeline_enabled

    new_esc, fired = arb.apply_de_escalate(
        intent=intent,
        needs_escalation=needs_esc,
        context_hint=context_hint,
        is_pipeline_enabled_fn=pipe,
    )
    assert new_esc == expected_esc, f"{tid}: new_esc={new_esc}"
    assert fired == expected_fired, f"{tid}: fired={fired}"


# ---------- Cross-cutting interactions ----------------------------------------


def test_cross_reformat_beats_arap():
    arb = _arb()
    matches = {
        "REFORMAT_GUARD": GuardMatch("REFORMAT_GUARD", "reformat_as_table"),
        "ARAP_GUARD": GuardMatch(
            "ARAP_GUARD", "query_ar_outstanding", metadata={"same_family": False}
        ),
    }
    d = _decide(arb, llm_intent="chitchat", llm_confidence=0.4, guard_matches=matches)
    assert d.winner == "REFORMAT_GUARD"
    assert d.conflict is True


def test_cross_pending_clar_overrides_all():
    arb = _arb()
    matches = {
        "REFORMAT_GUARD": GuardMatch("REFORMAT_GUARD", "reformat_as_table"),
        "MFG_GUARD": GuardMatch("MFG_GUARD", "query_bom_list"),
        "ARAP_GUARD": GuardMatch(
            "ARAP_GUARD", "query_ar_outstanding", metadata={"same_family": False}
        ),
    }
    d = _decide(
        arb,
        llm_intent="chitchat",
        llm_confidence=0.9,
        guard_matches=matches,
        session_state=_pending_state(True),
        user_text="tabel",
    )
    assert d.winner == "PENDING_CLAR"
    assert d.final_intent == "clarification_response"


def test_cross_rec_short_skips_most_guards():
    arb = _arb()
    matches = {
        "ARAP_GUARD": GuardMatch(
            "ARAP_GUARD", "query_ar_outstanding", metadata={"same_family": False}
        ),
    }
    d = _decide(
        arb,
        llm_intent="chitchat",
        llm_confidence=0.6,
        guard_matches=matches,
        session_state={"last_domain": "ar"},
        user_text="oke",  # 1 word short follow-up
    )
    assert d.winner == "REC", f"got {d.winner}"


def test_cross_drill_beats_list():
    arb = _arb()
    matches = {
        "DRILL_GUARD": GuardMatch(
            "DRILL_GUARD", "contextual_drill_down", metadata={"context_ok": True}
        ),
        "LIST_GUARD": GuardMatch("LIST_GUARD", "query_customers_list"),
    }
    d = _decide(arb, llm_intent="chitchat", llm_confidence=0.4, guard_matches=matches)
    assert d.winner == "DRILL_GUARD"


def test_cross_no_guards_passthrough():
    arb = _arb()
    d = _decide(
        arb, llm_intent="query_items_list", llm_confidence=0.9, guard_matches={}
    )
    assert d.winner == "NO_GUARD"
    assert d.final_intent == "query_items_list"
