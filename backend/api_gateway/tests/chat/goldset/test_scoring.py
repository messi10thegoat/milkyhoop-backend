from goldset.schema import (
    Tier,
    Behavior,
    QueryClass,
    A_INTENT_IN,
    A_TIER,
    A_TEXT_CONTAINS,
    A_TEXT_NOT_CONTAINS,
    A_IS_CONFIRMATION,
    A_ABSTAINS,
    A_HAS_TRACE,
)
from goldset.scoring import score_assert, score_behavior

OBS = {
    "intent": "query_gross_profit_projection",
    "tier": Tier.B,
    "text": "Berdasarkan data April–Mei 2026: laba kotor Rp 77.752.904. Asumsi margin tetap.",
    "message_type": "TEXT",
}


def test_intent_in_pass_and_fail():
    assert score_assert((A_INTENT_IN, ["query_gross_profit_projection"]), OBS) is True
    assert score_assert((A_INTENT_IN, ["calc_profit_margin_per_item"]), OBS) is False


def test_tier_equals():
    assert score_assert((A_TIER, Tier.B), OBS) is True
    assert score_assert((A_TIER, Tier.A), OBS) is False


def test_text_contains_case_insensitive():
    assert score_assert((A_TEXT_CONTAINS, "asumsi"), OBS) is True


def test_text_not_contains():
    assert score_assert((A_TEXT_NOT_CONTAINS, "Penjualan Outstanding"), OBS) is True


def test_is_confirmation():
    preview = {**OBS, "message_type": "DIRECT_ACTION_PREVIEW"}
    assert score_assert((A_IS_CONFIRMATION, True), preview) is True
    assert score_assert((A_IS_CONFIRMATION, True), OBS) is False


def test_abstains():
    abst = {**OBS, "text": "Maaf, saya belum bisa pastikan angkanya."}
    assert score_assert((A_ABSTAINS, True), abst) is True
    assert score_assert((A_ABSTAINS, True), OBS) is False


def test_has_trace():
    assert score_assert((A_HAS_TRACE, True), OBS) is True


# ---- score_behavior: 2nd scoring dimension ----
def test_behavior_stock_clarified_is_over_clarify_fail():
    beh, ok = score_behavior({"clarified": True}, QueryClass.STOCK)
    assert beh == Behavior.OVER_CLARIFY
    assert ok is False


def test_behavior_stock_not_clarified_is_direct_pass():
    beh, ok = score_behavior({"clarified": False}, QueryClass.STOCK)
    assert beh == Behavior.DIRECT
    assert ok is True


def test_behavior_flow_clarified_is_clarify_pass():
    beh, ok = score_behavior({"clarified": True}, QueryClass.FLOW)
    assert beh == Behavior.CLARIFY
    assert ok is True


def test_behavior_flow_not_clarified_is_direct_pass():
    beh, ok = score_behavior({"clarified": False}, QueryClass.FLOW)
    assert beh == Behavior.DIRECT
    assert ok is True


def test_behavior_none_query_class_not_scored():
    beh, ok = score_behavior({"clarified": True}, None)
    assert beh is None
    assert ok is True
