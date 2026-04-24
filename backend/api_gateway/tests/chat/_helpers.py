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


# ──────────────────────────────────────────────────────────────────────────
# P4 Clarification-Slot helpers (ADR P4 v1.3)
# ──────────────────────────────────────────────────────────────────────────


async def seed_pending_clarification(
    db_pool,
    session_id: str,
    slot_type: str = "period",
    parent_intent: str = "calc_sum_ar",
    parent_entities: dict = None,
    reask_count: int = 0,
    expires_in_minutes: int = 5,
) -> None:
    """Seed chat_session_state.pending_clarification directly (test bypass).

    Upserts a row keyed by session_id; chat_session_state uniques session_id.
    """
    import json as _json
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    payload = {
        "slot_type": slot_type,
        "parent_intent": parent_intent,
        "parent_entities": parent_entities or {},
        "asked_at": now.isoformat(),
        "reask_count": reask_count,
    }
    expires_at = now + timedelta(minutes=expires_in_minutes)

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_session_state (session_id, tenant_id,
                pending_clarification, pending_clarification_expires_at)
            VALUES ($3::uuid, 'grapgrap', $1::jsonb, $2)
            ON CONFLICT (session_id) DO UPDATE SET
                pending_clarification = EXCLUDED.pending_clarification,
                pending_clarification_expires_at =
                    EXCLUDED.pending_clarification_expires_at
            """,
            _json.dumps(payload),
            expires_at,
            session_id,
        )


async def get_pending_clarification(db_pool, session_id: str):
    """Return the pending_clarification payload (dict) or None."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT pending_clarification, pending_clarification_expires_at
            FROM chat_session_state
            WHERE session_id = $1::uuid
            """,
            session_id,
        )
    if not row or not row["pending_clarification"]:
        return None
    import json as _json

    data = row["pending_clarification"]
    return data if isinstance(data, dict) else _json.loads(data)


async def get_clarification_event(db_pool, session_id: str, retries: int = 4):
    """Return the most recent clarification_event for a session.

    NOTE: intent_decision_log has no request_id col on this DB; we key on
    session_id + order by ts DESC. Retries while gateway flushes telemetry.
    """
    import asyncio as _asyncio

    for _ in range(retries):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT clarification_event
                FROM intent_decision_log
                WHERE session_id = $1::uuid
                ORDER BY ts DESC
                LIMIT 1
                """,
                session_id,
            )
        if row and row["clarification_event"] is not None:
            return row["clarification_event"]
        await _asyncio.sleep(0.7)
    return None


async def make_db_pool():
    """Create asyncpg pool to the dev Postgres.

    Works in both environments:
    - Host shell: TEST_DB_HOST/PORT unset → uses 127.0.0.1:5433 (host-mapped)
    - Inside api_gateway container: set TEST_DB_HOST=postgres TEST_DB_PORT=5432
      (or rely on auto-detect: if 127.0.0.1:5433 refused, fallback to postgres:5432)

    Creds match /root/milkyhoop-dev/.env (DATABASE_URL superuser).
    """
    import asyncpg
    import os

    host = os.environ.get("TEST_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("TEST_DB_PORT", "5433"))
    # Auto-detect: if running inside api_gateway container, postgres hostname resolves
    if host == "127.0.0.1":
        try:
            import socket

            socket.gethostbyname("postgres")
            # resolvable → we're in docker network
            host = "postgres"
            port = 5432
        except Exception:
            pass
    return await asyncpg.create_pool(
        host=host,
        port=port,
        user="postgres",
        password="Proyek771977",  # pragma: allowlist secret (dev DB, not prod)
        database="milkydb",
        min_size=1,
        max_size=3,
    )
