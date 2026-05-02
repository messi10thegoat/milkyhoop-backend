"""Bucket 3 Step 1 — ActionService.edit_pending_action unit tests.

Per DOCS/plans/2026-04-29-mid-flow-edit-diagnosis.md (v3): storage = Postgres
pending_actions.action_plan JSONB (sales-invoice DIRECT_ACTION path).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.action_service import (
    ActionService,
    detect_edit_intent,
)


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def make_payload() -> dict:
    return {
        "customer_id": "cust-1",
        "customer_name": "Maju Jaya",
        "invoice_date": "2026-04-29",
        "due_date": "2026-05-29",
        "tax_rate": 0,
        "items": [{"description": "kaos", "quantity": 10, "unit_price": 50000}],
    }


class _FakeConn:
    """asyncpg connection mock supporting set_config, advisory lock, fetchrow,
    execute (UPDATE), and async context-manager `transaction()`."""

    def __init__(self, row: dict | None):
        self._row = row
        self.execute = AsyncMock(return_value="UPDATE 1")
        self.executed_calls: list[tuple] = []
        # capture UPDATE payloads
        # capture original (unused, kept for reference)
        _ = self.execute

        async def _exec(sql, *args):
            self.executed_calls.append((sql, args))
            return "OK"

        self.execute = _exec  # type: ignore

    async def fetchrow(self, sql, *args):
        return self._row

    def transaction(self):
        conn = self

        class _TxnCM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _TxnCM()


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _CM()


def make_service_with_row(row: dict | None) -> tuple[ActionService, _FakeConn]:
    conn = _FakeConn(row)
    pool = _FakePool(conn)
    svc = ActionService(pool=pool)  # type: ignore[arg-type]
    return svc, conn


def make_pg_row(
    *, status: str = "PENDING", version: int = 1, payload: dict | None = None
) -> dict:
    import uuid as _u
    from datetime import datetime, timedelta

    return {
        "id": _u.uuid4(),
        "status": status,
        "version": version,
        "action_plan": payload if payload is not None else make_payload(),
        "expires_at": datetime.utcnow() + timedelta(seconds=900),
    }


VALID_PENDING_ID = "11111111-1111-1111-1111-111111111111"


# ────────────────────────────────────────────────────────────────────
# T7 — detect_edit_intent precedence (sync)
# ────────────────────────────────────────────────────────────────────


def test_t7_detect_edit_intent_precedence():
    assert detect_edit_intent("ganti qty jadi 20") is True
    assert detect_edit_intent("ubah jadi mandiri") is True
    assert detect_edit_intent("koreksi harga") is True
    assert detect_edit_intent("tambah kaos") is True
    assert detect_edit_intent("qty 20 aja jadi 25") is True
    assert detect_edit_intent("betul ganti") is False
    assert detect_edit_intent("ya ganti aja") is False
    assert detect_edit_intent("batal ganti") is False
    assert detect_edit_intent("jangan ubah") is False
    assert detect_edit_intent("betul") is False
    assert detect_edit_intent("batal") is False
    assert detect_edit_intent("") is False
    assert detect_edit_intent("   ") is False
    assert detect_edit_intent("apa kabar") is False


# ────────────────────────────────────────────────────────────────────
# T1 — Happy path qty edit (regex)
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t1_qty_edit_regex():
    row = make_pg_row()
    svc, conn = make_service_with_row(row)

    result = await svc.edit_pending_action(
        tenant_id="tenant-1",
        pending_id=VALID_PENDING_ID,
        text="ganti qty jadi 20",
        action_key="create_sales_invoice",
    )

    assert result["success"] is True, result
    assert result["version"] == 2
    assert result["payload"]["items"][0]["quantity"] == 20
    assert result["payload"]["items"][0]["unit_price"] == 50000
    # UPDATE call captured
    update_calls = [c for c in conn.executed_calls if "UPDATE pending_actions" in c[0]]
    assert len(update_calls) == 1
    sql, args = update_calls[0]
    written_payload = json.loads(args[0])
    assert written_payload["items"][0]["quantity"] == 20
    assert args[1] == 2  # version


# ────────────────────────────────────────────────────────────────────
# T2 — Unknown field via LLM extractor → UNKNOWN_FIELD
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t2_unknown_field():
    row = make_pg_row()
    svc, _ = make_service_with_row(row)
    svc._extract_patch_llm = AsyncMock(return_value={"warna": "merah"})

    result = await svc.edit_pending_action(
        tenant_id="tenant-1",
        pending_id=VALID_PENDING_ID,
        text="ubah warna jadi merah",
        action_key="create_sales_invoice",
    )

    assert result["success"] is False
    assert result["error"] == "UNKNOWN_FIELD"
    assert "warna" in result["message"]


# ────────────────────────────────────────────────────────────────────
# T3 — Expired / not-found pending → EXPIRED
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t3_expired_pending():
    svc, _ = make_service_with_row(None)

    result = await svc.edit_pending_action(
        tenant_id="tenant-1",
        pending_id=VALID_PENDING_ID,
        text="ganti qty jadi 20",
        action_key="create_sales_invoice",
    )

    assert result["success"] is False
    assert result["error"] == "EXPIRED"


# ────────────────────────────────────────────────────────────────────
# T4 — Customer swap re-runs validation
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t4_customer_swap_revalidates():
    row = make_pg_row()
    svc, _ = make_service_with_row(row)
    svc._extract_patch_llm = AsyncMock(return_value={"customer_name": "Toko ABC"})
    revalidate_spy = AsyncMock(return_value=(True, []))
    svc._revalidate_action_plan = revalidate_spy

    result = await svc.edit_pending_action(
        tenant_id="tenant-1",
        pending_id=VALID_PENDING_ID,
        text="ganti customer jadi Toko ABC",
        action_key="create_sales_invoice",
    )

    assert result["success"] is True
    revalidate_spy.assert_awaited_once()
    args, _ = revalidate_spy.call_args
    _, payload_arg, action_key_arg = args
    assert payload_arg["customer_name"] == "Toko ABC"
    assert action_key_arg == "create_sales_invoice"


# ────────────────────────────────────────────────────────────────────
# T5 — Multi-field atomic edit
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t5_multi_field_atomic():
    row = make_pg_row()
    svc, _ = make_service_with_row(row)

    result = await svc.edit_pending_action(
        tenant_id="tenant-1",
        pending_id=VALID_PENDING_ID,
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
async def test_t6_ambiguous_llm():
    row = make_pg_row()
    svc, _ = make_service_with_row(row)
    svc._extract_patch_llm = AsyncMock(
        return_value={"_error": "ambiguous", "reason": "interrogative, not an edit"}
    )

    result = await svc.edit_pending_action(
        tenant_id="tenant-1",
        pending_id=VALID_PENDING_ID,
        text="kalau qty hasilnya berapa ya?",
        action_key="create_sales_invoice",
    )

    assert result["success"] is False
    assert result["error"] == "AMBIGUOUS_FIELD"
    assert "interrogative" in result["message"]
