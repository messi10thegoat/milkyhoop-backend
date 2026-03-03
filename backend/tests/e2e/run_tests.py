#!/usr/bin/env python3
"""
MilkyHoop E2E Test Runner.

Usage:
  python run_tests.py                          # run all
  python run_tests.py --tag sales              # filter by tag
  python run_tests.py --tag foundation         # foundation health via agent
  python run_tests.py --category edge_case     # filter by category
  python run_tests.py --id SI-001,PI-001       # specific tests
  python run_tests.py --output results.json    # custom output file
"""

import asyncio
import argparse
import glob
import json
import sys
import time

import httpx
import yaml

from test_harness import TestHarness, TestScenario


BASE_URL = "http://localhost:8001"
AUTH_EMAIL = "grapmanado@gmail.com"
AUTH_PASSWORD = "Jalanatputno.4"
AUTH_TENANT = "evlogia"


async def cleanup_test_data():
    """Truncate idempotency_keys and delete pending_actions before test run."""
    print('Cleaning up test data...', end=' ', flush=True)
    try:
        import subprocess as sp
        cmd = [
            'docker', 'exec', 'milkyhoop-dev-postgres-1',
            'psql', '-U', 'milkyadmin', '-d', 'milkydb', '-c',
            'TRUNCATE idempotency_keys CASCADE; DELETE FROM pending_actions;'
        ]
        result = sp.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            print('OK')
        else:
            print(f'WARNING: {result.stderr.strip()}')
    except Exception as e:
        print(f'WARNING: cleanup failed: {e}')




async def get_token() -> str:
    """Login and get auth token."""
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{BASE_URL}/api/auth/login",
                    json={
                        "email": AUTH_EMAIL,
                        "password": AUTH_PASSWORD,
                        "tenant_slug": AUTH_TENANT,
                    },
                )
                if resp.status_code == 200:
                    return resp.json()["data"]["access_token"]
                elif resp.status_code == 429:
                    wait = resp.json().get("retry_after", 5)
                    print(f"  Rate limited, waiting {wait}s...")
                    await asyncio.sleep(wait + 1)
                else:
                    print(f"  Login attempt {attempt+1}: HTTP {resp.status_code}")
                    await asyncio.sleep(3)
            except Exception as e:
                print(f"  Login attempt {attempt+1}: {e}")
                await asyncio.sleep(5)

    print("Login failed after 3 attempts")
    sys.exit(1)


def load_scenarios(scenario_dir: str = "scenarios") -> list[TestScenario]:
    """Load all YAML scenario files."""
    scenarios = []
    for filepath in sorted(set(glob.glob(f"{scenario_dir}/*.yaml") + glob.glob(f"{scenario_dir}/**/*.yaml", recursive=True))):
        with open(filepath) as f:
            data = yaml.safe_load(f)
            for s in data.get("scenarios", []):
                scenarios.append(TestScenario(**s))
    return scenarios


def filter_scenarios(
    scenarios: list[TestScenario],
    tag: str | None = None,
    category: str | None = None,
    ids: str | None = None,
) -> list[TestScenario]:
    """Filter scenarios by tag, category, or specific IDs."""
    filtered = scenarios
    if tag:
        filtered = [s for s in filtered if tag in (s.tags or [])]
    if category:
        filtered = [s for s in filtered if s.category == category]
    if ids:
        id_list = [i.strip() for i in ids.split(",")]
        filtered = [s for s in filtered if s.id in id_list]
    return filtered


async def main():
    parser = argparse.ArgumentParser(description="MilkyHoop E2E Test Runner")
    parser.add_argument("--tag", help="Filter by tag (e.g. sales, foundation)")
    parser.add_argument("--category", help="Filter by category (e.g. happy_path, edge_case)")
    parser.add_argument("--id", help="Comma-separated test IDs (e.g. SI-001,PI-001)")
    parser.add_argument("--output", default="test_results.json", help="Output file")
    parser.add_argument("--scenarios", default="scenarios", help="Scenarios directory")
    args = parser.parse_args()

    # Load scenarios
    scenarios = load_scenarios(args.scenarios)
    if not scenarios:
        print(f"No scenarios found in {args.scenarios}/")
        sys.exit(1)

    # Filter
    scenarios = filter_scenarios(scenarios, tag=args.tag, category=args.category, ids=args.id)
    if not scenarios:
        print("No scenarios match the filter criteria")
        sys.exit(1)

    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  MilkyHoop E2E Test Suite")
    print(f"  Scenarios: {len(scenarios)}")
    if args.tag:
        print(f"  Tag filter: {args.tag}")
    if args.category:
        print(f"  Category filter: {args.category}")
    if args.id:
        print(f"  ID filter: {args.id}")
    print(f"{sep}\n")

    # Cleanup test data
    await cleanup_test_data()

    # Auth
    print("Authenticating...", end=" ", flush=True)
    token = await get_token()
    print("OK\n")

    # Run
    harness = TestHarness(base_url=BASE_URL, token=token)
    report = await harness.run_all(scenarios)

    # Summary
    print(f"\n{sep}")
    print(f"  RESULTS: {report['passed']}/{report['total']} passed ({report['pass_rate']})")
    print(f"  Skipped: {report['skipped']}")
    if report["failures_by_layer"]:
        print(f"\n  Failures by layer:")
        for layer, count in sorted(report["failures_by_layer"].items()):
            print(f"    {layer}: {count}")
    if report["by_category"]:
        print(f"\n  By category:")
        for cat, counts in sorted(report["by_category"].items()):
            print(f"    {cat}: {counts['pass']}\u2705 {counts['fail']}\u274c {counts['skip']}\u23ed\ufe0f")
    print(f"{sep}\n")

    # Save
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Full report saved to {args.output}")

    # Exit code
    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
