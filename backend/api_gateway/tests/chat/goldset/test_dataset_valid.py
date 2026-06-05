from goldset.dataset import CASES
from goldset.schema import Category, QueryClass, A_INTENT_IN
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


def test_stock_flow_tagging_is_complete_and_consistent():
    # Stock/flow tagging is a consistency policy (2-dim scoring), not ad-hoc.
    # STOCK = point-in-time balance (piutang/hutang/saldo/stok/ekuitas) → DIRECT expected.
    # FLOW  = period-bound flow query lacking a period → CLARIFY acceptable.
    by_class = {c.id: c.query_class for c in CASES if c.query_class is not None}
    stock = {cid for cid, qc in by_class.items() if qc == QueryClass.STOCK}
    flow = {cid for cid, qc in by_class.items() if qc == QueryClass.FLOW}
    assert stock == {
        "lookup_ar",
        "lookup_ap",
        "followup_ar_top_value",
        "followup_domain_carry",
    }, f"unexpected STOCK set: {stock}"
    assert flow == {
        "lookup_profit_ambiguous",
        "lookup_omzet_ambiguous",
    }, f"unexpected FLOW set: {flow}"


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
