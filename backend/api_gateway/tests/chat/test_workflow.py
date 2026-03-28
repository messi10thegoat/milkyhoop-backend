"""
Workflow Lifecycle Tests — 5 scenarios
Tests multi-turn, cancellation, chitchat.
"""
import uuid
from conftest import TestSuite, run_test, THRESHOLDS


async def run_workflow_tests(suite: TestSuite):
    print("\n── Workflow Lifecycle Tests (5 scenarios) ──")

    # 1. Chitchat → should respond quickly, not propose CRUD
    await run_test(
        suite,
        "chitchat_greeting",
        "Halo, apa kabar?",
        expect_type="TEXT",
        max_latency=THRESHOLDS["chitchat"],
    )

    # 2. Chitchat — what can you do
    await run_test(
        suite,
        "chitchat_capabilities",
        "Kamu bisa bantu apa saja?",
        expect_type="TEXT",
        max_latency=THRESHOLDS["chitchat"],
    )

    # 3. Multi-turn: start CRUD then cancel (vague input may ask for name → TEXT ok)
    conv_id = str(uuid.uuid4())
    data = await run_test(
        suite,
        "workflow_start_crud",
        "Tambah pelanggan WorkflowTest telepon 081234567890",
        expect_type="DIRECT_ACTION_PREVIEW",
        conversation_id=conv_id,
        max_latency=THRESHOLDS["crud"],
    )

    # 4. Cancel mid-workflow
    await run_test(
        suite,
        "workflow_cancel",
        "Batal, ga jadi",
        expect_type="TEXT",
        conversation_id=conv_id,
        max_latency=THRESHOLDS["crud"],
    )

    # 5. After cancel, should handle new query normally (may not hit calc_engine if context lingers)
    await run_test(
        suite,
        "workflow_after_cancel",
        "Berapa item aktif?",
        expect_type="TEXT",
        conversation_id=conv_id,
        max_latency=THRESHOLDS["calc"],
    )
