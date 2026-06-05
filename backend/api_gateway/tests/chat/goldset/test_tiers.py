from goldset.schema import Tier
from goldset.tiers import derive_tier


def test_projection_is_tier_b():
    assert (
        derive_tier("query_gross_profit_projection", "projection_engine", "TEXT")
        == Tier.B
    )


def test_driver_deltas_is_tier_b():
    # The business-drivers (why-question) handler returns model_used="driver_deltas".
    # Like projection_engine, it is a deterministic reasoning path -> Tier B,
    # even though the intent string may be empty when resolved from the marker.
    assert derive_tier("", "driver_deltas", "TEXT") == Tier.B
    assert derive_tier("query_business_drivers", "driver_deltas", "TEXT") == Tier.B


def test_agent_loop_reasoning_is_tier_b():
    # open-ended reasoning falls to the gpt-4o-mini agent loop with multiple iterations
    assert derive_tier("", "gpt-4o-mini", "TEXT", iterations=3) == Tier.B


def test_lookup_calc_crud_chitchat_are_tier_a():
    assert derive_tier("calc_avg_harga_jual", "calc_engine", "TEXT") == Tier.A
    assert (
        derive_tier("query_customers_list", "gemini-2.5-flash-lite", "TEXT") == Tier.A
    )
    assert (
        derive_tier("create_customer", "gemini-2.5-flash-lite", "DIRECT_ACTION_PREVIEW")
        == Tier.A
    )
    assert derive_tier("query_profit_loss", "gemini-2.5-flash-lite", "TEXT") == Tier.A
    # chitchat uses gpt-4o-mini but is a single-iteration non-reasoning reply
    assert derive_tier("chitchat", "gpt-4o-mini", "TEXT", iterations=1) == Tier.A
