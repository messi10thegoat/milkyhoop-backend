"""
Tier 3: Structured Session Summary Generator.

Generates YAML-structured summaries on session close/idle.
Supports gap-based loading and pin state machine.

Iron Law 3.1: NO financial amounts in summaries.
"""

import json
import logging
import re
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("unified_agent.summary_generator")

VALID_OUTCOMES = ("posted", "draft_unfinished", "abandoned", "query_only")
VALID_PIN_REASONS = (
    "unfinished_action",
    "user_explicit",
    "error_state",
    "deferred_task",
)
MIN_MESSAGES_FOR_SUMMARY = 6
PIN_EXPIRY_DAYS = 90

GAP_FULL_SUMMARY_HOURS = 24
GAP_HEADER_ONLY_DAYS = 7


def _extract_result_text(result) -> str:
    """Extract text from chat_events.result which is JSONB."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("summary", "message", "status", "result"):
            if key in result:
                return str(result[key])
        return json.dumps(result)
    return str(result)


async def generate_structured_summary(messages: list, events: list) -> dict:
    summary = {
        "topic": "",
        "outcome": "query_only",
        "entities": {
            "customers": [],
            "vendors": [],
            "items": [],
            "warehouses": [],
        },
        "key_decisions": [],
        "unfinished": None,
    }

    action_types_seen = set()
    last_action_status = None
    for event in events:
        action_type = event.get("action_type", "")
        event_type = event.get("event_type", "")
        result = str(event.get("result_summary", "")).lower()

        if action_type:
            action_types_seen.add(action_type)

        if event_type == "confirm" and "posted" in result:
            last_action_status = "posted"
        elif event_type == "propose" and last_action_status is None:
            last_action_status = "proposed"
        elif event_type in ("reject", "cancel"):
            last_action_status = "abandoned"

    if last_action_status == "posted":
        summary["outcome"] = "posted"
    elif last_action_status == "proposed":
        summary["outcome"] = "draft_unfinished"
    elif last_action_status == "abandoned":
        summary["outcome"] = "abandoned"
    else:
        summary["outcome"] = "query_only"

    user_messages = [m for m in messages if m.get("role") == "user"]
    if user_messages:
        first_msg = user_messages[0].get("content", "")[:100]
        summary["topic"] = first_msg.strip()

    if action_types_seen:
        action_labels = {
            "CREATE_SALES_INVOICE": "faktur penjualan",
            "CREATE_BILL": "faktur pembelian",
            "CREATE_QUOTE": "penawaran",
            "CREATE_EXPENSE": "biaya",
            "CREATE_RECEIVE_PAYMENT": "pembayaran masuk",
            "CREATE_BILL_PAYMENT": "pembayaran keluar",
            "CREATE_CUSTOMER": "pelanggan baru",
            "CREATE_VENDOR": "vendor baru",
        }
        labels = [action_labels.get(a, a) for a in action_types_seen]
        if labels:
            summary["topic"] = ", ".join(labels[:2])

    if summary["outcome"] == "draft_unfinished":
        summary["unfinished"] = f"Draft {summary['topic']} belum posted"

    summary["topic"] = re.sub(r"Rp\.?\s*[\d.,]+", "", summary["topic"]).strip()

    return summary


async def get_last_session_context(
    pool, tenant_id: str, user_id: str, now: datetime
) -> str:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )

            row = await conn.fetchrow(
                """
                SELECT id, final_summary, created_at, updated_at, is_pinned, pin_reason,
                       (SELECT MAX(created_at) FROM chat_messages WHERE session_id = cs.id) as last_message_at
                FROM chat_sessions cs
                WHERE tenant_id = $1 AND final_summary IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 1
            """,
                tenant_id,
            )

    if not row:
        return ""

    last_msg_at = row["last_message_at"] or row["updated_at"] or row["created_at"]
    gap = now - last_msg_at

    summary_data = row["final_summary"]
    if isinstance(summary_data, str):
        try:
            summary_data = json.loads(summary_data)
        except json.JSONDecodeError:
            return ""

    topic = summary_data.get("topic", "")
    outcome = summary_data.get("outcome", "")
    unfinished = summary_data.get("unfinished")

    if row["is_pinned"] and unfinished:
        return f"## SESI TERTUNDA\n\u26a0 {unfinished} \u2014 lanjut atau topik lain?"

    if gap < timedelta(hours=GAP_FULL_SUMMARY_HOURS):
        parts = [f"## SESI TERAKHIR ({_format_gap(gap)} lalu)"]
        parts.append(f"Topik: {topic}")
        if outcome and outcome != "query_only":
            parts.append(f"Status: {outcome}")
        entities = summary_data.get("entities", {})
        entity_names = []
        for category in ["customers", "vendors", "items"]:
            entity_names.extend(entities.get(category, []))
        if entity_names:
            parts.append(f"Entities: {', '.join(entity_names[:5])}")
        decisions = summary_data.get("key_decisions", [])
        if decisions:
            parts.append(f"Keputusan: {', '.join(decisions[:3])}")
        if unfinished:
            parts.append(f"\u26a0 {unfinished}")
        return "\n".join(parts)

    elif gap < timedelta(days=GAP_HEADER_ONLY_DAYS):
        days_ago = gap.days
        return f"## SESI TERAKHIR\nSesi {days_ago} hari lalu, topik: {topic}"

    else:
        return ""


async def save_final_summary(pool, session_id: str, tenant_id: str, summary: dict):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await conn.execute(
                "UPDATE chat_sessions SET final_summary = $1 WHERE id = $2::uuid",
                json.dumps(summary),
                session_id,
            )
    logger.info("[TIER3] Saved final summary for session %s", session_id[:8])


async def pin_session(pool, session_id: str, tenant_id: str, reason: str):
    if reason not in VALID_PIN_REASONS:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await conn.execute(
                """
                UPDATE chat_sessions
                SET is_pinned = TRUE, pin_reason = $1, pinned_at = NOW()
                WHERE id = $2::uuid
                """,
                reason,
                session_id,
            )
    logger.info("[TIER3] Pinned session %s: %s", session_id[:8], reason)


async def unpin_session(pool, session_id: str, tenant_id: str):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await conn.execute(
                "UPDATE chat_sessions SET is_pinned = FALSE, pin_reason = NULL, pinned_at = NULL WHERE id = $1::uuid",
                session_id,
            )
    logger.info("[TIER3] Unpinned session %s", session_id[:8])


async def auto_unpin_expired(pool, tenant_id: str):
    cutoff = datetime.now(timezone.utc) - timedelta(days=PIN_EXPIRY_DAYS)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            rows = await conn.fetch(
                """
                UPDATE chat_sessions
                SET is_pinned = FALSE, pin_reason = NULL
                WHERE is_pinned = TRUE AND pinned_at < $1 AND tenant_id = $2
                RETURNING id
                """,
                cutoff,
                tenant_id,
            )
    if rows:
        logger.info(
            "[TIER3] Auto-unpinned %d expired sessions for %s", len(rows), tenant_id
        )
    return [str(r["id"]) for r in rows]


def _format_gap(gap: timedelta) -> str:
    hours = int(gap.total_seconds() / 3600)
    if hours < 1:
        return f"{int(gap.total_seconds() / 60)} menit"
    elif hours < 24:
        return f"{hours} jam"
    else:
        return f"{gap.days} hari"


async def summary_poller_tick(pool):
    try:
        rows = await pool.fetch(
            """
            SELECT cs.id, cs.tenant_id, cs.user_id::text
            FROM chat_sessions cs
            WHERE cs.final_summary IS NULL
              AND cs.message_count >= $1
              AND cs.status = 'active'
              AND (SELECT MAX(created_at) FROM chat_messages WHERE session_id = cs.id)
                  < NOW() - INTERVAL '30 minutes'
            ORDER BY cs.updated_at DESC
            LIMIT 10
        """,
            MIN_MESSAGES_FOR_SUMMARY,
        )

        if not rows:
            return 0

        generated = 0
        for row in rows:
            try:
                session_id = str(row["id"])
                tenant_id = row["tenant_id"]

                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                        )

                        messages = await conn.fetch(
                            "SELECT role, content FROM chat_messages WHERE session_id = $1::uuid ORDER BY created_at",
                            session_id,
                        )
                        events = await conn.fetch(
                            "SELECT event_type, action_type, result FROM chat_events WHERE session_id = $1::uuid ORDER BY created_at",
                            session_id,
                        )

                msgs = [
                    {"role": r["role"], "content": r["content"] or ""} for r in messages
                ]
                evts = [
                    {
                        "event_type": r["event_type"],
                        "action_type": r["action_type"] or "",
                        "result_summary": _extract_result_text(r["result"]),
                    }
                    for r in events
                ]

                summary = await generate_structured_summary(msgs, evts)
                await save_final_summary(pool, session_id, tenant_id, summary)

                if summary.get("unfinished"):
                    await pin_session(pool, session_id, tenant_id, "unfinished_action")

                generated += 1
            except Exception as e:
                logger.warning(
                    "[TIER3] Summary gen failed for %s: %s", session_id[:8], e
                )

        if generated:
            logger.info("[TIER3] Poller generated %d summaries", generated)
        return generated

    except Exception as e:
        logger.warning("[TIER3] Poller tick error: %s", e)
        return 0


async def summary_poller_loop(pool):
    import asyncio

    logger.info("[TIER3] Summary poller started")
    while True:
        await summary_poller_tick(pool)
        await asyncio.sleep(300)
