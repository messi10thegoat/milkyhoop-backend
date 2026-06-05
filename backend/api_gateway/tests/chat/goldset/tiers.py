from goldset.schema import Tier

# Open-ended reasoning intents that MUST run on the slower, traceable Tier B path.
TIER_B_INTENTS = {
    "query_gross_profit_projection",
    # add other deterministic reasoning intents here as they are built
}


def derive_tier(intent, model_used, message_type, iterations=1):
    intent = intent or ""
    if intent in TIER_B_INTENTS:
        return Tier.B
    if model_used == "projection_engine":
        return Tier.B
    if model_used == "driver_deltas":
        return Tier.B
    # agent-loop reasoning: gpt-4o-mini doing multi-iteration work that is NOT chitchat
    if model_used == "gpt-4o-mini" and (iterations or 1) > 1:
        return Tier.B
    return Tier.A
