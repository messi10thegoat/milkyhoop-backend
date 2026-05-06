"""Unit tests for Bug #1 fix: proper-noun guard for active_entity injection.

Validates the `_proper_noun_matches` helper used by both
llm_intent_router.py (primary guard at injection site) and
orchestrator.py (secondary guard at session-fallback inject).
"""

from app.services.unified_agent.llm_intent_router import _proper_noun_matches


def test_pronoun_carry_over_returns_true():
    """Pronoun-only follow-up: no proper noun in user_text → keep active context."""
    assert _proper_noun_matches("faktur dia berapa?", "PT Maju Jaya") is True


def test_explicit_different_proper_noun_returns_false():
    """Explicit different 2-word proper noun → override (skip injection)."""
    assert (
        _proper_noun_matches("Judita Kandou itu ada piutang berapa?", "PT Maju Jaya")
        is False
    )


def test_explicit_same_continuation_returns_true():
    """Explicit same name continuation → keep injection."""
    assert (
        _proper_noun_matches("PT Maju Jaya outstanding berapa", "PT Maju Jaya") is True
    )


def test_lowercase_substring_returns_true():
    """Lowercase reference (no proper noun pattern) → carry-over branch."""
    assert _proper_noun_matches("cek piutang maju jaya", "PT Maju Jaya") is True


def test_empty_user_text_returns_true():
    assert _proper_noun_matches("", "PT Maju Jaya") is True


def test_empty_active_name_returns_true():
    assert _proper_noun_matches("Judita Kandou piutang berapa", "") is True


def test_partial_token_overlap_returns_true():
    """If active_name is multi-word and one token overlaps, treat as same entity."""
    # "PT Maju" appears in user text → token "Maju" overlaps with "PT Maju Jaya"
    assert _proper_noun_matches("PT Maju kemarin bayar", "PT Maju Jaya") is True


def test_completely_different_proper_noun_returns_false():
    assert (
        _proper_noun_matches("Sintia Runtuwene punya outstanding?", "PT Maju Jaya")
        is False
    )
