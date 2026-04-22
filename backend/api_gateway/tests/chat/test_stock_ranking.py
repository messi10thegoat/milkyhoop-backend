"""
P3 — calc_rank_items_by_stock + calc_top_selling_items regex coverage.

Previously DOA intents (registered in PIPELINE_ENABLED_INTENTS but no regex
handler in classify_query_intent). After P1 boundary fix, these are no
longer hijacked to AR; this test proves the new regex branches fire.

Refs: docs/plans/2026-04-22-chat-regex-slot-fix-plan.md (P3)
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid

from _helpers import assert_final_intent


CASES = [
    # calc_rank_items_by_stock
    ("barang dengan stok terbanyak", "calc_rank_items_by_stock"),
    ("item barang apa saja yang stoknya paling banyak", "calc_rank_items_by_stock"),
    ("stok terbanyak apa?", "calc_rank_items_by_stock"),
    ("persediaan tertinggi", "calc_rank_items_by_stock"),
    ("item barang paling banyak stoknya", "calc_rank_items_by_stock"),
    # calc_top_selling_items
    ("item terlaris bulan ini", "calc_top_selling_items"),
    ("barang paling laku", "calc_top_selling_items"),
]


async def run_one(text: str, expected: str) -> dict:
    sess = str(uuid.uuid4())
    t0 = time.time()
    try:
        data = await assert_final_intent(
            text,
            expected,
            message=f"{expected}: {text!r}",
            conversation_id=sess,
            session_id=sess,
            max_wait_s=60.0,
        )
        return {
            "text": text,
            "expected": expected,
            "passed": True,
            "elapsed_s": round(time.time() - t0, 2),
            "intent": data,
        }
    except AssertionError as e:
        return {
            "text": text,
            "expected": expected,
            "passed": False,
            "elapsed_s": round(time.time() - t0, 2),
            "error": str(e)[:600],
        }
    except Exception as e:
        return {
            "text": text,
            "expected": expected,
            "passed": False,
            "elapsed_s": round(time.time() - t0, 2),
            "error": f"UNEXPECTED: {type(e).__name__}: {e}"[:400],
        }


async def main() -> int:
    print("=" * 80)
    print("  P3 — Stock/top-selling regex coverage test")
    print("=" * 80)
    results = []
    for text, expected in CASES:
        print(f"\n-> text={text!r}  expected={expected}")
        r = await run_one(text, expected)
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
    print(f"  P3 STOCK RESULT: {passed}/{total} passed")
    print("=" * 80)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
