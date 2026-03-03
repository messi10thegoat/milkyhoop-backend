import asyncio
"""
Automated E2E test harness for MilkyHoop unified agent.
Runs scenarios against live API, captures responses, diagnoses failures per layer.

Usage:
    from test_harness import TestHarness, TestScenario
    harness = TestHarness(base_url, token)
    result = await harness.run_scenario(scenario)
"""

import json
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

logger = logging.getLogger("e2e.harness")


class TestResult(Enum):
    PASS = "PASS"
    FAIL_AGENT = "FAIL_AGENT"
    FAIL_ENRICHMENT = "FAIL_ENRICHMENT"
    FAIL_KERNEL = "FAIL_KERNEL"
    FAIL_DATA = "FAIL_DATA"
    FAIL_TOOL = "FAIL_TOOL"
    FAIL_FORMAT = "FAIL_FORMAT"
    FAIL_TIMEOUT = "FAIL_TIMEOUT"
    SKIP = "SKIP"


@dataclass
class TestScenario:
    id: str
    category: str
    description: str
    messages: List[str]
    expect_behavior: List[str] = field(default_factory=list)
    expect_action_type: Optional[str] = None
    expect_contains: List[str] = field(default_factory=list)
    expect_not_contains: List[str] = field(default_factory=list)
    expect_tool_calls: Optional[List[str]] = None
    expect_confirm_success: Optional[bool] = None
    verify_created: Optional[Dict] = None
    verify_journal: Optional[Dict] = None
    verify_ledger_impact: Optional[Dict] = None
    preconditions: List[str] = field(default_factory=list)
    timeout_seconds: int = 60
    tags: List[str] = field(default_factory=list)


