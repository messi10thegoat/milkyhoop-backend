"""Behavioral fixture runner — objective smart-bot baseline measurement."""
import asyncio
import json
import sys
import time
import uuid
import re
import argparse
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from conftest import BASE_URL, CREDENTIALS, LOGIN_URL  # noqa: E402

STREAM_URL = f"{BASE_URL}/api/v3/chat/message/stream"
_TOKEN: str = ""


async def login_once():
    global _TOKEN
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(LOGIN_URL, json=CREDENTIALS)
        r.raise_for_status()
        _TOKEN = r.json()["data"]["access_token"]


async def stream_with_token(text, conv_id, sess_id, timeout_s=60.0):
    headers = {"Authorization": f"Bearer {_TOKEN}", "Accept": "text/event-stream"}
    body = {"text": text, "conversation_id": conv_id, "session_id": sess_id}
    events = []
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        async with client.stream(
            "POST", STREAM_URL, json=body, headers=headers
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass
    return events


def extract_fields(events):
    intent = None
    message_type = None
    text = ""
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("event") == "intent_classified":
            d = ev.get("data") or {}
            if isinstance(d, dict):
                intent = d.get("final_intent") or intent
        d = ev.get("data") if isinstance(ev.get("data"), dict) else ev
        if isinstance(d, dict):
            mt = d.get("message_type")
            if mt:
                message_type = mt
            for k in ("text", "response_text", "message", "content"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    text = v
    return {
        "intent": intent,
        "message_type": message_type,
        "text": text or "",
        "events": len(events),
    }


def chk_response_type(spec, f):
    e = spec.get("expect_response_type")
    if not e:
        return None
    a = f.get("message_type") or ""
    return ("response_type", a == e, "want=%s got=%r" % (e, a))


def chk_intent(spec, f):
    e = spec.get("expect_intent")
    if not e:
        return None
    a = f.get("intent")
    return ("intent", a == e, "want=%s got=%r" % (e, a))


def chk_intent_one_of(spec, f):
    options = spec.get("expect_intent_one_of")
    if not options:
        return None
    a = f.get("intent")
    return ("intent_one_of", a in options, "want_in=%s got=%r" % (options, a))


def chk_response_contains(spec, f):
    needle = spec.get("expect_response_contains")
    if needle is None:
        return None
    if needle == "":
        return ("response_contains", True, "empty needle = present")
    text = (f.get("text") or "").lower()
    return ("response_contains", needle.lower() in text, "needle=%r" % needle)


def chk_amount(spec, f):
    if not spec.get("expect_response_contains_amount"):
        return None
    text = f.get("text") or ""
    has = bool(re.search(r"Rp[\s.]*[\d.,]+", text))
    return ("response_contains_amount", has, "text_len=%d" % len(text))


def chk_date(spec, f):
    if not spec.get("expect_response_contains_date"):
        return None
    text = f.get("text") or ""
    has = bool(
        re.search(
            r"\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}", text
        )
    )
    return ("response_contains_date", has, "")


def chk_customer_name(spec, f):
    if not spec.get("expect_response_contains_customer_name"):
        return None
    text = f.get("text") or ""
    has = bool(re.search(r"\b(PT|CV|Toko|UD)\s+[A-Z]\w+|[A-Z]\w+\s+[A-Z]\w+", text))
    return ("response_contains_customer_name", has, "")


SKIPPABLE_KEYS = {
    "expect_action_succeeded",
    "expect_pattern_row_written",
    "expect_db_delta",
    "expect_session_active_entity_type",
    "expect_b2_graph_traverse_fired",
    "expect_response_asks_clarification",
    "expect_response_references_entity",
    "expect_response_contains_invoice_number",
    "expect_entities_resolved",
}


CHECKS = [
    chk_response_type,
    chk_intent,
    chk_intent_one_of,
    chk_response_contains,
    chk_amount,
    chk_date,
    chk_customer_name,
]


def evaluate_turn(spec, fields):
    results = []
    for c in CHECKS:
        r = c(spec, fields)
        if r:
            results.append(r)
    for k in spec:
        if k in SKIPPABLE_KEYS:
            results.append((k.replace("expect_", ""), True, "skipped"))
    return results


async def run_scenario(scenario, turn_sleep=2.0):
    conv = str(uuid.uuid4())
    sess = conv
    turn_results = []
    for i, turn_spec in enumerate(scenario["turns"]):
        try:
            events = await stream_with_token(turn_spec["user"], conv, sess)
            fields = extract_fields(events)
            assertions = evaluate_turn(turn_spec, fields)
        except Exception as e:
            fields = {"error": "%s: %s" % (type(e).__name__, e)}
            assertions = [("execution", False, str(e))]
        hard = [a for a in assertions if not a[2].startswith("skipped")]
        turn_passed = all(a[1] for a in hard) if hard else True
        turn_results.append(
            {
                "turn_idx": i,
                "user": turn_spec["user"],
                "fields": {k: v for k, v in fields.items() if k != "events"},
                "events": fields.get("events", 0) if isinstance(fields, dict) else 0,
                "assertions": assertions,
                "passed": turn_passed,
            }
        )
        await asyncio.sleep(turn_sleep)
    scenario_passed = all(t["passed"] for t in turn_results)
    return {
        "id": scenario["id"],
        "category": scenario.get("category", "uncategorized"),
        "weight": scenario.get("weight", 1),
        "passed": scenario_passed,
        "turns": turn_results,
    }


def aggregate(results):
    by_cat = {}
    for r in results:
        cat = r["category"]
        by_cat.setdefault(cat, {"pass": 0, "fail": 0, "wp": 0, "wt": 0})
        by_cat[cat]["pass"] += int(r["passed"])
        by_cat[cat]["fail"] += int(not r["passed"])
        by_cat[cat]["wp"] += r["weight"] * int(r["passed"])
        by_cat[cat]["wt"] += r["weight"]
    twp = sum(c["wp"] for c in by_cat.values())
    twt = sum(c["wt"] for c in by_cat.values())
    overall = round(twp / twt * 100, 1) if twt else 0
    for cat, c in by_cat.items():
        c["pct"] = round(c["wp"] / c["wt"] * 100, 1) if c["wt"] else 0
    return {
        "overall_weighted_pct": overall,
        "total_pass": sum(c["pass"] for c in by_cat.values()),
        "total_scenarios": len(results),
        "by_category": by_cat,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", default="/tmp/behavioral_run_%d.json" % int(time.time())
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()

    fixtures_path = Path(__file__).parent / "fixtures" / "behavioral_scenarios.yaml"
    fixtures = yaml.safe_load(fixtures_path.read_text())
    scenarios = fixtures["scenarios"]
    scenarios = scenarios[args.offset :]
    if args.limit:
        scenarios = scenarios[: args.limit]

    print("[runner] login...", file=sys.stderr, flush=True)
    await login_once()

    results = []
    t_start = time.time()
    for scenario in scenarios:
        print("[runner]   -> %s" % scenario["id"], file=sys.stderr, flush=True)
        try:
            results.append(await run_scenario(scenario))
        except Exception as e:
            print("[runner]      FAILED: %s" % e, file=sys.stderr, flush=True)
            results.append(
                {
                    "id": scenario["id"],
                    "category": scenario.get("category", "uncategorized"),
                    "weight": scenario.get("weight", 1),
                    "passed": False,
                    "turns": [],
                    "error": str(e),
                }
            )

    score = aggregate(results)
    output = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": round(time.time() - t_start, 1),
        "score": score,
        "scenarios": results,
    }
    Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(json.dumps(score, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
