"""Bucket 3 Step 1 — ActionService.edit_pending_action unit tests.

Per DOCS/plans/2026-04-29-mid-flow-edit-diagnosis.md (v2). Pure data layer:
no dispatch routing, no behavioral fixtures here.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.action_service import (
    ActionService,
    detect_edit_intent,
)


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def make_service() -> ActionService:
    """Build an ActionService with a mocked asyncpg pool (unused in these tests)."""
    pool = MagicMock()
    return ActionService(pool=pool)


def make_pending(
    *,
    status: str = "PENDING",
    payload: dict | None = None,
    version: int = 1,
) -> dict:
    """Construct a Redis-envelope-shaped pending dict for mocking get_pending_action."""
    return {
        "id": "pend-1",
        "tenant_id": "tenant-1",
        "status": status,
        "version": version,
        "action_type": "create_sales_invoice",
        "payload": payload
        if payload is not None
        else {
            "customer_id": "cust-1",
            "customer_name": "Maju Jaya",
            "invoice_date": "2026-04-29",
            "due_date": "2026-05-29",
            "tax_rate": 0,
            "items": [{"description": "kaos", "quantity": 10, "unit_price": 50000}],
        },
    }


class _FakeRedis:
    """Minimal async stub for redis.asyncio interface used by edit_pending_action."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value


# ────────────────────────────────────────────────────────────────────
# T7 — detect_edit_intent precedence (sync, runs first; no async deps)
# ────────────────────────────────────────────────────────────────────


def test_t7_detect_edit_intent_precedence():
    # pure edit
    assert detect_edit_intent("ganti qty jadi 20") is True
    assert detect_edit_intent("ubah jadi mandiri") is True
    assert detect_edit_intent("koreksi harga") is True
    assert detect_edit_intent("tambah kaos") is True
    # X-jadi-Y pattern (no edit keyword, but matches regex)
    assert detect_edit_intent("qty 20 aja jadi 25") is True
    # confirm precedence (overlap)
    assert detect_edit_intent("betul ganti") is False
    assert detect_edit_intent("ya ganti aja") is False
    # cancel precedence (overlap)
    assert detect_edit_intent("batal ganti") is False
    assert detect_edit_intent("jangan ubah") is False
    # pure confirm / cancel
    assert detect_edit_intent("betul") is False
    assert detect_edit_intent("batal") is False
    # empty / whitespace
    assert detect_edit_intent("") is False
    assert detect_edit_intent("   ") is False
    # unrelated
    assert detect_edit_intent("apa kabar") is False


# ────────────────────────────────────────────────────────────────────
# Async test fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture
def service(fake_redis):
    svc = make_service()
    svc._get_redis = AsyncMock(return_value=fake_redis)
    return svc


# ────────────────────────────────────────────────────────────────────
# T1 — Happy path qty edit (regex)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t1_qty_edit_regex(service, fake_redis):
    pending = make_pending(version=1)
    service.get_pending_action = AsyncMock(return_value=pending)

    result = await service.edit_pending_action(
        tenant_id="tenant-1",
        pending_id="pend-1",
        text="ganti qty jadi 20",
        action_key="create_sales_invoice",
    )

    assert result["success"] is True, result
    assert result["version"] == 2
    assert result["payload"]["items"][0]["quantity"] == 20
    # original unit_price preserved
    assert result["payload"]["items"][0]["unit_price"] == 50000
    # Redis setex called once
    assert len(fake_redis.setex_calls) == 1
    key, ttl, blob = fake_redis.setex_calls[0]
    assert key == "action:tenant-1:pend-1"
    assert ttl > 0
    written = json.loads(blob)
    assert written["payload"]["items"][0]["quantity"] == 20
    assert written["version"] == 2


# ────────────────────────────────────────────────────────────────────
# T2 — Unknown field via LLM extractor → UNKNOWN_FIELD
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t2_unknown_field(service):
    pending = make_pending()
    service.get_pending_action = AsyncMock(return_value=pending)
    # Force LLM path to return an unknown-field patch
    service._extract_patch_llm = AsyncMock(return_value={"warna": "merah"})

    result = await service.edit_pending_action(
        tenant_id="tenant-1",
        pending_id="pend-1",
        text="ubah warna jadi merah",
        action_key="create_sales_invoice",
    )

    assert result["success"] is False
    assert result["error"] == "UNKNOWN_FIELD"
    assert "warna" in result["message"]


# ────────────────────────────────────────────────────────────────────
# T3 — Expired pending → EXPIRED
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t3_expired_pending(service):
    # get_pending_action returns None when Redis key is gone (TTL expired)
    service.get_pending_action = AsyncMock(return_value=None)

    result = await service.edit_pending_action(
        tenant_id="tenant-1",
        pending_id="pend-gone",
        text="ganti qty jadi 20",
        action_key="create_sales_invoice",
    )

    assert result["success"] is False
    assert result["error"] == "EXPIRED"


# ────────────────────────────────────────────────────────────────────
# T4 — Customer swap re-runs validation
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t4_customer_swap_revalidates(service):
    pending = make_pending()
    service.get_pending_action = AsyncMock(return_value=pending)
    # LLM returns a customer_name-only patch
    service._extract_patch_llm = AsyncMock(return_value={"customer_name": "Toko ABC"})
    # Spy on _revalidate_action_plan
    revalidate_spy = AsyncMock(return_value=(True, []))
    service._revalidate_action_plan = revalidate_spy

    result = await service.edit_pending_action(
        tenant_id="tenant-1",
        pending_id="pend-1",
        text="ganti customer jadi Toko ABC",
        action_key="create_sales_invoice",
    )

    assert result["success"] is True
    revalidate_spy.assert_awaited_once()
    # The new payload passed in must contain the swapped customer_name
    args, _ = revalidate_spy.call_args
    _, payload_arg, action_key_arg = args
    assert payload_arg["customer_name"] == "Toko ABC"
    assert action_key_arg == "create_sales_invoice"


# ────────────────────────────────────────────────────────────────────
# T5 — Multi-field atomic edit
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t5_multi_field_atomic(service):
    pending = make_pending()
    service.get_pending_action = AsyncMock(return_value=pending)

    result = await service.edit_pending_action(
        tenant_id="tenant-1",
        pending_id="pend-1",
        text="ganti qty jadi 20, harga jadi 60 ribu",
        action_key="create_sales_invoice",
    )

    assert result["success"] is True, result
    item0 = result["payload"]["items"][0]
    assert item0["quantity"] == 20
    assert item0["unit_price"] == 60000


# ────────────────────────────────────────────────────────────────────
# T6 — Ambiguous edit via LLM → AMBIGUOUS_FIELD
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t6_ambiguous_llm(service):
    pending = make_pending()
    service.get_pending_action = AsyncMock(return_value=pending)
    # Use a phrasing the regex won't match (no "jadi N" + no edit keyword
    # that survives precedence) so we drop into the LLM path.
    service._extract_patch_llm = AsyncMock(
        return_value={"_error": "ambiguous", "reason": "interrogative, not an edit"}
    )

    result = await service.edit_pending_action(
        tenant_id="tenant-1",
        pending_id="pend-1",
        text="kalau qty hasilnya berapa ya?",
        action_key="create_sales_invoice",
    )

    assert result["success"] is False
    assert result["error"] == "AMBIGUOUS_FIELD"
    assert "interrogative" in result["message"]
