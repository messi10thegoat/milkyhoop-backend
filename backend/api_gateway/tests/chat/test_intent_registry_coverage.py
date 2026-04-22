"""
P3 — Registry coverage CI: fail if any calc_* intent registered in
PIPELINE_ENABLED_INTENTS has NEITHER:
  (a) a regex handler in classify_query_intent() in entity_extractor.py
  (b) at least one mention of the intent name in llm_intent_router.py
      ROUTER_SYSTEM_PROMPT (so LLM can route to it)

Prevents future DOAs (dead-on-arrival intents like calc_rank_items_by_stock
pre-P3-fix). This is a pure static-analysis test — no network/DB access.

Refs: docs/plans/2026-04-22-chat-regex-slot-fix-plan.md (P3 systemic)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


# Repo root relative to this file: tests/chat/ -> api_gateway root is 2 levels up
THIS = Path(__file__).resolve()
API_GATEWAY = THIS.parents[2]

EXTRACTOR = API_GATEWAY / "app" / "services" / "unified_agent" / "entity_extractor.py"
ROUTER = API_GATEWAY / "app" / "services" / "unified_agent" / "llm_intent_router.py"

# Intents that are ENUM-registered for telemetry/analytics only, not expected
# to be directly classified from natural language. Add with a short reason.
EXEMPT_INTENTS: set = {
    # Drill-down helpers: reached via session context, not direct utterance.
    "reformat_as_table",
    "drilldown_table",
    "contextual_drill_down",
    # Pre-existing DOA intents (2026-04-22 baseline, outside P3 commit scope).
    # These calc_* intents were already registered without regex or LLM prompt
    # coverage before P3; the P3 commit adds only calc_rank_items_by_stock and
    # calc_top_selling_items. Future commits must remove from this list as
    # regex/prompt coverage is added. DO NOT add new intents to this exempt list
    # without justification — the whole point is to prevent new DOAs.
    "calc_avg_harga_beli",
    "calc_count_bank_accounts",
    "calc_count_bills_active",
    "calc_count_bills_outstanding",
    "calc_count_customers_inactive",
    "calc_count_expenses_month",
    "calc_count_expenses_this_month",
    "calc_count_invoices_outstanding",
    "calc_count_items_inactive",
    "calc_count_sales_invoices_active",
    "calc_count_vendors_inactive",
    "calc_profit_margin_per_item",
    "calc_sum_bank_balance",
    "calc_sum_bills_outstanding",
    "calc_sum_harga_jual",
    "calc_sum_invoices_outstanding",
}


def load_pipeline_enabled() -> set[str]:
    """Parse PIPELINE_ENABLED_INTENTS = {...} literal from entity_extractor.py."""
    src = EXTRACTOR.read_text(encoding="utf-8")
    m = re.search(
        r"PIPELINE_ENABLED_INTENTS\s*=\s*\{(.*?)\n\}\s*\n",
        src,
        re.DOTALL,
    )
    assert m, "Could not locate PIPELINE_ENABLED_INTENTS literal"
    body = m.group(1)
    # Strip comments + extract quoted strings
    body_no_comments = re.sub(r"#[^\n]*", "", body)
    return set(re.findall(r"[\"\']([a-zA-Z_][a-zA-Z0-9_]*)[\"\']", body_no_comments))


def get_classify_body() -> str:
    """Extract the body of classify_query_intent() — used to detect regex coverage."""
    src = EXTRACTOR.read_text(encoding="utf-8")
    m = re.search(
        r"def classify_query_intent\([^)]*\)[^:]*:\n(.*?)(?:\n(?:def |class |\Z))",
        src,
        re.DOTALL,
    )
    assert m, "Could not locate classify_query_intent() function body"
    return m.group(1)


def has_regex_handler(intent: str, classify_body: str) -> bool:
    """Returns True if classify body contains `return "<intent>"` NOT in a
    commented-out line. (Best-effort — strips single-line comments first.)"""
    # Strip line comments (#...) to avoid false positives from disabled blocks.
    clean = "\n".join(re.sub(r"#.*$", "", line) for line in classify_body.splitlines())
    pattern = rf"return\s+[\"\']{re.escape(intent)}[\"\']"
    return re.search(pattern, clean) is not None


def in_router_prompt(intent: str, router_src: str) -> bool:
    """Returns True if the intent name appears anywhere inside the
    ROUTER_SYSTEM_PROMPT string literal."""
    m = re.search(
        r'ROUTER_SYSTEM_PROMPT\s*=\s*"""(.*?)"""',
        router_src,
        re.DOTALL,
    )
    if not m:
        return False
    prompt = m.group(1)
    return intent in prompt


def main() -> int:
    print("=" * 80)
    print("  P3 — Intent registry coverage (static)")
    print(f"  Scanning: {EXTRACTOR.name} + {ROUTER.name}")
    print("=" * 80)

    enabled = load_pipeline_enabled()
    classify_body = get_classify_body()
    router_src = ROUTER.read_text(encoding="utf-8")

    calc_intents = sorted([i for i in enabled if i.startswith("calc_")])
    print(f"\nFound {len(calc_intents)} calc_* intents in PIPELINE_ENABLED_INTENTS")

    dead: list[str] = []
    covered_regex: list[str] = []
    covered_prompt: list[str] = []

    for intent in calc_intents:
        if intent in EXEMPT_INTENTS:
            continue
        in_regex = has_regex_handler(intent, classify_body)
        in_prompt = in_router_prompt(intent, router_src)
        if in_regex:
            covered_regex.append(intent)
        elif in_prompt:
            covered_prompt.append(intent)
        else:
            dead.append(intent)

    print(f"  Covered by regex handler : {len(covered_regex)}")
    print(f"  Covered by LLM prompt    : {len(covered_prompt)} (regex absent)")
    print(f"  DEAD ON ARRIVAL          : {len(dead)}")
    for i in dead:
        print(f"    - {i}")

    if dead:
        print("\n" + "=" * 80)
        print(f"  FAIL: {len(dead)} intent(s) have neither regex nor prompt coverage.")
        print("  Add regex branch to classify_query_intent() OR mention in")
        print(
            "  ROUTER_SYSTEM_PROMPT. Alternatively add to EXEMPT_INTENTS with reason."
        )
        print("=" * 80)
        return 1

    print("\n" + "=" * 80)
    print("  PASS: all calc_* intents covered.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
