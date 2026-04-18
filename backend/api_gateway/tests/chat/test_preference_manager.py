"""
Integration tests for Tier 2 Explicit Preferences.
Runs inside api_gateway Docker container.
"""
import os
import asyncpg
import pytest


async def _make_pool():
    """Create a fresh asyncpg pool from environment variables."""
    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "postgres"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "milkydb"),
        min_size=1,
        max_size=2,
    )


@pytest.mark.asyncio
async def test_set_and_get_preference():
    from app.services.unified_agent.preference_manager import PreferenceManager

    pool = await _make_pool()
    try:
        mgr = PreferenceManager(pool, "grapgrap", "test-user-prefs-001")

        result = await mgr.set_preference("display_name", "Bu Grace", "explicit_chat")
        assert result["status"] == "ok"

        prefs = await mgr.get_all_preferences()
        assert any(
            p["key"] == "display_name" and p["value"] == "Bu Grace" for p in prefs
        )

        await mgr.delete_preference("display_name")
        prefs = await mgr.get_all_preferences()
        assert not any(p["key"] == "display_name" for p in prefs)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_capacity_limit():
    from app.services.unified_agent.preference_manager import PreferenceManager

    pool = await _make_pool()
    try:
        mgr = PreferenceManager(pool, "grapgrap", "test-user-prefs-002")

        result = await mgr.set_preference("invalid_key_xyz", "val", "explicit_chat")
        assert result["status"] == "invalid_key"

        await mgr.delete_all()
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_unevictable_keys():
    from app.services.unified_agent.preference_manager import PreferenceManager

    pool = await _make_pool()
    try:
        mgr = PreferenceManager(pool, "grapgrap", "test-user-prefs-003")

        await mgr.set_preference("display_name", "Bu Grace", "explicit_chat")
        await mgr.set_preference("output_format", "tabel", "explicit_chat")

        evicted = await mgr.get_eviction_candidates()
        assert "display_name" not in [e["key"] for e in evicted]
        assert any(e["key"] == "output_format" for e in evicted)

        await mgr.delete_all()
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_preference_context_format():
    from app.services.unified_agent.preference_manager import PreferenceManager

    pool = await _make_pool()
    try:
        mgr = PreferenceManager(pool, "grapgrap", "test-user-prefs-004")

        await mgr.set_preference("display_name", "Bu Grace", "explicit_chat")
        await mgr.set_preference("language_style", "santai", "explicit_chat")

        context = await mgr.get_preference_context()
        assert "Bu Grace" in context
        assert "santai" in context

        await mgr.delete_all()
    finally:
        await pool.close()
