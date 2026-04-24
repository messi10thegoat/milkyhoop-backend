"""
Batch 1.5a — REC telemetry tag test.

Ensures REC_FOLLOWUP path tags decision_source="rec_followup" so audit
queries can measure REC fast-path hit rate without parsing
conversation_history. Prevents silent regression if guard order is
refactored later.

Refs: docs/plans/2026-04-22-chat-regex-slot-fix-plan.md (1.5a spinoff)
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
import httpx

from conftest import BASE_URL, CREDENTIALS, LOGIN_URL

STREAM_URL = f"{BASE_URL}/api/v3/chat/message/stream"

# Share a single token across the suite to avoid /auth/login rate limit (429).
_TOKEN: str | None = None


async def _get_token() -> str:
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(LOGIN_URL, json=CREDENTIALS)
        r.raise_for_status()
        _TOKEN = r.json()["data"]["access_token"]
    return _TOKEN


async def _stream(text: str, conv: str) -> list[dict]:
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    body = {"text": text, "conversation_id": conv, "session_id": conv}
    events: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0) as c:
        async with c.stream("POST", STREAM_URL, json=body, headers=headers) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    ev = json.loads(payload)
                except Exception:
                    continue
                events.append(ev)
                if ev.get("event") in ("DONE", "ERROR"):
                    break
    return events


def _find_intent(events: list[dict]) -> dict | None:
    for ev in events:
        if ev.get("event") == "intent_classified":
            return ev.get("data") or {}
    return None


async def _case_rec_followup() -> tuple[bool, str]:
    conv = str(uuid.uuid4())
    await _stream("daftar piutang", conv)
    events = await _stream("siapa?", conv)
    intent = _find_intent(events)
    if not intent:
        return False, "no intent_classified event on turn 2"
    ds = str(intent.get("decision_source") or "")
    fi = str(intent.get("final_intent") or "")
    if ds != "rec_followup":
        return False, f"decision_source={ds!r} (want rec_followup), final_intent={fi!r}"
    return True, f"OK ds=rec_followup final_intent={fi}"


async def _case_non_rec() -> tuple[bool, str]:
    conv = str(uuid.uuid4())
    events = await _stream("daftar pelanggan", conv)
    intent = _find_intent(events)
    if not intent:
        return False, "no intent_classified event"
    ds = str(intent.get("decision_source") or "")
    if ds == "rec_followup":
        return False, "false-positive: standalone query tagged rec_followup"
    return True, f"OK ds={ds}"


async def main() -> int:
    cases = [
        ("rec_followup_tag_positive", _case_rec_followup),
        ("rec_followup_tag_negative", _case_non_rec),
    ]
    passed = 0
    failed = 0
    print("── REC Telemetry Tag Tests ──")
    for name, fn in cases:
        try:
            ok, msg = await fn()
        except Exception as e:
            ok, msg = False, f"exception: {e!r}"
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {msg}")
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n  Total: {passed + failed} | Passed: {passed} | Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
