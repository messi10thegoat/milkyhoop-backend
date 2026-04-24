"""
Action Memory — Pattern learning from confirmed actions.

Learns user repeat behaviors. Enables: "seperti biasanya?", auto-suggest items/tax.

RULES:
- Pattern match on STRUCTURE (customer + item combo), IGNORE variable fields (qty, amount)
- qty and price stored as LAST USED values (default suggestion)
- Suggest only if usage_count >= 3 AND confidence >= 0.7
- NEVER store saldo, outstanding, or financial calculations (Law 1)
- Updated ONLY by backend hooks (after_confirm), NEVER by LLM (Law 10)
"""

import json
import re
import logging
from datetime import datetime, timezone

logger = logging.getLogger("unified_agent.action_memory")

MIN_USAGE_TO_SUGGEST = 5
MIN_CONFIDENCE_TO_SUGGEST = 0.70
CONFIDENCE_INCREMENT = 0.10
CONFIDENCE_MAX = 0.95
DECAY_DAYS = 30
DECAY_RATE = 0.05

PATTERN_INTENTS = {
    "create_sales_invoice",
    "create_bill",
    "create_expense",
    "create_receive_payment",
    "create_bill_payment",
}


def build_structure_key(intent, payload):
    if intent not in PATTERN_INTENTS:
        return None
    parts = [intent]
    if payload.get("customer_id"):
        parts.append("customer:%s" % payload["customer_id"])
    elif payload.get("vendor_id"):
        parts.append("vendor:%s" % payload["vendor_id"])
    else:
        return None
    items = payload.get("items", [])
    if items and isinstance(items, list):
        item_names = []
        for item in items:
            name = (
                item.get("description")
                or item.get("product_name")
                or item.get("name")
                or item.get("item_id", "")
            )
            normalized = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
            if normalized:
                item_names.append(normalized)
        if item_names:
            item_names.sort()
            parts.append(":".join(item_names))
    if intent == "create_expense" and payload.get("account_id"):
        parts.append("account:%s" % payload["account_id"])
    return "|".join(parts)


def extract_pattern_data(intent, payload):
    if intent not in PATTERN_INTENTS:
        return None
    pattern = {}
    if payload.get("customer_id"):
        pattern["customer_id"] = payload["customer_id"]
        pattern["customer_name"] = payload.get("customer_name", "")
    if payload.get("vendor_id"):
        pattern["vendor_id"] = payload["vendor_id"]
        pattern["vendor_name"] = payload.get("vendor_name", "")
    items = payload.get("items", [])
    if items and isinstance(items, list):
        pattern_items = []
        for item in items:
            pi = {
                "item_id": item.get("item_id") or item.get("product_id", ""),
                "name": item.get("description")
                or item.get("product_name")
                or item.get("name", ""),
            }
            qty = item.get("quantity") or item.get("qty")
            if qty is not None:
                pi["last_qty"] = float(qty)
            price = item.get("unit_price") or item.get("price")
            if price is not None:
                pi["last_price"] = float(price)
            pattern_items.append(pi)
        pattern["items"] = pattern_items
    if payload.get("tax_rate") is not None:
        pattern["tax_rate"] = float(payload["tax_rate"])
    if intent == "create_expense":
        if payload.get("account_id"):
            pattern["account_id"] = payload["account_id"]
            pattern["account_name"] = payload.get("account_name", "")
        if payload.get("paid_through_id"):
            pattern["paid_through_id"] = payload["paid_through_id"]
            pattern["paid_through_name"] = payload.get("paid_through_name", "")
    if payload.get("bank_account_id"):
        pattern["bank_account_id"] = payload["bank_account_id"]
        pattern["bank_account_name"] = payload.get("bank_account_name", "")
    return pattern if pattern else None


