"""
Regression: sales_invoice float/Decimal TypeError at _internal_post_invoice.

Root cause (surfaced by Bucket 0 narrowed exception handling):

  app/routers/sales_invoices.py:1355
    subtotal_amount = total_amount - tax_amount
  TypeError: unsupported operand type(s) for -: 'float' and 'decimal.Decimal'

Origin: Pydantic schema declares `total_amount: float` (CreateInvoiceRequest).
asyncpg returns `numeric(18,2)` as Decimal (Iron Law 25 / V115).
Boundary fix: coerce `total_amount = _d(total_amount)` at the entry of
`_internal_post_invoice` so downstream arithmetic is Decimal-consistent.

Tests:
  1. Integration: chat-driven create + confirm produces a posted invoice,
     with no TypeError swallowed.
  2. Unit: _internal_post_invoice must accept float/int/mixed total_amount
     without raising TypeError.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest

from conftest import BASE_URL, CREDENTIALS, LOGIN_URL

CHAT_URL = f"{BASE_URL}/api/v3/chat/message"


async def _login_token() -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(LOGIN_URL, json=CREDENTIALS)
        r.raise_for_status()
        return r.json()["data"]["access_token"]


async def _send(token: str, text: str, conv_id: str) -> dict:
    async with httpx.AsyncClient(timeout=45.0) as client:
        r = await client.post(
            CHAT_URL,
            json={"text": text, "conversation_id": conv_id, "session_id": conv_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()


@pytest.mark.asyncio
async def test_create_sales_invoice_via_chat_no_typeerror():
    """End-to-end: natural-language create + confirm must not raise
    TypeError inside _internal_post_invoice. Response must be
    ACTION_RESULT with 'berhasil' (success)."""
    token = await _login_token()
    conv = str(uuid.uuid4())

    # Turn 1: request preview
    turn1 = await _send(
        token,
        "Buat faktur untuk Maju Jaya, 10 pcs kaos @50000",
        conv,
    )
    assert (
        turn1.get("message_type")
        in (
            "DIRECT_ACTION_PREVIEW",
            "ACTION_PREVIEW",
        )
    ), f"expected preview, got {turn1.get('message_type')}: {turn1.get('text','')[:200]}"

    # Turn 2: confirm -> should post via _internal_post_invoice
    turn2 = await _send(token, "betul", conv)
    assert turn2.get("message_type") == "ACTION_RESULT", (
        f"expected ACTION_RESULT, got {turn2.get('message_type')}: "
        f"{turn2.get('text','')[:300]}"
    )
    text = (turn2.get("text") or "").lower()
    assert "berhasil" in text, f"expected success message, got: {text[:300]}"
    # Negative: the previously-swallowed TypeError produced generic error text
    assert "unsupported operand" not in text
    assert "typeerror" not in text


def test_d_helper_accepts_float():
    """Unit: the `_d` boundary coerces floats without carrying imprecision.
    Uses Decimal(str(value)) per Iron Law 25 discipline."""
    # Import the helper directly (in-process)
    from app.routers.sales_invoices import _d

    result = _d(123.45)
    assert isinstance(result, Decimal)
    # str() coercion avoids binary-float artifacts like 123.4499999...
    assert result == Decimal("123.45")

    # Mixed arithmetic against Decimal must not raise
    tax = Decimal("10.00")
    assert result - tax == Decimal("113.45")


def test_d_helper_accepts_int_and_decimal():
    """Unit baseline: ints and Decimals must still round-trip correctly."""
    from app.routers.sales_invoices import _d

    assert _d(500000) == Decimal("500000")
    assert _d(Decimal("1234.56")) == Decimal("1234.56")
    assert _d(None) == Decimal("0")
    assert _d(0) == Decimal("0")


def test_d_helper_mixed_json_shapes():
    """Unit: the shapes JSON parsing produces (int for round numbers,
    float for decimals, str for arbitrary precision) must all coerce."""
    from app.routers.sales_invoices import _d

    # As JSON would parse a payload {"total_amount": 500000}
    assert _d(500000) - Decimal("50000.00") == Decimal("450000")
    # As JSON would parse {"total_amount": 500000.5}
    assert _d(500000.5) - Decimal("0.50") == Decimal("500000.00")
    # As produced by marshaling via str()
    assert _d("500000.50") - Decimal("0.50") == Decimal("500000.00")
