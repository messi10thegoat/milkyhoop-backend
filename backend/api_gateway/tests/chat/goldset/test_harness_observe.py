from goldset.harness import observe


def test_observe_driver_deltas_resolves_business_drivers_intent():
    # The business-drivers handler returns model_used="driver_deltas" with no
    # tool_calls and bypasses intent_decision_log. observe() must resolve the
    # intent deterministically from the marker (mirrors projection_engine).
    resp = {
        "text": "Faktor pendorong keuangan (1–5 Juni 2026 vs 1–5 Mei 2026)...",
        "tool_calls": None,
        "model_used": "driver_deltas",
    }
    obs = observe(resp)
    assert obs["intent"] == "query_business_drivers"
    assert obs["tier"].value == "B"


def test_observe_query_key_fallback():
    # ARAP routing path emits args.query_key (not args.intent). observe() must
    # fall back to it so the turn is scored as a real routing decision.
    resp = {
        "text": "Total piutang Anda Rp 12.500.000.",
        "tool_calls": [{"args": {"query_key": "query_ar_by_customer"}}],
    }
    obs = observe(resp)
    assert obs["intent"] == "query_ar_by_customer"


def test_observe_intent_preferred_over_query_key():
    resp = {
        "text": "ok",
        "tool_calls": [{"args": {"intent": "query_ar_outstanding", "query_key": "x"}}],
    }
    assert observe(resp)["intent"] == "query_ar_outstanding"


def test_observe_clarified_true_when_asks_period_without_amount():
    resp = {"text": "Untuk periode mana yang Anda maksud?", "tool_calls": None}
    assert observe(resp)["clarified"] is True


def test_observe_clarified_true_on_real_bot_period_kapan_phrasing():
    # The live bot over-clarifies stock queries with this exact phrasing; the
    # clarify regex must catch it so it surfaces as OVER_CLARIFY, not a blind miss.
    resp = {
        "text": "Untuk periode kapan? Misal: bulan ini, 30 hari terakhir, April 2026.",
        "tool_calls": None,
    }
    assert observe(resp)["clarified"] is True


def test_observe_clarified_false_when_answer_has_amount():
    # An answer that names a period but also states an Rp amount is NOT a clarify.
    resp = {"text": "Laba periode Mei 2026: Rp 77.752.904.", "tool_calls": None}
    assert observe(resp)["clarified"] is False


def test_observe_clarified_false_on_plain_answer():
    resp = {"text": "Daftar pelanggan: Toko A, Toko B.", "tool_calls": None}
    assert observe(resp)["clarified"] is False
