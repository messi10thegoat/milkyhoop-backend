from goldset.dataset import CASES
from goldset.schema import Category, A_INTENT_IN
from goldset.known_intents import KNOWN_INTENTS


def test_ids_unique():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"


def test_every_category_covered():
    cats = {c.category for c in CASES}
    assert cats == set(Category), f"missing categories: {set(Category) - cats}"


def test_adversarial_block_is_substantial_and_justified():
    adv = [c for c in CASES if c.category == Category.ADVERSARIAL]
    assert len(adv) >= 6, "need a real adversarial trap suite"
    assert all(
        c.why.strip() for c in adv
    ), "every adversarial case must explain the trap"


def test_min_size():
    assert len(CASES) >= 28


def test_all_expected_intents_are_known():
    # Catch A: an A_INTENT_IN label the system never emits is a typo/drift bug, not a misroute.
    bad = []
    for c in CASES:
        for t in c.turns:
            for kind, val in t.asserts:
                if kind == A_INTENT_IN:
                    for intent in val:
                        if intent not in KNOWN_INTENTS:
                            bad.append((c.id, intent))
    assert not bad, f"unknown intent labels (typo/drift, not misroute): {bad}"
