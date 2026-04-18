"""
Integration tests for Tier 1 Derived Profile.
Tests against live dev database (grapgrap tenant).
"""
import pytest


@pytest.mark.asyncio
async def test_tier1_top_entities():
    """Tier 1 should return top entities for active tenant."""
    from app.services.unified_agent.tier1_profile import get_tier1_context
    from app.services.unified_agent.db_utils import get_session_db_pool

    pool = await get_session_db_pool()
    context = await get_tier1_context(pool, "grapgrap", ttl_override=0)

    assert isinstance(context, str)
    # Either empty (new tenant) or starts with PROFIL BISNIS
    if context:
        assert "PROFIL BISNIS" in context
    print(f"[TEST] Tier 1 context ({len(context)} chars): {context[:300]}")


@pytest.mark.asyncio
async def test_tier1_no_amounts_in_profile():
    """Verify Iron Law 3.1: no amounts leak into Tier 1 context."""
    from app.services.unified_agent.tier1_profile import get_tier1_context
    from app.services.unified_agent.db_utils import get_session_db_pool

    pool = await get_session_db_pool()
    context = await get_tier1_context(pool, "grapgrap", ttl_override=0)

    assert "Rp " not in context, f"Amount found in Tier 1 context: {context[:500]}"
    assert "IDR" not in context, f"Amount found in Tier 1 context: {context[:500]}"


@pytest.mark.asyncio
async def test_tier1_graceful_on_empty_tenant():
    """Tier 1 should return empty string for nonexistent tenant."""
    from app.services.unified_agent.tier1_profile import get_tier1_context
    from app.services.unified_agent.db_utils import get_session_db_pool

    pool = await get_session_db_pool()
    context = await get_tier1_context(pool, "nonexistent_tenant_xyz", ttl_override=0)

    assert context == ""
