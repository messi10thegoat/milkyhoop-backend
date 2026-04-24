"""
Bucket A2 — after_resolve Intent Gate Regression Test
======================================================

Locks in the Bucket A2 deny-list contract on `StateUpdateHooks.after_resolve`:

1. Deny-listed intents (chitchat, MFG_*, reformat_as_table) do NOT write to
   the entity graph and DO emit a structured skip log.
2. Non-deny-listed intents (create_*, query_*_detail, etc.) proceed into the
   graph-update path.
3. Deny list is EXACT string match (not regex), so unknown new intent
   classes fail-open into graph writes (Risk Flag 2 mitigation).

Tests are unit-level: a `FakeSessionManager` captures `update_state` calls,
and `caplog` captures the skip log. No live DB required.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List


from app.services.unified_agent.session_manager import (
    AFTER_RESOLVE_DENY_LIST,
    StateUpdateHooks,
    StructuredState,
)


@dataclass
class _Resolved:
    entity_id: str
    entity_name: str


@dataclass
class _FakeSessionManager:
    """Minimal stub that records update_state calls."""

    graph: Dict[str, Any] = field(
        default_factory=lambda: {
            "nodes": {},
            "edges": [],
            "focus": None,
        }
    )
    update_calls: List[Dict[str, Any]] = field(default_factory=list)

    async def get_state(self, session_id: str) -> StructuredState:
        s = StructuredState()
        s.entity_graph = dict(self.graph)
        return s

    async def update_state(self, session_id: str, **kwargs):
        self.update_calls.append({"session_id": session_id, **kwargs})
        if "entity_graph" in kwargs:
            self.graph = kwargs["entity_graph"]


_SESSION = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _resolved_customer() -> Dict[str, Any]:
    return {"customer": _Resolved(entity_id="cust-1", entity_name="Maju Jaya")}


# ---------------------------------------------------------------------------
# 1. CHITCHAT blocks graph write
# ---------------------------------------------------------------------------
def test_chitchat_intent_blocks_graph_write(caplog):
    sm = _FakeSessionManager()
    caplog.set_level(logging.INFO, logger="unified_agent.session_manager")

    asyncio.run(
        StateUpdateHooks.after_resolve(sm, _SESSION, "chitchat", _resolved_customer())
    )

    assert (
        sm.update_calls == []
    ), f"CHITCHAT must not trigger update_state; got {sm.update_calls}"
    skip_logs = [r for r in caplog.records if "after_resolve_skipped" in r.getMessage()]
    assert skip_logs, "Expected skip log for chitchat intent"
    assert "intent=chitchat" in skip_logs[0].getMessage()
    assert "reason=deny_list" in skip_logs[0].getMessage()


# ---------------------------------------------------------------------------
# 2. MFG_* blocks graph write
# ---------------------------------------------------------------------------
def test_mfg_intent_blocks_graph_write(caplog):
    sm = _FakeSessionManager()
    caplog.set_level(logging.INFO, logger="unified_agent.session_manager")

    asyncio.run(
        StateUpdateHooks.after_resolve(
            sm, _SESSION, "query_bom_list", _resolved_customer()
        )
    )

    assert sm.update_calls == []
    skip_logs = [r for r in caplog.records if "after_resolve_skipped" in r.getMessage()]
    assert skip_logs
    assert "intent=query_bom_list" in skip_logs[0].getMessage()


# ---------------------------------------------------------------------------
# 3. ACTION (create_*) allows graph write
# ---------------------------------------------------------------------------
def test_action_intent_allows_graph_write(caplog):
    sm = _FakeSessionManager()
    caplog.set_level(logging.INFO, logger="unified_agent.session_manager")

    asyncio.run(
        StateUpdateHooks.after_resolve(
            sm, _SESSION, "create_sales_invoice", _resolved_customer()
        )
    )

    assert (
        len(sm.update_calls) == 1
    ), f"create_sales_invoice must trigger update_state; got {sm.update_calls}"
    assert "entity_graph" in sm.update_calls[0]
    skip_logs = [r for r in caplog.records if "after_resolve_skipped" in r.getMessage()]
    assert not skip_logs, "ACTION intent must not emit skip log"


# ---------------------------------------------------------------------------
# 4. QUERY_*_DETAIL (not in deny list) allows graph write
# ---------------------------------------------------------------------------
def test_query_intent_allows_graph_write(caplog):
    sm = _FakeSessionManager()
    caplog.set_level(logging.INFO, logger="unified_agent.session_manager")

    asyncio.run(
        StateUpdateHooks.after_resolve(
            sm, _SESSION, "query_customer_detail", _resolved_customer()
        )
    )

    assert len(sm.update_calls) == 1
    skip_logs = [r for r in caplog.records if "after_resolve_skipped" in r.getMessage()]
    assert not skip_logs


# ---------------------------------------------------------------------------
# 5. Unknown variant must fail-open (Risk Flag 2)
# ---------------------------------------------------------------------------
def test_deny_list_uses_exact_match_not_regex(caplog):
    """A future intent like CHITCHAT_NEW_VARIANT must NOT be silently skipped.
    The deny list is exact-match; unknown intents fail-open into graph writes."""
    sm = _FakeSessionManager()
    caplog.set_level(logging.INFO, logger="unified_agent.session_manager")

    # Something that regex-style "CHITCHAT*" would have matched:
    asyncio.run(
        StateUpdateHooks.after_resolve(
            sm, _SESSION, "chitchat_new_variant_not_in_list", _resolved_customer()
        )
    )

    assert (
        len(sm.update_calls) == 1
    ), "Unknown intent must fail-open into graph write (Risk Flag 2)"
    skip_logs = [r for r in caplog.records if "after_resolve_skipped" in r.getMessage()]
    assert not skip_logs


# ---------------------------------------------------------------------------
# 6. Skip log contains both session and intent fields
# ---------------------------------------------------------------------------
def test_skip_log_contains_session_and_intent(caplog):
    sm = _FakeSessionManager()
    caplog.set_level(logging.INFO, logger="unified_agent.session_manager")

    asyncio.run(
        StateUpdateHooks.after_resolve(
            sm, _SESSION, "reformat_as_table", _resolved_customer()
        )
    )

    skip_logs = [r for r in caplog.records if "after_resolve_skipped" in r.getMessage()]
    assert skip_logs, "Expected skip log"
    msg = skip_logs[0].getMessage()
    assert f"session={_SESSION}" in msg, f"Missing session field: {msg}"
    assert "intent=reformat_as_table" in msg, f"Missing intent field: {msg}"
    assert "reason=deny_list" in msg, f"Missing reason field: {msg}"


# ---------------------------------------------------------------------------
# 7. Deny-list constant sanity — all expected buckets present
# ---------------------------------------------------------------------------
def test_deny_list_contains_expected_buckets():
    # Chitchat
    assert "chitchat" in AFTER_RESOLVE_DENY_LIST
    # MFG subset
    for mfg in ("query_bom_list", "query_work_order_list", "query_fg_receipts"):
        assert mfg in AFTER_RESOLVE_DENY_LIST, f"Missing MFG intent: {mfg}"
    # Reformat
    assert "reformat_as_table" in AFTER_RESOLVE_DENY_LIST
    # Action intents NOT in deny list
    for allow in ("create_sales_invoice", "query_customer_detail", "update_bill"):
        assert (
            allow not in AFTER_RESOLVE_DENY_LIST
        ), f"Action intent leaked into deny list: {allow}"
