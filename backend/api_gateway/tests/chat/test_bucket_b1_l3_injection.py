"""Bucket B1 regression tests — L3 recent events injection.

Covers:
1. ACTION intent injects L3 context block.
2. QUERY intent skips.
3. CHITCHAT skips.
4. MFG_* skips.
5. Empty events → no inject, no error.
6. Injection failure is non-fatal (Bucket 0 regression guard).
7. Event formatter is compact and bounded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
import logging

import pytest

from app.services.unified_agent.l3_prompt import (
    L3_INJECT_DENY_LIST,
    build_l3_context_block,
    format_l3_event_for_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso(minutes_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def _make_event(
    event_type: str,
    action_type: str = "",
    result_summary: str = "",
    minutes_ago: int = 1,
) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "action_type": action_type,
        "result_summary": result_summary,
        "timestamp": _now_iso(minutes_ago),
    }


class _FakeSessionManager:
    """Minimal duck-typed stand-in for session_manager.get_recent_events."""

    def __init__(self, events: List[Dict[str, Any]]):
        self._events = events
        self.calls: List[tuple] = []

    async def get_recent_events(self, session_id: str, limit: int = 10):
        self.calls.append((session_id, limit))
        return self._events[:limit]


class _RaisingSessionManager:
    async def get_recent_events(self, session_id: str, limit: int = 10):
        raise RuntimeError("simulated db failure")


# ---------------------------------------------------------------------------
# Simulate the orchestrator's injection snippet (exact logic copy).
# We test the logic inline so we don't have to spin up the full agent loop.
# ---------------------------------------------------------------------------


async def _simulate_injection(intent: str, session_manager, session_id: str):
    """Returns (messages_appended, log_records)."""
    logger = logging.getLogger("bucket_b1_sim")
    logger.setLevel(logging.DEBUG)
    records: List[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _H()
    logger.addHandler(h)
    try:
        appended: List[Dict[str, str]] = []
        intent_lower = (intent or "").lower()
        try:
            if intent_lower in L3_INJECT_DENY_LIST:
                logger.debug(
                    "l3_skipped session=%s intent=%s reason=deny_list",
                    session_id,
                    intent_lower,
                )
            elif session_manager and session_id:
                events = await session_manager.get_recent_events(session_id, limit=5)
                block = build_l3_context_block(events, max_age_seconds=1800)
                if block:
                    appended.append(
                        {
                            "role": "system",
                            "content": f"## Recent session context\n{block}",
                        }
                    )
                    logger.info(
                        "l3_injected session=%s intent=%s event_count=%d",
                        session_id,
                        intent_lower,
                        block.count("\n") + 1,
                    )
                else:
                    logger.debug(
                        "l3_skipped session=%s intent=%s reason=no_events",
                        session_id,
                        intent_lower,
                    )
        except (KeyError, TypeError, ValueError, AttributeError, ImportError) as e:
            logger.error("l3_injection_failed err=%s", e, exc_info=True)
        except Exception as e:
            logger.error("l3_injection_failed_unexpected err=%s", e, exc_info=True)
        return appended, records
    finally:
        logger.removeHandler(h)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_intent_injects_l3_context():
    events = [
        _make_event(
            "confirm",
            "create_sales_invoice",
            "Invoice INV-1 | Rp 500,000",
            minutes_ago=3,
        ),
        _make_event("propose", "create_sales_invoice", "Maju Jaya", minutes_ago=5),
    ]
    sm = _FakeSessionManager(events)
    appended, records = await _simulate_injection("create_sales_invoice", sm, "sid-1")
    assert len(appended) == 1
    assert "Recent session context" in appended[0]["content"]
    assert "confirmed create_sales_invoice" in appended[0]["content"]
    assert sm.calls == [("sid-1", 5)]
    assert any("l3_injected" in r.getMessage() for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        "query_bills_list",
        "query_customer_ar",
        "query_bank_account_balance",
        "query_items_list",
    ],
)
async def test_query_intent_skips_l3_injection(intent):
    sm = _FakeSessionManager([_make_event("confirm", "create_bill", "x", 2)])
    appended, records = await _simulate_injection(intent, sm, "sid-q")
    assert appended == []
    assert sm.calls == []  # short-circuited before fetch
    assert any(
        "l3_skipped" in r.getMessage() and "deny_list" in r.getMessage()
        for r in records
    )


@pytest.mark.asyncio
async def test_chitchat_intent_skips_l3_injection():
    sm = _FakeSessionManager([_make_event("confirm", "create_bill", "x", 2)])
    appended, records = await _simulate_injection("chitchat", sm, "sid-c")
    assert appended == []
    assert sm.calls == []
    assert any("deny_list" in r.getMessage() for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        "query_bom_list",
        "query_work_order_detail",
        "query_production_active",
        "query_material_issues",
    ],
)
async def test_mfg_intent_skips_l3_injection(intent):
    sm = _FakeSessionManager([_make_event("confirm", "create_bill", "x", 2)])
    appended, _ = await _simulate_injection(intent, sm, "sid-m")
    assert appended == []
    assert sm.calls == []


@pytest.mark.asyncio
async def test_empty_events_does_not_inject():
    sm = _FakeSessionManager([])
    appended, records = await _simulate_injection("create_bill", sm, "sid-e")
    assert appended == []
    # should record no_events skip, not deny_list
    assert any("no_events" in r.getMessage() for r in records)


@pytest.mark.asyncio
async def test_injection_failure_is_non_fatal():
    sm = _RaisingSessionManager()
    appended, records = await _simulate_injection("create_sales_invoice", sm, "sid-x")
    assert appended == []
    # must have logged an error with exc_info (Bucket 0 regression guard)
    err_records = [r for r in records if r.levelno >= logging.ERROR]
    assert err_records, "expected ERROR log on injection failure"
    assert err_records[0].exc_info is not None


def test_event_format_compact():
    events = [
        _make_event("confirm", "create_sales_invoice", "Invoice INV-1 | Rp 500,000", 3),
        _make_event("propose", "create_bill", "PT Knitto", 8),
        _make_event("reject", "create_sales_invoice", "", 12),
    ]
    for e in events:
        line = format_l3_event_for_prompt(e)
        assert line, f"formatter returned empty for {e}"
        assert len(line) <= 120, f"line too long: {line!r}"
        # relative time marker present
        assert ("ago" in line) or ("just now" in line)


def test_event_format_skips_noise():
    # search / tool events are noise — formatter returns empty.
    assert format_l3_event_for_prompt(_make_event("search", "", "", 1)) == ""
    assert format_l3_event_for_prompt(_make_event("tool", "", "", 1)) == ""


def test_build_block_drops_stale():
    events = [
        _make_event("confirm", "create_bill", "ok", 2),  # fresh
        _make_event("confirm", "create_bill", "ok_old", 45),  # >30min → drop
    ]
    block = build_l3_context_block(events, max_age_seconds=1800)
    assert "ok" in block
    assert "ok_old" not in block


def test_deny_list_contains_a2_superset():
    # A2 items must all be present in B1 deny list.
    from app.services.unified_agent.hook_gates import AFTER_RESOLVE_DENY_LIST

    assert AFTER_RESOLVE_DENY_LIST <= L3_INJECT_DENY_LIST
    # Plus common query_* picks.
    assert "query_bills_list" in L3_INJECT_DENY_LIST
    assert "query_customer_ar" in L3_INJECT_DENY_LIST
