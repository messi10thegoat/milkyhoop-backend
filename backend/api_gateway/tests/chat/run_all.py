#!/usr/bin/env python3
"""
MilkyHoop Chat Bot — Automated Test Suite
Run: python3 run_all.py
"""
import asyncio
import sys
import time

from conftest import TestSuite
from test_crud import run_crud_tests
from test_query import run_query_tests
from test_workflow import run_workflow_tests
from test_regression import run_regression_tests
from test_pipeline_expansion import run_pipeline_expansion_tests
from test_followup_guard import run_followup_guard_tests


async def main():
    suite = TestSuite()
    start = time.time()

    print("=" * 60)
    print("  MilkyHoop Chat Bot — Test Suite v1.1")
    print("  Target: https://milkyhoop.com/api/v3/chat/message")
    print("=" * 60)

    # Run test batches sequentially (token refresh per batch)
    await run_crud_tests(suite)
    await run_query_tests(suite)
    await run_workflow_tests(suite)
    await run_regression_tests(suite)
    await run_pipeline_expansion_tests(suite)
    await run_followup_guard_tests(suite)

    elapsed = time.time() - start
    suite.print_summary()
    print(f"  Total time: {elapsed:.1f}s")

    # Exit code: 0 if all pass, 1 if any fail
    failed = sum(1 for r in suite.results if not r.passed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
