"""
Tier 2: Explicit User Preferences — KV store.

ONLY stores things user explicitly tells the bot.
Bot NEVER infers preferences.

Table: user_explicit_preferences (NOT user_preferences — that's Tier 1 auto-learn).

Categories: display_name, language_style, language_mix, output_format, interruption_tolerance.
Hard cap: 10 per (tenant, user). LRU eviction with unevictable guard.
"""

import json
import logging

logger = logging.getLogger("unified_agent.preference_manager")

HARD_CAP = 10
SOFT_WARN = 7
UNEVICTABLE_KEYS = {"display_name", "language_style", "language_mix"}
VALID_KEYS = {
    "display_name",
    "language_style",
    "language_mix",
    "output_format",
    "interruption_tolerance",
}
VALID_SOURCES = {"explicit_chat", "settings_ui", "system_default"}

CONFIRM_THRESHOLD_MAX_IDR = 500_000

LABEL_MAP = {
    "display_name": "Panggilan",
    "language_style": "Gaya bahasa",
    "language_mix": "Bahasa",
    "output_format": "Format output",
    "interruption_tolerance": "Toleransi interupsi",
}


class PreferenceManager:
    def __init__(self, db_pool, tenant_id: str, user_id: str):
        self.pool = db_pool
        self.tenant_id = tenant_id
        self.user_id = user_id

    async def _with_rls(self, callback):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", self.tenant_id
                )
                return await callback(conn)

    async def get_all_preferences(self) -> list:
        async def _query(conn):
            return await conn.fetch(
                """
                SELECT key, value, source, set_at, last_used_at, expires_at
                FROM user_explicit_preferences
                WHERE tenant_id = $1 AND user_id = $2
                ORDER BY set_at
                """,
                self.tenant_id,
                self.user_id,
            )

        rows = await self._with_rls(_query)
        return [
            {
                "key": r["key"],
                "value": json.loads(r["value"])
                if isinstance(r["value"], str)
                else r["value"],
                "source": r["source"],
                "set_at": r["set_at"].isoformat() if r["set_at"] else None,
                "last_used_at": r["last_used_at"].isoformat()
                if r["last_used_at"]
                else None,
            }
            for r in rows
        ]

    async def set_preference(
        self, key: str, value, source: str = "explicit_chat"
    ) -> dict:
        if key not in VALID_KEYS:
            return {"status": "invalid_key", "message": f"Key '{ key }' tidak dikenal."}
        if source not in VALID_SOURCES:
            return {"status": "invalid_source"}

        if key == "interruption_tolerance" and isinstance(value, dict):
            threshold = value.get("confirm_threshold_idr", 0)
            if threshold > CONFIRM_THRESHOLD_MAX_IDR:
                value["confirm_threshold_idr"] = CONFIRM_THRESHOLD_MAX_IDR

        value_json = (
            json.dumps(value) if not isinstance(value, str) else json.dumps(value)
        )

        async def _op(conn):
            existing = await conn.fetchrow(
                "SELECT key FROM user_explicit_preferences WHERE tenant_id=$1 AND user_id=$2 AND key=$3",
                self.tenant_id,
                self.user_id,
                key,
            )
            if not existing:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_explicit_preferences WHERE tenant_id=$1 AND user_id=$2",
                    self.tenant_id,
                    self.user_id,
                )
                if count >= HARD_CAP:
                    return {
                        "status": "capacity_full",
                        "message": f"Preferensi kamu sudah penuh ({HARD_CAP}). Mau hapus yang mana dulu?",
                    }

            await conn.execute(
                """
                INSERT INTO user_explicit_preferences (tenant_id, user_id, key, value, source, set_at, last_used_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, NOW(), NOW())
                ON CONFLICT (tenant_id, user_id, key) DO UPDATE SET
                    value = $4::jsonb, source = $5, set_at = NOW(), last_used_at = NOW()
                """,
                self.tenant_id,
                self.user_id,
                key,
                value_json,
                source,
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM user_explicit_preferences WHERE tenant_id=$1 AND user_id=$2",
                self.tenant_id,
                self.user_id,
            )
            return {
                "status": "ok",
                "warn_approaching_limit": count >= SOFT_WARN,
                "current_count": count,
            }

        result = await self._with_rls(_op)
        if result["status"] == "ok":
            logger.info(
                "[TIER2] Set preference: %s=%s for %s/%s",
                key,
                str(value)[:50],
                self.tenant_id,
                self.user_id,
            )
        return result

    async def delete_preference(self, key: str) -> dict:
        async def _op(conn):
            result = await conn.execute(
                "DELETE FROM user_explicit_preferences WHERE tenant_id=$1 AND user_id=$2 AND key=$3",
                self.tenant_id,
                self.user_id,
                key,
            )
            return "DELETE 1" in result

        deleted = await self._with_rls(_op)
        if deleted:
            logger.info(
                "[TIER2] Deleted preference: %s for %s/%s",
                key,
                self.tenant_id,
                self.user_id,
            )
        return {"status": "ok" if deleted else "not_found"}

    async def delete_all(self) -> dict:
        async def _op(conn):
            await conn.execute(
                "DELETE FROM user_explicit_preferences WHERE tenant_id=$1 AND user_id=$2",
                self.tenant_id,
                self.user_id,
            )

        await self._with_rls(_op)
        logger.info(
            "[TIER2] Deleted all preferences for %s/%s", self.tenant_id, self.user_id
        )
        return {"status": "ok"}

    async def get_eviction_candidates(self) -> list:
        async def _op(conn):
            return await conn.fetch(
                """
                SELECT key, value, last_used_at
                FROM user_explicit_preferences
                WHERE tenant_id = $1 AND user_id = $2
                  AND key NOT IN ('display_name', 'language_style', 'language_mix')
                ORDER BY last_used_at ASC NULLS FIRST
                """,
                self.tenant_id,
                self.user_id,
            )

        rows = await self._with_rls(_op)
        return [{"key": r["key"], "last_used_at": r["last_used_at"]} for r in rows]

    async def touch_last_used(self, keys: list[str]):
        if not keys:
            return

        async def _op(conn):
            await conn.execute(
                "UPDATE user_explicit_preferences SET last_used_at = NOW() WHERE tenant_id = $1 AND user_id = $2 AND key = ANY($3)",
                self.tenant_id,
                self.user_id,
                keys,
            )

        await self._with_rls(_op)

    async def get_preference_context(self) -> str:
        prefs = await self.get_all_preferences()
        if not prefs:
            return ""

        await self.touch_last_used([p["key"] for p in prefs])

        parts = ["## PREFERENSI EKSPLISIT"]
        for p in prefs:
            label = LABEL_MAP.get(p["key"], p["key"])
            val = p["value"]
            if isinstance(val, dict):
                val_str = ", ".join(
                    f"{k}: {v}" for k, v in val.items() if v is not None
                )
            else:
                val_str = str(val)
            parts.append(f"{label}: {val_str}")

        return "\n".join(parts) if len(parts) > 1 else ""
