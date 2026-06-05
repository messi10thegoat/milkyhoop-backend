from collections import defaultdict
from goldset.schema import (
    Tier,
    Category,
    Behavior,
    QueryClass,
    A_INTENT_IN,
    A_TIER,
    A_TEXT_CONTAINS,
    A_TEXT_CONTAINS_ANY,
    A_TEXT_NOT_CONTAINS,
    A_HAS_TRACE,
    A_IS_CONFIRMATION,
    A_ABSTAINS,
)
from goldset.trace import has_trace

# NOTE: A_ABSTAINS is a reserved primitive (unit-tested, ready for data-missing cases).
# Markers are deliberately specific — bare "maaf" was excluded (false-positives on "maaf, ini datanya").
_ABSTAIN_MARKERS = [
    "belum bisa pastikan",
    "belum bisa memastikan",
    "belum bisa saya pastikan",
    "tidak yakin",
    "belum ada data",
    "tidak ditemukan",
    "data tidak tersedia",
]


def score_assert(assertion, obs):
    kind, val = assertion
    text = obs.get("text") or ""
    low = text.lower()
    if kind == A_INTENT_IN:
        return obs.get("intent") in set(val)
    if kind == A_TIER:
        return obs.get("tier") == val
    if kind == A_TEXT_CONTAINS:
        return val.lower() in low
    if kind == A_TEXT_CONTAINS_ANY:
        return any(v.lower() in low for v in val)
    if kind == A_TEXT_NOT_CONTAINS:
        return val.lower() not in low
    if kind == A_HAS_TRACE:
        return has_trace(text) is bool(val)
    if kind == A_IS_CONFIRMATION:
        return (obs.get("message_type") == "DIRECT_ACTION_PREVIEW") is bool(val)
    if kind == A_ABSTAINS:
        return any(m in low for m in _ABSTAIN_MARKERS) is bool(val)
    raise ValueError(f"unknown assert kind: {kind}")


def score_turn(turn, obs):
    return [(a, score_assert(a, obs)) for a in turn.asserts]


def score_behavior(obs, query_class):
    """2nd scoring dimension: did the bot DIRECT-answer vs CLARIFY appropriately?

    Returns (Behavior | None, ok: bool). None query_class => not scored (ok=True).
    """
    if query_class is None:
        return None, True  # behavior not scored for this case
    clarified = bool(obs.get("clarified"))
    if query_class == QueryClass.STOCK:
        # stock (piutang/hutang/balance) is point-in-time → must answer directly.
        return (Behavior.OVER_CLARIFY, False) if clarified else (Behavior.DIRECT, True)
    # FLOW: clarify-first is correct per charter; direct (with stated assumption) also ok
    return (Behavior.CLARIFY, True) if clarified else (Behavior.DIRECT, True)


def aggregate(case_results):
    """case_results: list[dict(id, category, passed: bool, turns: list[dict(asserts:[(a,ok)], obs)])]"""
    by_cat = defaultdict(lambda: [0, 0])  # cat -> [passed, total]
    routing = [0, 0]  # [correct, total] over A_INTENT_IN + A_TIER asserts
    confusion = defaultdict(int)  # (expected, actual_intent) -> count
    # Routing denominator audit (TASK 2): make the denominator never a mystery number.
    routing_gross = 0  # ALL A_INTENT_IN + A_TIER asserts, before clarify-exclusion
    routing_excluded_ids = []  # case_ids whose A_INTENT_IN was excluded (clarify on a stock/flow turn)
    tier_b_trace = [
        0,
        0,
    ]  # Catch B: [with_trace, total] over ALL Tier B responses (I5 baseline)
    behavior_counts = defaultdict(int)  # behavior value -> count
    behavior_scored = [0, 0]  # [ok, total] over cases with query_class set
    for cr in case_results:
        by_cat[cr["category"]][1] += 1
        by_cat[cr["category"]][0] += 1 if cr["passed"] else 0
        case_query_class = cr.get("query_class")
        # Behavior scorecard (cases with a stock/flow tag only).
        beh = cr.get("behavior")
        if beh is not None:
            behavior_counts[beh] += 1
            behavior_scored[1] += 1
            behavior_scored[0] += 1 if cr.get("behavior_ok") else 0
        for t in cr["turns"]:
            if t["obs"].get("tier") == Tier.B:
                tier_b_trace[1] += 1
                if has_trace(t["obs"].get("text") or ""):
                    tier_b_trace[0] += 1
            # Routing N/A on clarify: if the case is stock/flow-tagged AND the bot
            # clarified instead of answering, this is NOT a routing decision — exclude
            # its A_INTENT_IN from routing numerator/denominator and the confusion matrix.
            obs_clarified = bool(t["obs"].get("clarified"))
            routing_na_on_clarify = case_query_class is not None and obs_clarified
            for (kind, val), ok in t["asserts"]:
                if kind in (A_INTENT_IN, A_TIER):
                    routing_gross += 1  # gross denominator (audit), pre-exclusion
                if kind == A_INTENT_IN and routing_na_on_clarify:
                    # not a misroute — the bot asked instead of answering.
                    # Excluded from BOTH routing numerator/denominator AND confusion.
                    routing_excluded_ids.append(cr["id"])
                    continue
                if kind in (A_INTENT_IN, A_TIER):
                    routing[1] += 1
                    routing[0] += 1 if ok else 0
                    if kind == A_INTENT_IN and not ok:
                        confusion[(tuple(val), t["obs"].get("intent"))] += 1
    return {
        "by_category": {
            c.value if isinstance(c, Category) else c: v for c, v in by_cat.items()
        },
        "routing_accuracy": (routing[0] / routing[1]) if routing[1] else None,
        "routing_correct": routing[0],
        "routing_total": routing[1],
        # Denominator audit (TASK 2): routing_total == routing_gross - len(excluded).
        "routing_gross_asserts": routing_gross,  # all A_INTENT_IN + A_TIER asserts
        "routing_excluded_on_clarify": sorted(
            routing_excluded_ids
        ),  # case_ids excluded
        "routing_excluded_count": len(routing_excluded_ids),
        "confusion": {f"{exp}->{act}": n for (exp, act), n in confusion.items()},
        "tier_b_trace_rate": (tier_b_trace[0] / tier_b_trace[1])
        if tier_b_trace[1]
        else None,
        "tier_b_with_trace": tier_b_trace[0],
        "tier_b_total": tier_b_trace[1],
        "cases_passed": sum(1 for cr in case_results if cr["passed"]),
        "cases_total": len(case_results),
        "behavior_scorecard": {
            "direct": behavior_counts.get(Behavior.DIRECT.value, 0),
            "clarify": behavior_counts.get(Behavior.CLARIFY.value, 0),
            "over_clarify": behavior_counts.get(Behavior.OVER_CLARIFY.value, 0),
        },
        "behavior_pass_rate": (behavior_scored[0] / behavior_scored[1])
        if behavior_scored[1]
        else None,
        "behavior_ok": behavior_scored[0],
        "behavior_scored_total": behavior_scored[1],
    }
