"""
Integration tests for Tier 3 Structured Summary Generator.
"""
import json
import os

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_summary_structure():
    from app.services.unified_agent.summary_generator import generate_structured_summary

    messages = [
        {"role": "user", "content": "buat faktur untuk Sintia, Kain Taslan 50 pcs"},
        {"role": "assistant", "content": "Siap, saya buatkan faktur untuk Sintia..."},
        {"role": "user", "content": "ok, posting"},
        {"role": "assistant", "content": "Faktur INV-2604-0030 berhasil diposting."},
    ]
    events = [
        {
            "event_type": "propose",
            "action_type": "CREATE_SALES_INVOICE",
            "result_summary": "proposed",
        },
        {
            "event_type": "confirm",
            "action_type": "CREATE_SALES_INVOICE",
            "result_summary": "posted INV-2604-0030",
        },
    ]

    summary = await generate_structured_summary(messages, events)

    assert isinstance(summary, dict)
    assert "topic" in summary
    assert summary["outcome"] in (
        "posted",
        "draft_unfinished",
        "abandoned",
        "query_only",
    )
    assert "entities" in summary
    assert "customers" in summary["entities"]
    assert "unfinished" in summary


@pytest.mark.asyncio
async def test_summary_no_amounts():
    from app.services.unified_agent.summary_generator import generate_structured_summary

    messages = [
        {"role": "user", "content": "cek piutang Sintia, totalnya Rp 45.000.000"},
        {
            "role": "assistant",
            "content": "Piutang Sintia total Rp 45.000.000 dari 5 faktur.",
        },
    ]
    events = []

    summary = await generate_structured_summary(messages, events)
    summary_str = json.dumps(summary)

    assert "45.000.000" not in summary_str
    assert "45000000" not in summary_str


@pytest.mark.asyncio
async def test_gap_based_loading():
    from datetime import datetime, timezone

    from app.services.unified_agent.summary_generator import get_last_session_context

    pool = await asyncpg.create_pool(
        host=os.environ.get("DB_HOST", "postgres"),
        port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "Proyek771977"),
        database=os.environ.get("DB_NAME", "milkydb"),
        min_size=1,
        max_size=2,
    )
    try:
        context = await get_last_session_context(
            pool, "grapgrap", "test-user-summary", datetime.now(timezone.utc)
        )
        assert isinstance(context, str)
    finally:
        await pool.close()
