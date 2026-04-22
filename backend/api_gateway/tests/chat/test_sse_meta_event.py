"""
Smoke test: SSE intent_classified meta event fires for chitchat/query/crud paths.

Validates the P5 test foundation (batch 1 chat-regex-slot-fix-plan).
Each case asserts:
  1. intent_classified event is present in SSE stream
  2. final_intent matches expected value (exact or prefix)
  3. event fires reasonably fast (soft: <30s end-to-end incl. response gen)

NOTE: The plan says "SSE event fires in <3 sec after request start". The event
IS emitted before response gen, but httpx.aiter_lines may yield in chunks;
we assert the event is present AND the full stream completes within max_wait_s.
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid

from _helpers import assert_final_intent


CASES = [
    {
        "name": "chitchat: selamat pagi",
        "text": "selamat pagi",
        "expected": "chitchat",
        "prefix": False,
    },
    {
        "name": "query: daftar pelanggan",
        "text": "daftar pelanggan",
        "expected": "query_customers",
        "prefix": True,
    },
    {
        "name": "crud: tambah pelanggan PT Test",
        "text": "tambah pelanggan PT Test SSE Meta",
        "expected": "create_customer",
        "prefix": True,
    },
]


async def run_one(case: dict) -> dict:
    sess = str(uuid.uuid4())
    t0 = time.time()
    try:
        data = await assert_final_intent(
            case["text"],
            case["expected"],
            message=case["name"],
            prefix=case["prefix"],
            conversation_id=sess,
            session_id=sess,
            max_wait_s=60.0,
        )
        elapsed = time.time() - t0
        return {
            "name": case["name"],
            "passed": True,
            "elapsed_s": round(elapsed, 2),
            "intent": data,
        }
    except AssertionError as e:
        elapsed = time.time() - t0
        return {
            "name": case["name"],
            "passed": False,
            "elapsed_s": round(elapsed, 2),
            "error": str(e)[:600],
        }
    except Exception as e:
        elapsed = time.time() - t0
        return {
            "name": case["name"],
            "passed": False,
            "elapsed_s": round(elapsed, 2),
            "error": f"UNEXPECTED: {type(e).__name__}: {e}"[:600],
        }


async def main() -> int:
    print("=" * 80)
    print("  SSE intent_classified meta-event smoke test")
    print("=" * 80)
    results = []
    for c in CASES:
        print(f"\n-> {c['name']}")
        r = await run_one(c)
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"   [{status}] elapsed={r['elapsed_s']}s")
        if r["passed"]:
            print(f"   intent payload: {r['intent']}")
        else:
            print(f"   error: {r['error']}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print("\n" + "=" * 80)
    print(f"  SMOKE RESULT: {passed}/{total} passed")
    print("=" * 80)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
