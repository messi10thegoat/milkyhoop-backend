import uuid
import time
import subprocess
import httpx
from goldset.scoring import score_turn
from goldset.tiers import derive_tier

BASE = "https://milkyhoop.com"
UA = {"User-Agent": "Mozilla/5.0 (goldset-harness)"}


def login(
    email="grapmanado@gmail.com",
    password="grapgrap007",  # pragma: allowlist secret
):
    r = httpx.post(
        f"{BASE}/api/auth/login",
        json={"email": email, "password": password},
        headers=UA,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    # conftest.py: resp.json()["data"]["access_token"]
    top = data.get("access_token") or data.get("token")
    if top:
        return top
    nested = (data.get("data") or {}).get("access_token")
    return nested


def send(token, conversation_id, message):
    h = {**UA, "Authorization": f"Bearer {token}"}
    # CAPTURE_NOTES: request field is "text" (not "message"), per conftest.py
    r = httpx.post(
        f"{BASE}/api/v3/chat/message",
        json={"text": message, "conversation_id": conversation_id},
        headers=h,
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


def _intent_from_db(session_id=None):
    # Task 1 spike: pipeline/agent-loop paths log final_intent to intent_decision_log.
    # The table has no trace_id column — correlation is via session_id or recency.
    # projection_engine bypasses this log entirely (CAPTURE_NOTES §5).
    # postgres superuser avoids RLS surprises; harness runs on the docker host.
    if not session_id:
        return ""
    sql = (
        f"SELECT final_intent FROM intent_decision_log "
        f"WHERE session_id='{session_id}' ORDER BY ts DESC LIMIT 1;"
    )
    pg_cmd = f'PGPASSWORD=Proyek771977 psql -U postgres -d milkydb -t -A -c "{sql}"'  # pragma: allowlist secret
    cmd = [
        "docker",
        "exec",
        "milkyhoop-dev-postgres-1",
        "sh",
        "-c",
        pg_cmd,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def observe(resp):
    # CAPTURE_NOTES: NO top-level `intent` field. Resolve via fallbacks.
    # Response text field is `text` (not `message` or `response`).
    text = resp.get("text") or ""
    model = resp.get("model_used", "") or ""
    mtype = resp.get("message_type", "TEXT") or "TEXT"
    iters = resp.get("iterations", 1) or 1

    # Primary: tool_calls[0].args.intent
    tcs = resp.get("tool_calls")
    intent = ""
    if isinstance(tcs, list) and tcs and isinstance(tcs[0], dict):
        args = tcs[0].get("args") or {}
        intent = args.get("intent") or ""

    # Secondary: data.action_key (DIRECT_ACTION_PREVIEW only)
    if not intent:
        data = resp.get("data") or {}
        if data.get("action_key"):
            intent = data["action_key"]

    # projection_engine special case: tool_calls is null AND bypasses intent_decision_log.
    # CAPTURE_NOTES §5: "harness should accept it as valid even if not in DB telemetry."
    # The only model that uses this path is projection_engine → intent is deterministic.
    if not intent and model == "projection_engine":
        intent = "query_gross_profit_projection"

    # Fallback: DB intent_decision_log by session_id (no trace_id column in table).
    # Covers agent-loop / pipeline paths that do write to the log.
    # NOTE: intent_decision_log.session_id maps to the conversation_id we sent.
    session_id = resp.get("session_id")
    if not intent and session_id:
        intent = _intent_from_db(session_id=session_id)

    return {
        "intent": intent,
        "tier": derive_tier(intent, model, mtype, iters),
        "text": text,
        "message_type": mtype,
        "model_used": model,
    }


def run_case(token, case):
    conv = str(uuid.uuid4())
    turns_out = []
    for turn in case.turns:
        resp = send(token, conv, turn.query)
        obs = observe(resp)
        scored = score_turn(turn, obs)
        turns_out.append({"query": turn.query, "obs": obs, "asserts": scored})
        time.sleep(0.5)  # gentle pacing
    passed = all(ok for t in turns_out for (_a, ok) in t["asserts"])
    return {
        "id": case.id,
        "category": case.category,
        "why": case.why,
        "passed": passed,
        "turns": turns_out,
    }
