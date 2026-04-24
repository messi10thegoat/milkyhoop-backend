"""Bucket A1.5 — First real action_patterns row verification.

Sends a natural-language invoice creation + confirm via /api/v3/chat/message,
then asserts the direct-action path fires `after_confirm` and writes a
well-formed action_patterns row.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from conftest import TestSuite
from _helpers import make_db_pool


@pytest.mark.asyncio
async def test_first_pattern_row_well_formed():
    suite = TestSuite()
    conv_id = str(uuid.uuid4())

    # Dev-only: truncate action_patterns so we can assert initial-write
    # semantics (confidence=0.30, usage_count=1) deterministically. This
    # table is repopulated by user behavior; losing it in dev is fine.
    db_pool = await make_db_pool()
    async with db_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE action_patterns")
        before_count = await conn.fetchval("SELECT COUNT(*) FROM action_patterns")
    assert before_count == 0, f"truncate failed: count={before_count}"

    # Turn 1: create bill (direct action propose).
    # NOTE: we use create_bill instead of create_sales_invoice because the
    # sales-invoice POST endpoint has a pre-existing float/Decimal bug
    # (sales_invoices.py:1355) that blocks end-to-end confirm → 500. Bills go
    # through a clean path and trigger the same after_confirm direct-action
    # wiring we are validating here. Both are in PATTERN_INTENTS, so this is
    # a representative test of the A1.5 contract.
    propose = await suite.send(
        "buat tagihan pembelian dari PT Test A15 Supplier, "
        "kain katun 5 meter 100 ribu",
        conversation_id=conv_id,
    )
    assert propose is not None, "Propose turn returned no response"

    # Turn 2: confirm via natural language (Bucket A1 route)
    confirm = await suite.send("betul", conversation_id=conv_id)
    assert confirm is not None, "Confirm turn returned no response"
    assert (
        confirm.get("data", {}).get("success") is True
    ), f"Direct-action confirm failed: {str(confirm)[:300]}"

    # Give the hook a moment to flush
    await asyncio.sleep(2.5)

    async with db_pool.acquire() as conn:
        after_count = await conn.fetchval("SELECT COUNT(*) FROM action_patterns")
        row = await conn.fetchrow(
            """
            SELECT intent, structure_key, confidence, usage_count, tenant_id
            FROM action_patterns
            ORDER BY created_at DESC
            LIMIT 1
            """
        )

    await db_pool.close()

    assert after_count > before_count, (
        f"No new action_patterns row after confirm "
        f"(before={before_count} after={after_count}). "
        f"Confirm response: {str(confirm)[:300]}"
    )
    assert row is not None, "No action_patterns row after confirm"
    assert row["intent"], f"intent empty: {row['intent']}"
    assert "create" in row["intent"].lower(), f"Unexpected intent: {row['intent']}"
    assert row["structure_key"], "structure_key empty"
    assert (
        len(row["structure_key"]) > 5
    ), f"structure_key too short: {row['structure_key']}"
    # Initial confidence = 0.30 per ActionMemory.record_pattern INSERT
    assert (
        float(row["confidence"]) == 0.30
    ), f"Expected initial confidence 0.30, got {row['confidence']}"
    assert row["usage_count"] == 1, f"Expected usage_count 1, got {row['usage_count']}"
    assert row["tenant_id"], "tenant_id empty"
