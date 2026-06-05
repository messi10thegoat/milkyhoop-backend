"""
Regression: READ_PROMOTE / COMPLEX_READ path must NOT raise UnboundLocalError
on the function-scope variable `_qci_guard`.

BUG (prod 500, 2026-06-04 night):
    UnboundLocalError: cannot access local variable '_qci_guard' where it is
    not associated with a value

ROOT CAUSE (orchestrator.py, process_message):
    `_qci_guard` / `_qci_entity_name` are assigned at line ~7215, but ONLY inside
    the `if extraction is not None:` block. That block runs only for ACTION /
    SIMPLE_READ intents. When `LLM_ROUTER_READ_PROMOTE=true`, the PHASE 2 LLM
    router primary path widens its tiers to include COMPLEX_READ
    (`_rp_primary_tiers = ("ACTION","SIMPLE_READ","COMPLEX_READ")`), so a
    COMPLEX_READ message enters `if USE_LLM_ROUTER and _intent in _rp_primary_tiers:`
    with `extraction is None`. Inside `if _llm_extraction is not None:` the
    PROJECTION_OVERRIDE *defensive* check reads `_qci_guard` (orchestrator.py
    ~line 8085: `if _qci_guard == "query_gross_profit_projection":`) — but that
    var was never assigned for COMPLEX_READ → UnboundLocalError → HTTP 500.

    Last night's log signature:
        [INTENT] intent=COMPLEX_READ user='...'
        [LLM_ROUTER_PRIMARY] adopted/intent=query_profit_loss(1.00)
        500 Internal Server Error

FIX (commit f2a68bde, marker FIX_READ_PROMOTE), orchestrator.py ~line 6172:
    Pre-initialize at FUNCTION scope, before any `_intent`-gated branch:
        _qci_guard = None
        _qci_entity_name = None
    so the COMPLEX_READ primary path reads a defined `None` instead of crashing.

WHY THIS TEST EXERCISES THE GUARDED PATH (not a grep):
    "analisis laba rugi bulan ini" is deterministically classified COMPLEX_READ
    by `_infer_intent` (keyword "analisis" in the COMPLEX_READ list,
    system_prompt.py). With USE_LLM_ROUTER=true + LLM_ROUTER_READ_PROMOTE=true
    (live env), the LLM router returns a READY query_profit_loss pick, so
    `extraction` stays None AND the PHASE 2 primary branch runs and reaches the
    PROJECTION_OVERRIDE `_qci_guard` read at orchestrator.py ~line 8085.
    Without the f2a68bde init this request raises UnboundLocalError → 500.
    The assertions below require HTTP 200 + no error envelope, so a reverted
    guard turns this test RED.

This is an HTTP-integration test (highest fidelity) — read-only against the
live Grapgrap test tenant, same target/credentials as the rest of tests/chat/.
No writes are performed (profit/loss is a pure READ query).

Run standalone:
    cd backend/api_gateway/tests/chat && python3 test_read_promote_qci_guard_regression.py
Or via the suite runner aggregation (run_read_promote_regression_tests).
"""
import asyncio
import sys
import uuid

from conftest import TestSuite


# Messages that deterministically infer as COMPLEX_READ (see _infer_intent
# complex_words: "analisis", "evaluasi", "margin") AND drive the LLM router to
# a ready query_ pick — the exact shape that reached the un-init `_qci_guard`
# read for the prod 500.
_TRIGGERS = [
    ("read_promote_qci_guard__analisis_laba_rugi", "analisis laba rugi bulan ini"),
    ("read_promote_qci_guard__evaluasi_laba_rugi", "evaluasi laba rugi bulan ini"),
    ("read_promote_qci_guard__analisis_margin", "analisis margin laba bulan ini"),
]

# Substrings that would indicate the bug resurfaced (error envelope / traceback
# leaking into the chat response).
_ERROR_MARKERS = [
    "unboundlocalerror",
    "_qci_guard",
    "internal server error",
    "traceback (most recent call last)",
    "500",
]


async def _assert_no_unbound(suite: TestSuite, name: str, text: str) -> bool:
    """Send `text`; PASS iff HTTP 200 + non-error envelope + no UnboundLocalError.

    `TestSuite.send()` calls `resp.raise_for_status()`, so a 500 (the original
    bug) raises here and is recorded as a failure — exactly the regression we
    want to catch.
    """
    from conftest import TestResult

    try:
        data = await suite.send(text, conversation_id=str(uuid.uuid4()))
    except Exception as e:
        # raise_for_status on a 500 lands here → regression caught.
        suite.record(
            TestResult(name=name, passed=False, error=f"HTTP/exception: {str(e)[:120]}")
        )
        return False

    msg_type = data.get("message_type") or ""
    resp_text = data.get("text") or ""
    latency = data.get("latency_ms") or 0
    model = data.get("model_used") or ""

    errors = []

    # The bot must produce a real answer, not an error message_type.
    if msg_type.upper() in ("ERROR", "EXCEPTION"):
        errors.append(f"error message_type={msg_type}")

    low = resp_text.lower()
    for marker in _ERROR_MARKERS:
        if marker in low:
            errors.append(f"error marker '{marker}' in response")

    # Sanity: a successful profit/loss read should mention rupiah / laba.
    if not errors and not any(k in low for k in ("rp", "laba", "rugi", "pendapatan")):
        # Not strictly an error envelope, but flag — the path didn't answer.
        errors.append("response lacks any profit/loss content")

    suite.record(
        TestResult(
            name=name,
            passed=len(errors) == 0,
            latency_ms=latency,
            model=model,
            message_type=msg_type,
            error="; ".join(errors),
            response_text=resp_text[:200],
        )
    )
    return len(errors) == 0


async def run_read_promote_regression_tests(suite: TestSuite):
    print("\n── READ_PROMOTE _qci_guard UnboundLocalError regression (f2a68bde) ──")
    for name, text in _TRIGGERS:
        await _assert_no_unbound(suite, name, text)


async def _main():
    suite = TestSuite()
    print("=" * 72)
    print("  READ_PROMOTE _qci_guard regression — target https://milkyhoop.com")
    print("=" * 72)
    await run_read_promote_regression_tests(suite)
    suite.print_summary()
    failed = sum(1 for r in suite.results if not r.passed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(_main())