class ActionMemory:
    def __init__(self, db_pool, tenant_id, user_id):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.user_id = user_id

    async def record_pattern(self, intent, payload):
        structure_key = build_structure_key(intent, payload)
        if not structure_key:
            return
        pattern_data = extract_pattern_data(intent, payload)
        if not pattern_data:
            return
        try:
            await self.db.execute(
                """
                INSERT INTO action_patterns (tenant_id, user_id, intent, structure_key, pattern, usage_count, confidence)
                VALUES ($1, $2, $3, $4, $5, 1, 0.30)
                ON CONFLICT (tenant_id, user_id, structure_key)
                DO UPDATE SET
                    pattern = $5,
                    usage_count = action_patterns.usage_count + 1,
                    confidence = LEAST(action_patterns.confidence + $6, $7),
                    last_used_at = now(),
                    updated_at = now()
            """,
                self.tenant_id,
                self.user_id,
                intent,
                structure_key,
                json.dumps(pattern_data),
                CONFIDENCE_INCREMENT,
                CONFIDENCE_MAX,
            )
            logger.info(
                "[ACTION_MEMORY] Recorded: intent=%s key=%s", intent, structure_key[:60]
            )
        except Exception as e:
            logger.warning(
                "[ACTION_MEMORY] Failed to record: intent=%s structure_key=%s err=%s",
                intent,
                structure_key[:60] if structure_key else None,
                e,
                exc_info=True,
            )

    async def suggest_pattern(self, intent, payload):
        if intent not in PATTERN_INTENTS:
            return None
        full_key = build_structure_key(intent, payload)
        partial_parts = [intent]
        if payload.get("customer_id"):
            partial_parts.append("customer:%s" % payload["customer_id"])
        elif payload.get("vendor_id"):
            partial_parts.append("vendor:%s" % payload["vendor_id"])
        else:
            return None
        partial_key = "|".join(partial_parts)
        try:
            row = None
            if full_key:
                row = await self.db.fetchrow(
                    """
                    SELECT pattern, confidence, usage_count, last_used_at
                    FROM action_patterns
                    WHERE tenant_id = $1 AND user_id = $2 AND structure_key = $3
                """,
                    self.tenant_id,
                    self.user_id,
                    full_key,
                )
            if not row:
                row = await self.db.fetchrow(
                    """
                    SELECT pattern, confidence, usage_count, last_used_at
                    FROM action_patterns
                    WHERE tenant_id = $1 AND user_id = $2
                      AND intent = $3
                      AND structure_key LIKE $4
                    ORDER BY confidence DESC, usage_count DESC
                    LIMIT 1
                """,
                    self.tenant_id,
                    self.user_id,
                    intent,
                    partial_key + "%",
                )
            if not row:
                return None
            confidence = float(row["confidence"])
            last_used = row["last_used_at"]
            if last_used:
                days_since = (datetime.now(timezone.utc) - last_used).days
                if days_since > DECAY_DAYS:
                    decay_periods = (days_since - DECAY_DAYS) / DECAY_DAYS
                    confidence -= DECAY_RATE * decay_periods
                    confidence = max(confidence, 0.10)
            if (
                row["usage_count"] < MIN_USAGE_TO_SUGGEST
                or confidence < MIN_CONFIDENCE_TO_SUGGEST
            ):
                return None
            pattern = (
                json.loads(row["pattern"])
                if isinstance(row["pattern"], str)
                else row["pattern"]
            )
            logger.info(
                "[ACTION_MEMORY] Pattern found: intent=%s confidence=%.2f usage=%d",
                intent,
                confidence,
                row["usage_count"],
            )
            return {
                "pattern": pattern,
                "confidence": round(confidence, 2),
                "usage_count": row["usage_count"],
            }
        except Exception as e:
            logger.warning(
                "[ACTION_MEMORY] suggest_pattern failed: intent=%s err=%s",
                intent,
                e,
                exc_info=True,
            )
            return None

    async def get_suggestion_text(self, intent, payload):
        result = await self.suggest_pattern(intent, payload)
        if not result:
            return None
        pattern = result["pattern"]
        parts = []
        items = pattern.get("items", [])
        for item in items[:3]:
            name = item.get("name", "?")
            qty = item.get("last_qty")
            price = item.get("last_price")
            item_str = name
            if qty:
                item_str += " %d pcs" % int(qty)
            if price:
                item_str += " @ Rp %s" % "{:,}".format(int(price)).replace(",", ".")
            parts.append(item_str)
        if pattern.get("tax_rate"):
            parts.append("PPN %d%%" % int(pattern["tax_rate"]))
        if not parts:
            return None
        return "Seperti biasanya? " + ", ".join(parts)

    async def record_rejection(self, intent: str, structure_key: str):
        """Decrement confidence on rejection. 3x consecutive → pause 30 days."""
        try:
            await self.db.execute(
                """
                UPDATE action_patterns
                SET confidence = GREATEST(confidence - $1, 0.10),
                    updated_at = now()
                WHERE tenant_id = $2 AND user_id = $3 AND structure_key = $4
            """,
                CONFIDENCE_INCREMENT,
                self.tenant_id,
                self.user_id,
                structure_key,
            )

            row = await self.db.fetchrow(
                """
                SELECT confidence, usage_count FROM action_patterns
                WHERE tenant_id = $1 AND user_id = $2 AND structure_key = $3
            """,
                self.tenant_id,
                self.user_id,
                structure_key,
            )

            if row and float(row["confidence"]) <= 0.30:
                await self.db.execute(
                    """
                    UPDATE action_patterns
                    SET last_used_at = now() - interval '30 days',
                        confidence = 0.10
                    WHERE tenant_id = $1 AND user_id = $2 AND structure_key = $3
                """,
                    self.tenant_id,
                    self.user_id,
                    structure_key,
                )
                logger.info(
                    "[ACTION_MEMORY] Paused pattern (3x reject): %s", structure_key[:60]
                )
        except Exception as e:
            logger.warning(
                "[ACTION_MEMORY] record_rejection failed: intent=%s structure_key=%s err=%s",
                intent,
                structure_key[:60] if structure_key else None,
                e,
                exc_info=True,
            )

    async def get_top_patterns_for_intent(self, intent: str, limit: int = 3) -> list:
        """Get top patterns for display in suggestion (confidence-ordered)."""
        if intent not in PATTERN_INTENTS:
            return []
        try:
            rows = await self.db.fetch(
                """
                SELECT pattern, confidence, usage_count, structure_key
                FROM action_patterns
                WHERE tenant_id = $1 AND user_id = $2 AND intent = $3
                  AND usage_count >= $4 AND confidence >= $5
                ORDER BY confidence DESC, usage_count DESC
                LIMIT $6
            """,
                self.tenant_id,
                self.user_id,
                intent,
                MIN_USAGE_TO_SUGGEST,
                MIN_CONFIDENCE_TO_SUGGEST,
                limit,
            )

            results = []
            for r in rows:
                pattern = (
                    json.loads(r["pattern"])
                    if isinstance(r["pattern"], str)
                    else r["pattern"]
                )
                entity_name = (
                    pattern.get("customer_name") or pattern.get("vendor_name") or "?"
                )
                results.append(
                    {
                        "entity_name": entity_name,
                        "usage_count": r["usage_count"],
                        "confidence": float(r["confidence"]),
                        "structure_key": r["structure_key"],
                        "pattern": pattern,
                    }
                )
            return results
        except Exception as e:
            logger.warning(
                "[ACTION_MEMORY] get_top_patterns failed: intent=%s err=%s",
                intent,
                e,
                exc_info=True,
            )
            return []
