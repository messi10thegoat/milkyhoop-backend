"""Bucket C runner — C1 + C2 + C3. Single login, reuses token across all turns."""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from conftest import BASE_URL, CREDENTIALS, LOGIN_URL  # noqa: E402

STREAM_URL = f"{BASE_URL}/api/v3/chat/message/stream"

# Single shared token — populated once at startup
_TOKEN: str = ""


async def login_once():
    global _TOKEN
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(LOGIN_URL, json=CREDENTIALS)
        r.raise_for_status()
        _TOKEN = r.json()["data"]["access_token"]


async def stream_with_token(text, conv_id, sess_id, timeout_s=60.0):
    headers = {"Authorization": f"Bearer {_TOKEN}", "Accept": "text/event-stream"}
    body = {"text": text, "conversation_id": conv_id, "session_id": sess_id}
    events = []
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        async with client.stream(
            "POST", STREAM_URL, json=body, headers=headers
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass
    return events


def extract_response_text(events):
    candidates = []
    for ev in events:
        if isinstance(ev, dict):
            d = (
                ev.get("data")
                if "data" in ev and isinstance(ev.get("data"), dict)
                else ev
            )
            for key in ("text", "response_text", "message", "content"):
                v = d.get(key) if isinstance(d, dict) else None
                if isinstance(v, str) and v.strip():
                    candidates.append(v)
    return candidates[-1][:400] if candidates else ""


def detect_message_type(events):
    for ev in events:
        if isinstance(ev, dict):
            d = (
                ev.get("data")
                if "data" in ev and isinstance(ev.get("data"), dict)
                else ev
            )
            mt = (
                d.get("message_type") or d.get("final_intent") or d.get("intent")
                if isinstance(d, dict)
                else None
            )
            if mt:
                return mt
    return ""


async def run_turn(conv_id, sess_id, text, label):
    t0 = time.time()
    try:
        events = await stream_with_token(text, conv_id, sess_id)
        return {
            "label": label,
            "text": text,
            "ok": True,
            "latency_ms": int((time.time() - t0) * 1000),
            "message_type": detect_message_type(events),
            "response_snippet": extract_response_text(events),
            "event_count": len(events),
        }
    except Exception as e:
        return {
            "label": label,
            "text": text,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }


async def run_stage(stage_name, turns):
    conv = str(uuid.uuid4())
    sess = conv
    results = []
    for label, text in turns:
        r = await run_turn(conv, sess, text, label)
        results.append(r)
        await asyncio.sleep(2.0)
    return {"stage": stage_name, "conv_id": conv, "session_id": sess, "turns": results}


async def main():
    await login_once()
    out = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    out["c1"] = await run_stage(
        "C1",
        [
            ("C1.1 ACTION_create", "buat faktur 10 kaos untuk Maju Jaya"),
            ("C1.2 ACTION_confirm", "betul"),
            ("C1.3 QUERY_pipeline", "piutang total"),
            ("C1.4 QUERY_calc", "rata-rata harga jual kaos"),
            ("C1.5 REC_followup", "siapa?"),
            ("C1.6 CRUD_delete", "hapus faktur terakhir"),
            ("C1.7 CHITCHAT", "selamat pagi"),
            ("C1.8 MFG", "daftar BOM"),
        ],
    )

    out["c2"] = await run_stage(
        "C2",
        [
            ("C2.1", "customer Maju Jaya hutangnya berapa?"),
            ("C2.2", "faktur terakhir dia"),
            ("C2.3", "kapan jatuh temponya?"),
            ("C2.4", "buat faktur baru untuk dia, kaos 5 pcs 75 ribu"),
            ("C2.5", "betul"),
            ("C2.6", "tadi saya buat berapa faktur?"),
            ("C2.7", "vendor PT Knitto, BPJS-nya berapa hutangnya?"),
            ("C2.8", "tagihan dari mereka berapa?"),
            ("C2.9", "yang paling besar"),
            ("C2.10", "siapa customer pertama yang muncul tadi?"),
            ("C2.11", "saldo BCA"),
            ("C2.12", "ringkas semua aktivitas saya hari ini"),
        ],
    )

    c3_turns = []
    for i in range(1, 6):
        c3_turns.append(
            (
                f"C3.{i}.preview",
                f"buat faktur untuk PT Sumber Rezeki, kemeja 10 pcs {1000*i} ribu",
            )
        )
        c3_turns.append((f"C3.{i}.confirm", "betul"))
    c3_turns.append(
        ("C3.6.probe", "buat faktur untuk PT Sumber Rezeki, kemeja 10 pcs 7000 ribu")
    )
    out["c3"] = await run_stage("C3", c3_turns)

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
