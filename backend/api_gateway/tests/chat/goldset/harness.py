import re
import uuid
import time
import subprocess
import httpx
from goldset.scoring import score_turn, score_behavior
from goldset.tiers import derive_tier

BASE = "https://milkyhoop.com"
UA = {"User-Agent": "Mozilla/5.0 (goldset-harness)"}

# CLARIFY heuristic: the bot asked a clarifying question instead of answering.
# Used by the 2-dimensional scorer (stock -> direct expected; flow -> clarify ok).
_CLARIFY_MARKERS = re.compile(
    r"\b(periode mana|periode berapa|periode kapan|untuk periode|bulan apa|"
    r"rentang waktu|dari kapan|sampai kapan|untuk kapan|"
    r"tanggal berapa|maksud anda|yang mana)\b",
    re.I,
)
_HAS_AMOUNT = re.compile(r"\bRp\b|\b\d{1,3}(\.\d{3})+\b", re.I)


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
        intent = (
            args.get("intent") or args.get("query_key") or ""
        )  # query_key = ARAP routing path

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

    # CLARIFY detection: did the bot ask a clarifying question instead of answering?
    clarified = bool(_CLARIFY_MARKERS.search(text)) and not _HAS_AMOUNT.search(text)

    return {
        "intent": intent,
        "tier": derive_tier(intent, model, mtype, iters),
        "text": text,
        "message_type": mtype,
        "model_used": model,
        "clarified": clarified,
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
    asserts_ok = all(ok for t in turns_out for (_a, ok) in t["asserts"])
    # Behavior dimension: query_class describes the FIRST turn's stimulus
    # (the stock-balance / period-flow question), so score behavior on turn 1.
    # For single-turn cases this is identical to the only turn; for followup
    # cases (e.g. followup_domain_carry) the stock query is turn 1, NOT the
    # last (pronoun/ordinal) turn.
    query_class = getattr(case, "query_class", None)
    first_obs = turns_out[0]["obs"] if turns_out else {}
    behavior, behavior_ok = score_behavior(first_obs, query_class)
    # A case passes only if its asserts pass AND behavior is ok (charter rule).
    passed = asserts_ok and behavior_ok
    return {
        "id": case.id,
        "category": case.category,
        "why": case.why,
        "query_class": query_class,
        "passed": passed,
        "behavior": behavior.value if behavior is not None else None,
        "behavior_ok": behavior_ok,
        "turns": turns_out,
    }
