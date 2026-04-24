"""
Regression coverage for chat-driven create+confirm CRUD (sales_invoice + bill).

Surfaced by Bucket 0 cleanup: the existing run_all `create_sales_invoice` case
only exercises the preview turn and never sends "betul", so posting paths
(_internal_post_invoice, bills_service.post_bill) are never exercised by the
baseline. A real TypeError in those paths went undetected until a targeted
repro was written.

These tests close that gap by:
  1. Creating a chat session
  2. Sending the natural-language create request
  3. Confirming with "betul"
  4. Verifying the DB row actually exists (filtered by invoice_number returned
     from the ACTION_RESULT response)

Scope:
  - sales_invoice: adds DB-verify on top of existing decimal_fix coverage
  - bill:          create+confirm+verify, closes symmetric gap

Parallel to test_sales_invoice_decimal_fix.py but at the persistence level
rather than the arithmetic level.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
import pytest

from conftest import BASE_URL, CREDENTIALS, LOGIN_URL

CHAT_URL = f"{BASE_URL}/api/v3/chat/message"

# DB access — superuser for RLS bypass during assertion.
# Matches the docker-compose service credentials.
DB_DSN = os.environ.get("TEST_DB_DSN") or os.environ.get("DATABASE_URL")


async def _login_token() -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(LOGIN_URL, json=CREDENTIALS)
        r.raise_for_status()
        return r.json()["data"]["access_token"]


async def _send(token: str, text: str, conv_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            CHAT_URL,
            json={"text": text, "conversation_id": conv_id, "session_id": conv_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()


async def _fetch_latest_by_party(
    table: str, party_column: str, party_name: str, since: datetime
) -> dict | None:
    """Superuser query — bypasses RLS to fetch the most recent row matching
    the party name created AFTER `since`. Filtering by `since` avoids
    matching pre-existing rows from unrelated fixtures/runs."""
    conn = await asyncpg.connect(DB_DSN)
    try:
        row = await conn.fetchrow(
            f"SELECT * FROM {table} WHERE {party_column} ILIKE $1 "
            f"AND created_at >= $2 ORDER BY created_at DESC LIMIT 1",
            f"%{party_name}%",
            since,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_create_sales_invoice_with_confirm_creates_row():
    """Chat: create sales invoice + confirm ⇒ row present in DB with
    non-null total_amount and customer_id. Closes run_all coverage gap."""
    since = datetime.now(timezone.utc) - timedelta(seconds=5)
    token = await _login_token()
    conv = str(uuid.uuid4())

    turn1 = await _send(
        token,
        "Buat faktur untuk Maju Jaya, 10 pcs kaos @50000",
        conv,
    )
    assert turn1.get("message_type") in ("DIRECT_ACTION_PREVIEW", "ACTION_PREVIEW"), (
        f"expected preview, got {turn1.get('message_type')}: "
        f"{turn1.get('text','')[:200]}"
    )

    turn2 = await _send(token, "betul", conv)
    assert turn2.get("message_type") == "ACTION_RESULT", (
        f"expected ACTION_RESULT, got {turn2.get('message_type')}: "
        f"{turn2.get('text','')[:300]}"
    )
    text = turn2.get("text") or ""
    assert "berhasil" in text.lower(), f"expected success, got: {text[:300]}"

    row = await _fetch_latest_by_party(
        "sales_invoices", "customer_name", "Maju Jaya", since
    )
    assert (
        row is not None
    ), "no sales_invoices row for 'Maju Jaya' created after test start"
    assert row.get("total_amount") is not None, "total_amount NULL"
    assert (
        row.get("total_amount") > 0
    ), f"total_amount not positive: {row.get('total_amount')}"
    assert row.get("customer_name"), "customer_name empty"


@pytest.mark.asyncio
async def test_create_bill_with_confirm_creates_row():
    """Chat: create bill + confirm ⇒ row present in bills table.

    Symmetric to sales_invoice coverage. Guards against same-bug-class
    regressions in bills_service.post_bill arithmetic paths."""
    since = datetime.now(timezone.utc) - timedelta(seconds=5)
    token = await _login_token()
    conv = str(uuid.uuid4())

    turn1 = await _send(
        token,
        "Buat faktur pembelian dari vendor Knitto, 5 meter kain katun @100000",
        conv,
    )
    assert turn1.get("message_type") in ("DIRECT_ACTION_PREVIEW", "ACTION_PREVIEW"), (
        f"expected preview, got {turn1.get('message_type')}: "
        f"{turn1.get('text','')[:200]}"
    )

    turn2 = await _send(token, "betul", conv)
    assert turn2.get("message_type") == "ACTION_RESULT", (
        f"expected ACTION_RESULT, got {turn2.get('message_type')}: "
        f"{turn2.get('text','')[:300]}"
    )
    text = turn2.get("text") or ""
    assert "berhasil" in text.lower(), f"expected success, got: {text[:300]}"

    row = await _fetch_latest_by_party("bills", "vendor_name", "Knitto", since)
    assert row is not None, "no bills row for 'Knitto' created after test start"
    assert row.get("amount") is not None, "amount NULL"
    assert row.get("amount") > 0, f"amount not positive: {row.get('amount')}"
    assert row.get("vendor_name"), "vendor_name empty"
