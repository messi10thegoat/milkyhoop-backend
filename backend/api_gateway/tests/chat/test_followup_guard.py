"""
Follow-Up Guard Tests — 5 scenarios
Validates that contextual follow-up questions route to agent loop,
while standalone queries stay in pipeline (no false positives).
"""
import uuid
import httpx
from conftest import TestSuite, run_test, THRESHOLDS, CHAT_URL


async def send_with_session(
    suite: TestSuite, text: str, conversation_id: str, session_id: str = None
) -> dict:
    """Send chat message with optional session_id for multi-turn."""
    token = await suite.get_token()
    payload = {"text": text, "conversation_id": conversation_id}
    if session_id:
        payload["session_id"] = session_id
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            CHAT_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def run_followup_guard_tests(suite: TestSuite):
    print("\n── Follow-Up Guard Tests (5 scenarios) ──")

    # Test 1: Follow-up after piutang — agent loop should resolve
    conv1 = f"followup-test-1-{uuid.uuid4().hex[:8]}"
    r1a = await send_with_session(suite, "piutang berapa?", conv1)
    sid1 = r1a.get("session_id")
    # Step B: implicit follow-up
    r1b = await send_with_session(
        suite, "Rp 150 ribu itu dari pelanggan siapa?", conv1, sid1
    )
    text1 = (r1b.get("text") or "").lower()
    err1 = []
    if "nama barang" in text1 or "mohon sebutkan" in text1:
        err1.append("got item prompt instead of AR follow-up")
    if not any(w in text1 for w in ["sintia", "pelanggan", "customer", "inv-"]):
        err1.append("missing customer/invoice info")
    if r1b.get("latency_ms", 0) > THRESHOLDS["agent_loop"]:
        err1.append(f"slow: {r1b.get(latency_ms)}ms")
    from conftest import TestResult

    suite.record(
        TestResult(
            name="followup_piutang_dari_siapa",
            passed=len(err1) == 0,
            latency_ms=r1b.get("latency_ms", 0),
            model=r1b.get("model_used", ""),
            message_type=r1b.get("message_type", ""),
            error="; ".join(err1),
            response_text=(r1b.get("text") or "")[:200],
        )
    )

    # Test 2: Follow-up after hutang — "yang paling besar"
    conv2 = f"followup-test-2-{uuid.uuid4().hex[:8]}"
    r2a = await send_with_session(suite, "hutang gw berapa?", conv2)
    sid2 = r2a.get("session_id")
    r2b = await send_with_session(suite, "yang paling besar yang mana?", conv2, sid2)
    text2 = (r2b.get("text") or "").lower()
    err2 = []
    if "nama barang" in text2 or "mohon sebutkan" in text2:
        err2.append("got item prompt instead of AP follow-up")
    if r2b.get("latency_ms", 0) > THRESHOLDS["agent_loop"]:
        err2.append(f"slow: {r2b.get(latency_ms)}ms")
    suite.record(
        TestResult(
            name="followup_hutang_yang_terbesar",
            passed=len(err2) == 0,
            latency_ms=r2b.get("latency_ms", 0),
            model=r2b.get("model_used", ""),
            message_type=r2b.get("message_type", ""),
            error="; ".join(err2),
            response_text=(r2b.get("text") or "")[:200],
        )
    )

    # Test 3: Standalone — no false positive
    await run_test(
        suite,
        "followup_no_false_positive_standalone",
        "berapa total stok?",
        expect_type="TEXT",
        expect_model="calc_engine",
        expect_contains=["stok"],
        max_latency=THRESHOLDS["calc"],
    )

    # Test 4: Explicit keyword after context — stays in pipeline
    conv4 = f"followup-test-4-{uuid.uuid4().hex[:8]}"
    await send_with_session(suite, "piutang berapa?", conv4)
    await run_test(
        suite,
        "followup_explicit_keyword_stays_pipeline",
        "daftar kategori",
        expect_type="TEXT",
        expect_contains=["kategori"],
        max_latency=THRESHOLDS["query_pipeline"],
        conversation_id=conv4,
    )

    # Test 5: Pronoun without session context — should NOT trigger guard
    await run_test(
        suite,
        "followup_no_context_no_guard",
        "data pelanggan tersebut",
        expect_type="TEXT",
        max_latency=THRESHOLDS[
            "agent_loop"
        ],  # may go to agent loop but not guard-triggered
    )
