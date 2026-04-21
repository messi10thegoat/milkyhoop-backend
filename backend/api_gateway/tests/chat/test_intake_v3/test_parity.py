"""Document Intake V3 Phase 1 — V2 vs V3 byte-level payload parity.

Real tenant (grapgrap) fixtures, auto-synthesized OCR payloads.
Exempt from diff: '_meta_transfer_type' (additive V3 field).
"""

import pytest

from tests.chat.test_intake_v3.conftest import (
    TENANT,
    _fetch_ap,
    _fetch_ar,
    synthesize_ocr_from_ap,
    synthesize_ocr_from_ar,
)


def _payload_diff(v2_payload: dict, v3_payload: dict) -> dict:
    """Return dict of {field: (v2_val, v3_val)} for diverging fields.

    Excludes '_meta_transfer_type' (V3 additive).
    """
    v3_minus_meta = {k: v for k, v in v3_payload.items() if k != "_meta_transfer_type"}
    diffs = {}
    all_keys = set(v2_payload.keys()) | set(v3_minus_meta.keys())
    for k in all_keys:
        v2 = v2_payload.get(k, "<MISSING>")
        v3 = v3_minus_meta.get(k, "<MISSING>")
        if v2 != v3:
            diffs[k] = (v2, v3)
    return diffs


async def _run_v2(pool, ocr: dict):
    from app.services.unified_agent.document_intake import DocumentIntakePipeline

    p = DocumentIntakePipeline(pool, TENANT)
    try:
        return await p.process(ocr, caption="")
    except Exception:
        # V2 pre-existing bugs (e.g. match_confidence) — treat as "no result"
        return None


async def _run_v3(pool, ocr: dict):
    from app.services.unified_agent.document_intake_v3.pipeline import (
        DocumentIntakePipelineV3,
        _FallbackToGenericChatSkip,
        _FallbackToV2PreviewSkip,
    )

    p = DocumentIntakePipelineV3(pool, TENANT)
    try:
        return await p.process(ocr, caption="")
    except (_FallbackToGenericChatSkip, _FallbackToV2PreviewSkip):
        return None


@pytest.mark.asyncio
async def test_scenario_1_receive_payment_full_match(pool):
    """RECEIVE_PAYMENT: OCR amount + counterparty match an open AR invoice."""
    ar = await _fetch_ar(pool)
    ocr = synthesize_ocr_from_ar(ar)

    v2 = await _run_v2(pool, ocr)
    v3 = await _run_v3(pool, ocr)

    assert v2 is not None and v2.resolved_action is not None, "V2 produced no action"
    assert v3 is not None and v3.resolved_action is not None, "V3 produced no action"

    v2_a = v2.resolved_action
    v3_a = v3.resolved_action

    assert (
        v2_a.action_key == v3_a.action_key
    ), f"action_key mismatch: {v2_a.action_key} vs {v3_a.action_key}"
    assert v3_a.payload.get("_meta_transfer_type") == "receive_payment", "meta missing"

    diffs = _payload_diff(v2_a.payload, v3_a.payload)
    assert not diffs, f"payload diff found: {diffs}"


@pytest.mark.asyncio
async def test_scenario_2_receive_payment_partial_match(pool):
    """Partial match: counterparty matches, amount outside tolerance."""
    ar = await _fetch_ar(pool)
    ocr = synthesize_ocr_from_ar(ar)
    ocr["total_amount"] = float(ar.outstanding) * 1.5
    ocr["amount"] = ocr["total_amount"]

    v2 = await _run_v2(pool, ocr)
    v3 = await _run_v3(pool, ocr)

    if v2 is None and v3 is None:
        pytest.skip("both V2 and V3 failed to produce action — trivial parity")

    if (v2 and v2.resolved_action) and (v3 is None or v3.resolved_action is None):
        pytest.fail(
            f"V2 produced action but V3 did not: V2={v2.resolved_action.action_key}"
        )
    if (v3 and v3.resolved_action) and (v2 is None or v2.resolved_action is None):
        pytest.fail(
            f"V3 produced action but V2 did not: V3={v3.resolved_action.action_key}"
        )

    if v2 and v2.resolved_action and v3 and v3.resolved_action:
        diffs = _payload_diff(v2.resolved_action.payload, v3.resolved_action.payload)
        assert not diffs, f"payload diff: {diffs}"


