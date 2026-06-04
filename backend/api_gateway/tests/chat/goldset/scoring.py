from collections import defaultdict
from goldset.schema import (
    Tier,
    Category,
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


def aggregate(case_results):
    """case_results: list[dict(id, category, passed: bool, turns: list[dict(asserts:[(a,ok)], obs)])]"""
    by_cat = defaultdict(lambda: [0, 0])  # cat -> [passed, total]
    routing = [0, 0]  # [correct, total] over A_INTENT_IN + A_TIER asserts
    confusion = defaultdict(int)  # (expected, actual_intent) -> count
    tier_b_trace = [
        0,
        0,
    ]  # Catch B: [with_trace, total] over ALL Tier B responses (I5 baseline)
    for cr in case_results:
        by_cat[cr["category"]][1] += 1
        by_cat[cr["category"]][0] += 1 if cr["passed"] else 0
        for t in cr["turns"]:
            if t["obs"].get("tier") == Tier.B:
                tier_b_trace[1] += 1
                if has_trace(t["obs"].get("text") or ""):
                    tier_b_trace[0] += 1
            for (kind, val), ok in t["asserts"]:
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
        "confusion": {f"{exp}->{act}": n for (exp, act), n in confusion.items()},
        "tier_b_trace_rate": (tier_b_trace[0] / tier_b_trace[1])
        if tier_b_trace[1]
        else None,
        "tier_b_with_trace": tier_b_trace[0],
        "tier_b_total": tier_b_trace[1],
        "cases_passed": sum(1 for cr in case_results if cr["passed"]),
        "cases_total": len(case_results),
    }
