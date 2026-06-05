from goldset.schema import (
    Tier,
    Category,
    Behavior,
    QueryClass,
    Turn,
    GoldCase,
    A_INTENT_IN,
    A_TIER,
    A_TEXT_CONTAINS,
)


def test_goldcase_single_and_multi_turn():
    single = GoldCase(
        id="lookup_customers",
        category=Category.LOOKUP,
        turns=[Turn(query="daftar pelanggan", asserts=[(A_TIER, Tier.A)])],
    )
    assert single.turns[0].query == "daftar pelanggan"
    assert single.turns[0].asserts == [(A_TIER, Tier.A)]

    multi = GoldCase(
        id="followup_ar_top",
        category=Category.FOLLOWUP,
        turns=[
            Turn(
                query="piutang terbesar siapa?",
                asserts=[(A_INTENT_IN, ["query_ar_invoices", "query_ar_outstanding"])],
            ),
            Turn(query="berapa nilainya?", asserts=[(A_TEXT_CONTAINS, "Rp")]),
        ],
        why="ordinal+pronoun follow-up must resolve from session state",
    )
    assert len(multi.turns) == 2


def test_goldcase_query_class_defaults_none_and_is_optional():
    # existing positional construction (id, category, turns[, why]) stays valid
    c = GoldCase(
        "lookup_x",
        Category.LOOKUP,
        [Turn("x", [(A_TIER, Tier.A)])],
    )
    assert c.query_class is None
    tagged = GoldCase(
        "lookup_ar",
        Category.LOOKUP,
        [Turn("total piutang berapa", [(A_TIER, Tier.A)])],
        why="stock",
        query_class=QueryClass.STOCK,
    )
    assert tagged.query_class == QueryClass.STOCK


def test_behavior_and_queryclass_enums():
    assert Behavior.OVER_CLARIFY.value == "over_clarify"
    assert QueryClass.STOCK.value == "stock"
    assert QueryClass.FLOW.value == "flow"
