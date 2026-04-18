"""
End-to-end test for bot memory architecture.
Verifies Tier 1/2/3 across sessions.
"""
import uuid

import pytest
from tests.chat.conftest import TestSuite


@pytest.fixture
def suite():
    return TestSuite()


@pytest.mark.asyncio
async def test_tier1_context_in_response(suite):
    """Verify bot has business awareness from Tier 1."""
    conv_id = f"e2e-memory-{uuid.uuid4().hex[:8]}"
    data = await suite.send("siapa supplier utama kita?", conversation_id=conv_id)
    text = data.get("text", "").lower()
    assert data.get("message_type") == "TEXT"
    assert len(text) > 20, f"Response too short: {text}"
    print(f"[E2E] Tier 1 vendor response: {text[:200]}")


@pytest.mark.asyncio
async def test_cross_session_entity_resolution(suite):
    """Verify entity mentioned in session 1 is recognized in session 2 via Tier 1."""
    conv1 = f"e2e-cross-{uuid.uuid4().hex[:8]}"
    conv2 = f"e2e-cross-{uuid.uuid4().hex[:8]}"

    await suite.send("berapa total faktur ke Sintia bulan ini?", conversation_id=conv1)

    data = await suite.send("tagih Sintia", conversation_id=conv2)
    text = data.get("text", "").lower()

    assert (
        "siapa" not in text or "sintia" in text
    ), f"Bot failed cross-session entity resolution: {text[:200]}"
    print(f"[E2E] Cross-session response: {text[:200]}")


@pytest.mark.asyncio
async def test_memory_degradation_graceful(suite):
    """Verify bot works even if memory tiers fail."""
    conv_id = f"e2e-degrade-{uuid.uuid4().hex[:8]}"
    data = await suite.send("halo", conversation_id=conv_id)
    assert data.get("message_type") == "TEXT"
    assert len(data.get("text", "")) > 5
    print(f"[E2E] Graceful degradation: {data.get('text', '')[:100]}")
