from goldset.schema import (
    Tier,
    Category,
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
