"""Dogfood capture — PASSIVE triage harvest (NOT a scorer).

Reads REAL Grapgrap chat turns produced during a live dogfood session on the
actual milkyhoop.com app, and emits a human-taggable triage list. There are no
expected labels and NO scoring here: dogfood is what *discovers* the expected
labels. YOUR per-turn judgment (tag each good / wrong / weird) is the signal.

CAVEAT (honest): the `intent` shown is the CLASSIFICATION-stage intent from
intent_decision_log. It can diverge from the intent that actually executed
(the executed intent lives in the response tool_calls, which is NOT persisted
to chat_messages). A classification-vs-response mismatch is itself a flag worth
tagging. For the final verdict, read the response text and use your judgment.

Passive by design: it does NOT send anything to the bot. You dogfood in the
real web/mobile app as the Grapgrap owner; this only taps the wire afterward.

Usage (on the server, from .../tests/chat):
  python3 -m goldset.dogfood_capture --since "2026-06-05 08:00:00"   # UTC
  python3 -m goldset.dogfood_capture --minutes 90
Outputs: a markdown triage list to stdout + JSONL (empty `tag` field to fill)
at /tmp/dogfood_capture.jsonl
"""
import argparse
import json
import subprocess

from goldset.tiers import derive_tier

TENANT = "grapgrap"
SEP = "|~SEP~|"  # field separator unlikely to appear in data


def _psql(sql):
    pg = (
        "PGPASSWORD=Proyek771977 psql -U postgres -d milkydb "  # pragma: allowlist secret
        f"-t -A -F '{SEP}' -c \"{sql}\""
    )
    cmd = ["docker", "exec", "milkyhoop-dev-postgres-1", "sh", "-c", pg]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return out.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--since", help="UTC 'YYYY-MM-DD HH:MM:SS' (start of dogfood session)"
    )
    ap.add_argument(
        "--minutes", type=int, default=120, help="lookback window if --since omitted"
    )
    a = ap.parse_args()
    where_ts = (
        f"l.ts > '{a.since}'"
        if a.since
        else f"l.ts > now() - interval '{a.minutes} minutes'"
    )
    # Collapse newlines in text fields so each turn is exactly one output row.
    sql = (
        "SELECT l.ts, "
        "regexp_replace(coalesce(l.user_text,''), E'\\n', ' / ', 'g'), "
        "coalesce(l.final_intent,''), coalesce(l.model_used,''), "
        "coalesce(l.total_latency_ms,0), "
        "coalesce(l.decision_source,''), "
        "regexp_replace(coalesce("
        "  (SELECT m.content FROM chat_messages m "
        "   WHERE m.session_id = l.session_id AND m.role = 'assistant' "
        "   AND m.created_at >= l.ts ORDER BY m.created_at ASC LIMIT 1), ''), "
        "  E'\\n', ' ⏎ ', 'g') "
        f"FROM intent_decision_log l WHERE l.tenant_id = '{TENANT}' AND {where_ts} "
        "ORDER BY l.ts ASC"
    )
    rows = [r for r in _psql(sql).split("\n") if r.strip()]
    recs = []
    for r in rows:
        p = r.split(SEP)
        if len(p) < 7:
            continue
        ts, text, intent, model, lat, src, resp = p[:7]
        tier = derive_tier(intent, model, "TEXT", 1)
        recs.append(
            {
                "ts": ts,
                "text": text,
                "intent_classified": intent,
                "decision_source": src,
                "tier": getattr(tier, "value", str(tier)),
                "model": model,
                "latency_ms": lat,
                "response": resp,
                "tag": "",  # <- fill: good | wrong | weird | overclarify | ...
                "note": "",  # <- fill: what's wrong / expected intent-tier-behavior-trace
            }
        )
    with open("/tmp/dogfood_capture.jsonl", "w") as f:
        for rec in recs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"# Dogfood capture — {len(recs)} turns (tenant={TENANT})")
    print(
        "# intent shown = CLASSIFICATION stage (may differ from executed). Tag by judgment.\n"
    )
    for i, rec in enumerate(recs, 1):
        print(
            f"## {i}. [{rec['tier']}] {rec['intent_classified']} "
            f"({rec['decision_source']}, {rec['latency_ms']}ms)   tag:____  note:____"
        )
        print(f"**Q:** {rec['text']}")
        print(f"**A:** {rec['response'][:400]}")
        print()
    print("\nFull JSONL (empty tag/note fields to fill): /tmp/dogfood_capture.jsonl")


if __name__ == "__main__":
    main()
