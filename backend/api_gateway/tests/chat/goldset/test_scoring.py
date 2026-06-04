from goldset.schema import (
    Tier,
    A_INTENT_IN,
    A_TIER,
    A_TEXT_CONTAINS,
    A_TEXT_NOT_CONTAINS,
    A_IS_CONFIRMATION,
    A_ABSTAINS,
    A_HAS_TRACE,
)
from goldset.scoring import score_assert

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
