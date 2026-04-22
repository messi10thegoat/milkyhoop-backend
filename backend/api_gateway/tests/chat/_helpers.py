"""
Chat test helpers — SSE intent_classified meta-event parsing + assertion.

Used by P1/P3 intent-level tests (batch 1 chat-regex-slot-fix-plan).
The backend emits:

    data: {"event": "intent_classified",
           "data": {"request_id": "...", "final_intent": "...",
                    "decision_source": "...", "confidence": 0.x}}

BEFORE the main response stream (chitchat short-circuit or full classify).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import List, Optional

import httpx

# Reuse credentials + base URL from existing conftest (no duplication,
# avoids secret-scan false positives on literal creds in this file).
from conftest import BASE_URL, CREDENTIALS, LOGIN_URL  # noqa: E402

STREAM_URL = f"{BASE_URL}/api/v3/chat/message/stream"


async def _login() -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(LOGIN_URL, json=CREDENTIALS)
        r.raise_for_status()
        return r.json()["data"]["access_token"]


def _parse_sse_line(raw: str) -> Optional[dict]:
    """Parse a single 'data: {...}' SSE line. Returns None if not an event."""
    raw = raw.strip()
    if not raw.startswith("data:"):
        return None
    payload = raw[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


async def stream_chat(
    text: str,
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout_s: float = 60.0,
) -> List[dict]:
    """Send /message/stream request, return all SSE events as a list of dicts.

    Each entry is of shape {"event": "...", "data": {...}} OR raw parsed payload.
    """
    token = await _login()
    conv_id = conversation_id or str(uuid.uuid4())
    sess_id = session_id or conv_id
    events: List[dict] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }
    body = {"text": text, "conversation_id": conv_id, "session_id": sess_id}
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        async with client.stream(
            "POST", STREAM_URL, json=body, headers=headers
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                parsed = _parse_sse_line(line)
                if parsed is not None:
                    events.append(parsed)
                    # Stop iteration early once DONE/ERROR seen
                    ev_type = parsed.get("event") if isinstance(parsed, dict) else None
                    if ev_type in ("DONE", "ERROR"):
                        break
    return events


def get_intent_from_sse(events: List[dict]) -> Optional[dict]:
    """Scan event list and return the intent_classified payload dict, or None.

    Returns: {"request_id", "final_intent", "decision_source", "confidence"} or None.
    """
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("event") == "intent_classified":
            data = ev.get("data") or {}
            if isinstance(data, dict):
                return data
    return None


def _format_tail(events: List[dict], n: int = 8) -> str:
    tail = events[-n:]
    lines = []
    for ev in tail:
        try:
            lines.append(json.dumps(ev, ensure_ascii=False)[:300])
        except Exception:
            lines.append(repr(ev)[:300])
    return "\n  ".join(lines)


async def assert_final_intent(
    text: str,
    expected_intent: str,
    *,
    message: str = "",
    prefix: bool = False,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    max_wait_s: float = 30.0,
) -> dict:
    """Send message via SSE stream, assert intent_classified event fires
    with final_intent matching expected_intent.

    Args:
        text: User message to send
        expected_intent: Expected final_intent value (exact or prefix)
        prefix: If True, match via startswith; else exact match
        message: Optional test-case label shown on failure

    Returns: the intent_classified data dict.
    Raises AssertionError with last-N events context on mismatch.
    """
    t0 = time.time()
    events = await stream_chat(
        text,
        conversation_id=conversation_id,
        session_id=session_id,
        timeout_s=max_wait_s,
    )
    elapsed = time.time() - t0

    intent_data = get_intent_from_sse(events)
    label = f"[{message}] " if message else ""

    if intent_data is None:
        raise AssertionError(
            f"{label}No 'intent_classified' event received for text={text!r} "
            f"(elapsed={elapsed:.2f}s). Last events:\n  {_format_tail(events)}"
        )

    actual = str(intent_data.get("final_intent") or "")
    ok = actual.startswith(expected_intent) if prefix else actual == expected_intent
    if not ok:
        how = "startswith" if prefix else "=="
        raise AssertionError(
            f"{label}Intent mismatch for text={text!r}: "
            f"expected {how} {expected_intent!r}, got {actual!r}. "
            f"Payload={intent_data}. "
            f"Last events:\n  {_format_tail(events)}"
        )
    return intent_data
