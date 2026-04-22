"""
P1 — word-boundary regex for AR/AP ranking intents.

Tests that `\\bar\\b` / `\\bap\\b` don't hijack substrings in "barang",
"bayar", "besar", "apa". Uses assert_final_intent from _helpers.

Refs: docs/plans/2026-04-22-chat-regex-slot-fix-plan.md (P1 root)
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid

from _helpers import assert_final_intent, stream_chat, get_intent_from_sse


# (text, expected_intent, prefix, is_negative, note)
CASES = [
    # Must route to correct calc intents (previously hijacked to AR)
    (
        "beban terbesar apa?",
        "calc_rank_expense_accounts",
        False,
        False,
        "P1: 'beban' + 'terbesar' must NOT hijack to AR",
    ),
    (
        "pengeluaran terbesar bulan ini",
        "calc_sum_expenses_this_month",
        True,
        False,
        "P1: pengeluaran must route to expense sum",
    ),
    # Regression guards — standalone `ar`/`piutang` still work
    (
        "piutang terbesar",
        "calc_rank_customers_by_ar",
        False,
        False,
        "Regression: standalone 'piutang terbesar' still maps to AR",
    ),
    (
        "ar terbesar",
        "calc_rank_customers_by_ar",
        False,
        False,
        "Regression: standalone 'ar terbesar' still maps to AR",
    ),
    (
        "siapa punya piutang paling banyak",
        "calc_rank_customers_by_ar",
        False,
        False,
        "Regression: 'piutang paling banyak' still maps to AR",
    ),
    # \bap\b inside "apa" must NOT hijack to AP; 'hutang' carries the intent
    (
        "apa hutang terbesar ke vendor?",
        "calc_rank_vendors_by_ap",
        False,
        False,
        "P1: 'apa' prefix must not break AP routing via 'hutang'",
    ),
]


# Negative assertion: "bayar yang paling besar" must NOT hijack to AR.
NEGATIVE_CASES = [
    (
        "bayar yang paling besar",
        "calc_rank_customers_by_ar",
        "P1 negative: 'bayar'+'besar' must not hijack to AR",
    ),
]


async def run_pos(text: str, expected: str, prefix: bool, note: str) -> dict:
    sess = str(uuid.uuid4())
    t0 = time.time()
    try:
        data = await assert_final_intent(
            text,
            expected,
            message=note,
            prefix=prefix,
            conversation_id=sess,
            session_id=sess,
            max_wait_s=60.0,
        )
        return {
            "name": note,
            "text": text,
            "passed": True,
            "elapsed_s": round(time.time() - t0, 2),
            "intent": data,
        }
    except AssertionError as e:
        return {
            "name": note,
            "text": text,
            "passed": False,
            "elapsed_s": round(time.time() - t0, 2),
            "error": str(e)[:600],
        }
    except Exception as e:
        return {
            "name": note,
            "text": text,
            "passed": False,
            "elapsed_s": round(time.time() - t0, 2),
            "error": f"UNEXPECTED: {type(e).__name__}: {e}"[:600],
        }


async def run_neg(text: str, forbidden_intent: str, note: str) -> dict:
    sess = str(uuid.uuid4())
    t0 = time.time()
    try:
        events = await stream_chat(
            text, conversation_id=sess, session_id=sess, timeout_s=60.0
        )
        intent_data = get_intent_from_sse(events)
        if intent_data is None:
            return {
                "name": note,
                "text": text,
                "passed": False,
                "elapsed_s": round(time.time() - t0, 2),
                "error": "no intent_classified event",
            }
        actual = str(intent_data.get("final_intent") or "")
        if actual == forbidden_intent:
            return {
                "name": note,
                "text": text,
                "passed": False,
                "elapsed_s": round(time.time() - t0, 2),
                "error": f"regression: got forbidden {actual!r}",
            }
        return {
            "name": note,
            "text": text,
            "passed": True,
            "elapsed_s": round(time.time() - t0, 2),
            "intent": intent_data,
        }
    except Exception as e:
        return {
            "name": note,
            "text": text,
            "passed": False,
            "elapsed_s": round(time.time() - t0, 2),
            "error": f"UNEXPECTED: {type(e).__name__}: {e}"[:600],
        }


async def main() -> int:
    print("=" * 80)
    print("  P1 — Intent regex word-boundary test")
    print("=" * 80)
    results = []
    for text, expected, prefix, _neg, note in CASES:
        print(f"\n-> {note}")
        print(f"   text={text!r}  expected={expected}")
        r = await run_pos(text, expected, prefix, note)
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"   [{status}] elapsed={r['elapsed_s']}s")
        if not r["passed"]:
            print(f"   error: {r['error']}")
        else:
            print(f"   intent: {r.get('intent')}")

    for text, forbidden, note in NEGATIVE_CASES:
        print(f"\n-> {note}")
        print(f"   text={text!r}  forbidden={forbidden}")
        r = await run_neg(text, forbidden, note)
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"   [{status}] elapsed={r['elapsed_s']}s")
        if not r["passed"]:
            print(f"   error: {r['error']}")
        else:
            print(f"   intent: {r.get('intent')}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print("\n" + "=" * 80)
    print(f"  P1 BOUNDARY RESULT: {passed}/{total} passed")
    print("=" * 80)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