class TestHarness:
    """Automated E2E test runner with layer-based failure diagnosis."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
        self.results: List[Dict] = []

    async def run_scenario(self, scenario: TestScenario) -> Dict:
        """Run one test scenario, return detailed result with diagnosis."""
        conv_id = f"e2e-{scenario.id}-{uuid4().hex[:8]}"
        result = {
            "id": scenario.id,
            "description": scenario.description,
            "category": scenario.category,
            "tags": scenario.tags,
            "status": None,
            "diagnosis": None,
            "layer": None,
            "turns": [],
            "duration_ms": 0,
            "timestamp": datetime.now().isoformat(),
        }

        start = time.time()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        try:
            # Check preconditions
            for precondition in scenario.preconditions:
                met = await self._check_precondition(precondition, headers)
                if not met:
                    result["status"] = TestResult.SKIP.value
                    result["diagnosis"] = f"Precondition failed: {precondition}"
                    result["duration_ms"] = int((time.time() - start) * 1000)
                    self.results.append(result)
                    return result

            # Run conversation turns
            last_turn_data = None
            for i, message in enumerate(scenario.messages):
                if i > 0:
                    await asyncio.sleep(1)
                turn_start = time.time()
                try:
                    resp = None
                    for _retry in range(3):
                        async with httpx.AsyncClient(timeout=scenario.timeout_seconds) as client:
                            resp = await client.post(
                                f"{self.base_url}/api/v3/chat/message",
                                json={"conversation_id": conv_id, "text": message},
                                headers=headers,
                            )
                        if resp.status_code == 429 and _retry < 2:
                            logger.info(f"Rate limited on turn {i+1}, retry {_retry+1}/2")
                            await asyncio.sleep(5)
                            continue
                        break
                except httpx.TimeoutException:
                    result["status"] = TestResult.FAIL_TIMEOUT.value
                    result["diagnosis"] = f"Turn {i+1} timeout after {scenario.timeout_seconds}s"
                    result["layer"] = "infra"
                    break

                turn_ms = int((time.time() - turn_start) * 1000)
                turn_data = resp.json() if resp.status_code == 200 else {"error": resp.text, "status_code": resp.status_code}
                turn_record = {
                    "turn": i + 1,
                    "input": message,
                    "status_code": resp.status_code,
                    "message_type": turn_data.get("message_type", "unknown"),
                    "text": (turn_data.get("text") or "")[:500],
                    "pending_action_id": turn_data.get("pending_action_id"),
                    "tool_calls": turn_data.get("tool_calls") or [],
                    "iterations": turn_data.get("iterations"),
                    "duration_ms": turn_ms,
                }
                result["turns"].append(turn_record)
                last_turn_data = turn_data

                if resp.status_code != 200:
                    result["status"] = TestResult.FAIL_FORMAT.value
                    result["diagnosis"] = f"Turn {i+1}: HTTP {resp.status_code}"
                    result["layer"] = "infra"
                    break

                # Check expected behavior for this turn
                if i < len(scenario.expect_behavior):
                    expected = scenario.expect_behavior[i]
                    actual_mt = turn_data.get("message_type", "unknown")

                    if expected == "propose_action" and actual_mt != "ACTION_PREVIEW":
                        tool_calls = turn_data.get("tool_calls") or []
                        failed_tools = [tc for tc in tool_calls if not tc.get("success")]
                        if failed_tools:
                            result["status"] = TestResult.FAIL_TOOL.value
                            result["diagnosis"] = f"Turn {i+1}: expected ACTION_PREVIEW, got {actual_mt}. Tool failures: {[t['name'] for t in failed_tools]}"
                            result["layer"] = "tool"
                        else:
                            result["status"] = TestResult.FAIL_AGENT.value
                            result["diagnosis"] = f"Turn {i+1}: expected ACTION_PREVIEW, got {actual_mt}"
                            result["layer"] = "agent/prompt"
                        break

                    elif expected == "text_response" and actual_mt not in ("TEXT", "text"):
                        result["status"] = TestResult.FAIL_AGENT.value
                        result["diagnosis"] = f"Turn {i+1}: expected TEXT, got {actual_mt}"
                        result["layer"] = "agent/prompt"
                        break

                    elif expected == "clarification":
                        text = turn_data.get("text", "")
                        is_question = "?" in text or any(w in text.lower() for w in ["mana", "yang mana", "pilih", "maksudnya"])
                        if actual_mt not in ("TEXT", "CLARIFICATION") or not is_question:
                            result["status"] = TestResult.FAIL_AGENT.value
                            result["diagnosis"] = f"Turn {i+1}: expected clarification question, got {actual_mt}"
                            result["layer"] = "agent/prompt"
                            break

                    elif expected == "error_message":
                        text = turn_data.get("text", "")
                        if actual_mt not in ("TEXT", "VALIDATION_ERROR"):
                            result["status"] = TestResult.FAIL_AGENT.value
                            result["diagnosis"] = f"Turn {i+1}: expected error message, got {actual_mt}"
                            result["layer"] = "agent/prompt"
                            break

            # Confirm step
            if result["status"] is None and scenario.expect_confirm_success is not None:
                if not last_turn_data:
                    result["status"] = TestResult.FAIL_FORMAT.value
                    result["diagnosis"] = "No turn data to confirm"
                    result["layer"] = "harness"
                else:
                    pending_id = (last_turn_data or {}).get("pending_action_id")
                    if not pending_id:
                        result["status"] = TestResult.FAIL_ENRICHMENT.value
                        result["diagnosis"] = "ACTION_PREVIEW missing pending_action_id"
                        result["layer"] = "enrichment"
                    else:
                        time.sleep(0.5)
                        try:
                            confirm_resp = None
                            for _retry in range(3):
                                async with httpx.AsyncClient(timeout=30) as client:
                                    confirm_resp = await client.post(
                                        f"{self.base_url}/api/v3/chat/confirm",
                                        json={
                                            "conversation_id": conv_id,
                                            "pending_action_id": pending_id,
                                            "doc_status": "POSTED",
                                        },
                                        headers=headers,
                                    )
                                if confirm_resp.status_code == 429 and _retry < 2:
                                    logger.info(f"Rate limited on confirm, retry {_retry+1}/2")
                                    await asyncio.sleep(5)
                                    continue
                                break
                        except httpx.TimeoutException:
                            result["status"] = TestResult.FAIL_TIMEOUT.value
                            result["diagnosis"] = "Confirm timeout"
                            result["layer"] = "kernel"

                        if result["status"] is None:
                            confirm_data = confirm_resp.json()
                            confirm_mt = confirm_data.get("message_type", "unknown")
                            confirm_text = confirm_data.get("text", "") or ""

                            result["turns"].append({
                                "turn": "confirm",
                                "status_code": confirm_resp.status_code,
                                "message_type": confirm_mt,
                                "text": confirm_text[:500],
                            })

                            if scenario.expect_confirm_success:
                                if confirm_mt == "ACTION_RESULT" and "berhasil" in confirm_text.lower():
                                    pass
                                elif confirm_mt == "ACTION_RESULT":
                                    pass
                                elif confirm_mt == "VALIDATION_ERROR":
                                    errors = confirm_data.get("data", {}).get("errors", [])
                                    error_detail = "; ".join(e.get("message", "") for e in errors) if errors else confirm_text[:200]
                                    result["status"] = TestResult.FAIL_ENRICHMENT.value
                                    result["diagnosis"] = f"Confirm VALIDATION_ERROR: {error_detail}"
                                    result["layer"] = "enrichment"
                                else:
                                    result["status"] = TestResult.FAIL_KERNEL.value
                                    result["diagnosis"] = f"Confirm unexpected: {confirm_mt} - {confirm_text[:200]}"
                                    result["layer"] = "kernel"

            # Content checks
            if result["status"] is None and last_turn_data:
                last_content = (last_turn_data or {}).get("text", "") or ""
                confirm_turns = [t for t in result["turns"] if t.get("turn") == "confirm"]
                if confirm_turns:
                    last_content += " " + (confirm_turns[-1].get("text", "") or "")

                for must_contain in scenario.expect_contains:
                    if must_contain.lower() not in last_content.lower():
                        preview = ((last_turn_data or {}).get("data") or {}).get("preview", {})
                        payload_str = json.dumps(preview, ensure_ascii=False).lower()
                        if must_contain.lower() not in payload_str:
                            result["status"] = TestResult.FAIL_AGENT.value
                            result["diagnosis"] = f"Response missing: '{must_contain}'"
                            result["layer"] = "agent/prompt"
                            break

                for must_not in (scenario.expect_not_contains or []):
                    if must_not.lower() in last_content.lower():
                        result["status"] = TestResult.FAIL_AGENT.value
                        result["diagnosis"] = f"Response contains forbidden: '{must_not}'"
                        result["layer"] = "agent/prompt"
                        break

            # Journal verification
            if result["status"] is None and scenario.verify_journal and last_turn_data:
                preview = ((last_turn_data or {}).get("data") or {}).get("preview", {})
                journal_lines = preview.get("journal_lines", [])
                if scenario.verify_journal.get("balanced") and not preview.get("balanced", False):
                    result["status"] = TestResult.FAIL_KERNEL.value
                    result["diagnosis"] = "Journal not balanced"
                    result["layer"] = "kernel/accounting"

            # All checks passed
            if result["status"] is None:
                result["status"] = TestResult.PASS.value

        except Exception as e:
            result["status"] = TestResult.FAIL_FORMAT.value
            result["diagnosis"] = f"Unexpected error: {str(e)[:300]}"
            result["layer"] = "unknown"
            logger.exception(f"Scenario {scenario.id} error")

        result["duration_ms"] = int((time.time() - start) * 1000)
        self.results.append(result)
        return result

    async def run_all(self, scenarios: List[TestScenario]) -> Dict:
        """Run all scenarios sequentially, return summary report."""
        for scenario in scenarios:
            print(f"  [{scenario.id}] {scenario.description}...", end=" ", flush=True)
            result = await self.run_scenario(scenario)
            status_icon = "\u2705" if result["status"] == "PASS" else "\u23ed\ufe0f" if result["status"] == "SKIP" else "\u274c"
            print(f"{status_icon} {result['status']} ({result['duration_ms']}ms)")
            if result["diagnosis"]:
                print(f"       \u2192 [{result['layer']}] {result['diagnosis']}")
            time.sleep(3)
        return self._generate_report()

    def _generate_report(self) -> Dict:
        """Generate summary report with failure diagnosis per layer."""
        total = len(self.results)
        by_status = {}
        by_layer = {}
        by_category = {}

        for r in self.results:
            status = r["status"]
            by_status[status] = by_status.get(status, 0) + 1
            if r["layer"]:
                by_layer[r["layer"]] = by_layer.get(r["layer"], 0) + 1
            cat = r["category"]
            if cat not in by_category:
                by_category[cat] = {"pass": 0, "fail": 0, "skip": 0}
            if status == "PASS":
                by_category[cat]["pass"] += 1
            elif status == "SKIP":
                by_category[cat]["skip"] += 1
            else:
                by_category[cat]["fail"] += 1

        passed = by_status.get("PASS", 0)
        skipped = by_status.get("SKIP", 0)
        failed = total - passed - skipped

        return {
            "timestamp": datetime.now().isoformat(),
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": f"{passed / max(total - skipped, 1) * 100:.1f}%",
            "by_status": by_status,
            "failures_by_layer": by_layer,
            "by_category": by_category,
            "details": self.results,
        }

    async def _check_precondition(self, precondition: str, headers: Dict) -> bool:
        """Check if precondition is met. Format: 'type:value'"""
        try:
            ptype, pvalue = precondition.split(":", 1)
        except ValueError:
            return True

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if ptype == "customer_exists":
                    resp = await client.get(
                        f"{self.base_url}/api/customers/search",
                        params={"q": pvalue},
                        headers=headers,
                    )
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("data", [])
                    return len(items) > 0

                elif ptype == "item_exists":
                    resp = await client.get(
                        f"{self.base_url}/api/items",
                        params={"search": pvalue},
                        headers=headers,
                    )
                    data = resp.json()
                    items = data.get("data", data.get("items", []))
                    if isinstance(items, list):
                        return len(items) > 0
                    return False

                elif ptype == "vendor_exists":
                    resp = await client.get(
                        f"{self.base_url}/api/vendors",
                        params={"q": pvalue},
                        headers=headers,
                    )
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("data", [])
                    return len(items) > 0

        except Exception as e:
            logger.warning(f"Precondition check failed: {precondition}: {e}")
            return False

        return True