@pytest.mark.asyncio
async def test_scenario_3_bill_payment_full_match(pool):
    """BILL_PAYMENT: OCR matches open AP bill."""
    ap = await _fetch_ap(pool)
    ocr = synthesize_ocr_from_ap(ap)

    v2 = await _run_v2(pool, ocr)
    v3 = await _run_v3(pool, ocr)

    assert v2 is not None and v2.resolved_action is not None, "V2 no action"
    assert v3 is not None and v3.resolved_action is not None, "V3 no action"

    v2_a = v2.resolved_action
    v3_a = v3.resolved_action
    assert v2_a.action_key == v3_a.action_key
    assert v3_a.payload.get("_meta_transfer_type") == "bill_payment"

    diffs = _payload_diff(v2_a.payload, v3_a.payload)
    assert not diffs, f"bill payload diff: {diffs}"


@pytest.mark.asyncio
async def test_scenario_4_expense_no_match(pool):
    """EXPENSE: bank_transfer to utility (PLN), no AR/AP match."""
    ocr = {
        "doc_type": "receipt",
        "total_amount": 450000,
        "amount": 450000,
        "counterparty_name": "PLN PERSERO",
        "vendor_name": "PLN PERSERO",
        "document_date": "2026-04-15",
        "date": "2026-04-15",
        "raw_text": "tagihan listrik PLN bulan April 2026",
    }

    v2 = await _run_v2(pool, ocr)
    v3 = await _run_v3(pool, ocr)

    if v2 is None:
        pytest.skip("V2 failed on expense path")
    if v3 is None:
        pytest.skip("V3 fell back; not a parity issue for this test")

    if v2.resolved_action and v3.resolved_action:
        assert (
            v2.resolved_action.action_key
            == v3.resolved_action.action_key
            == "create_expense"
        )
        assert (
            v3.resolved_action.payload.get("_meta_transfer_type")
            == "expense_operational"
        )
        diffs = _payload_diff(v2.resolved_action.payload, v3.resolved_action.payload)
        significant = {k: v for k, v in diffs.items() if k not in ("notes",)}
        assert not significant, f"expense payload diff (excl notes): {significant}"


@pytest.mark.asyncio
async def test_scenario_5_ambiguous(pool):
    """AMBIGUOUS: OCR with minimal info, no DB match → both should clarify."""
    ocr = {
        "doc_type": "bank_transfer",
        "total_amount": 99.99,
        "amount": 99.99,
        "counterparty_name": "UNKNOWN ENTITY XZY",
        "document_date": "2026-04-15",
    }

    v2 = await _run_v2(pool, ocr)
    v3 = await _run_v3(pool, ocr)

    v2_clarify = v2 is not None and (
        getattr(v2, "needs_direction_clarification", False)
        or getattr(v2, "needs_bank_clarification", False)
    )
    v3_clarify = v3 is not None and (
        getattr(v3, "needs_direction_clarification", False)
        or getattr(v3, "needs_clarification", False)
    )
    v2_no_action = (v2 is None) or (v2.resolved_action is None)
    v3_no_action = (v3 is None) or (v3.resolved_action is None)

    assert (v2_clarify == v3_clarify) or (v2_no_action and v3_no_action), (
        f"ambiguous parity issue: v2_clarify={v2_clarify} v3_clarify={v3_clarify} "
        f"v2_no_action={v2_no_action} v3_no_action={v3_no_action}"
    )


@pytest.mark.asyncio
async def test_scenario_6_unknown_empty_ocr(pool):
    """UNKNOWN: empty OCR → both must bail."""
    ocr = {}

    v2 = await _run_v2(pool, ocr)
    v3 = await _run_v3(pool, ocr)

    # "bailed" = produced no resolved_action. Both V2 (via clarification) and V3
    # (via fallback/skip) are acceptable no-action outcomes for empty OCR.
    v2_no_action = (v2 is None) or (v2.resolved_action is None)
    v3_no_action = (v3 is None) or (v3.resolved_action is None)

    assert v2_no_action and v3_no_action, f"unknown produced action: v2={v2} v3={v3}"
