"""
Chat Bot Automated Test Suite — Fixtures & Helpers
Runs against /api/v3/chat/message (non-streaming JSON response).
Token refreshed per batch to handle short TTL.
"""
import time
import uuid
import httpx
from dataclasses import dataclass, field
from typing import Optional

BASE_URL = "https://milkyhoop.com"
LOGIN_URL = f"{BASE_URL}/api/auth/login"
CHAT_URL = f"{BASE_URL}/api/v3/chat/message"

CREDENTIALS = {
    "email": "grapmanado@gmail.com",
    "password": "grapgrap007",
    "tenant_slug": "grapgrap",
}

# Latency thresholds (ms)
THRESHOLDS = {
    "calc": 10000,
    "query_pipeline": 12000,
    "crud": 12000,
    "agent_loop": 30000,
    "chitchat": 15000,
}


@dataclass
class TestResult:
    name: str
    passed: bool
    latency_ms: int = 0
    model: str = ""
    message_type: str = ""
    error: str = ""
    response_text: str = ""


@dataclass
class TestSuite:
    results: list = field(default_factory=list)
    _token: Optional[str] = None
    _token_time: float = 0

    async def get_token(self) -> str:
        """Get fresh token, refresh if older than 30s."""
        if self._token and (time.time() - self._token_time) < 30:
            return self._token
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(LOGIN_URL, json=CREDENTIALS)
            resp.raise_for_status()
            self._token = resp.json()["data"]["access_token"]
            self._token_time = time.time()
        return self._token

    async def send(self, text: str, conversation_id: str = None) -> dict:
        """Send chat message, return parsed response dict."""
        token = await self.get_token()
        conv_id = conversation_id or str(uuid.uuid4())
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                CHAT_URL,
                json={"text": text, "conversation_id": conv_id, "session_id": conv_id},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            return resp.json()

    def record(self, result: TestResult):
        self.results.append(result)

    def print_summary(self):
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        print(f"\n{'='*80}")
        print(f"  CHAT BOT TEST SUITE — {passed}/{total} PASSED")
        print(f"{'='*80}")
        print(
            f"{'#':>3} {'Status':>6} {'Latency':>8} {'Model':>14} {'Type':>25} {'Test Name'}"
        )
        print(f"{'-'*3} {'-'*6} {'-'*8} {'-'*14} {'-'*25} {'-'*40}")
        for i, r in enumerate(self.results, 1):
            status = "PASS" if r.passed else "FAIL"
            lat = f"{r.latency_ms}ms" if r.latency_ms else "—"
            err = f" [{r.error[:50]}]" if r.error else ""
            print(
                f"{i:>3} {status:>6} {lat:>8} {r.model:>14} {r.message_type:>25} {r.name}{err}"
            )
        print(f"\n  Total: {total} | Passed: {passed} | Failed: {total - passed}")
        avg_lat = sum(r.latency_ms for r in self.results if r.latency_ms) / max(
            1, sum(1 for r in self.results if r.latency_ms)
        )
        print(f"  Avg latency: {avg_lat:.0f}ms")
        print()


async def run_test(
    suite: TestSuite,
    name: str,
    text: str,
    expect_type: str = None,
    expect_contains: list = None,
    expect_model: str = None,
    max_latency: int = None,
    conversation_id: str = None,
):
    """Run a single test case and record result."""
    try:
        data = await suite.send(text, conversation_id=conversation_id)
    except Exception as e:
        suite.record(TestResult(name=name, passed=False, error=str(e)[:100]))
        return None

    msg_type = data.get("message_type", "")
    latency = data.get("latency_ms", 0)
    model = data.get("model_used", "")
    resp_text = data.get("text", "")

    errors = []

    if expect_type and msg_type != expect_type:
        errors.append(f"type={msg_type}, expected={expect_type}")

    if expect_contains:
        for kw in expect_contains:
            if kw.lower() not in resp_text.lower():
                errors.append(f"missing '{kw}'")

    if expect_model and model != expect_model:
        errors.append(f"model={model}, expected={expect_model}")

    if max_latency and latency > max_latency:
        errors.append(f"slow: {latency}ms > {max_latency}ms")

    suite.record(
        TestResult(
            name=name,
            passed=len(errors) == 0,
            latency_ms=latency,
            model=model,
            message_type=msg_type,
            error="; ".join(errors),
            response_text=resp_text[:200],
        )
    )
    return data
