"""
Phase 4C: Fire-and-forget telemetry recording.

Usage in unified_chat.py:
    from ..services.unified_agent.telemetry import record_telemetry
    asyncio.create_task(record_telemetry(db_pool, data))
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("unified_agent.telemetry")

# gpt-4o-mini pricing (per 1M tokens)
_PRICE_INPUT = 0.15 / 1_000_000   # $0.15 per 1M input
_PRICE_OUTPUT = 0.60 / 1_000_000  # $0.60 per 1M output
_PRICE_CACHED = 0.075 / 1_000_000 # $0.075 per 1M cached


def _estimate_cost(input_tokens: int, output_tokens: int, cached_tokens: int = 0,
                   classifier_in: int = 0, classifier_out: int = 0) -> float:
    """Estimate USD cost for a turn (agent + classifier)."""
    # Agent cost
    agent_input = max(0, input_tokens - cached_tokens)
    cost = agent_input * _PRICE_INPUT + cached_tokens * _PRICE_CACHED + output_tokens * _PRICE_OUTPUT
    # Classifier cost
    cost += classifier_in * _PRICE_INPUT + classifier_out * _PRICE_OUTPUT
    return round(cost, 6)


async def record_telemetry(
    db_pool,
    tenant_id: str,
    user_id: str = None,
    session_id: str = None,
    # Classifier
    intent: str = None,
    confidence: float = None,
    classifier_skipped: bool = False,
    classifier_tokens_in: int = 0,
    classifier_tokens_out: int = 0,
    classifier_latency_ms: int = 0,
    low_confidence_fallback: bool = False,
    workflow_type: str = None,
    # Agent
    tools_loaded: int = 0,
    tools_called: int = 0,
    iteration_count: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    total_latency_ms: int = 0,
    message_type: str = None,
    model_used: str = None,
):
    """Fire-and-forget: insert telemetry row. Errors are logged, never raised."""
    if not db_pool:
        return

    try:
        cost = _estimate_cost(
            input_tokens, output_tokens, cached_tokens,
            classifier_tokens_in, classifier_tokens_out,
        )

        await db_pool.execute(
            """INSERT INTO chat_telemetry (
                tenant_id, user_id, session_id,
                intent, confidence, classifier_skipped,
                classifier_tokens_in, classifier_tokens_out, classifier_latency_ms,
                low_confidence_fallback, workflow_type,
                tools_loaded, tools_called, iteration_count,
                input_tokens, output_tokens, cached_tokens,
                estimated_cost_usd, total_latency_ms,
                message_type, model_used
            ) VALUES (
                $1, $2, $3::uuid,
                $4, $5, $6,
                $7, $8, $9,
                $10, $11,
                $12, $13, $14,
                $15, $16, $17,
                $18, $19,
                $20, $21
            )""",
            tenant_id, user_id, session_id,
            intent, confidence, classifier_skipped,
            classifier_tokens_in, classifier_tokens_out, classifier_latency_ms,
            low_confidence_fallback, workflow_type,
            tools_loaded, tools_called, iteration_count,
            input_tokens, output_tokens, cached_tokens,
            cost, total_latency_ms,
            message_type, model_used,
        )

        logger.info(
            "[TELEMETRY] intent=%s conf=%.2f cost=$%.4f tokens=%d+%d lat=%dms",
            intent, confidence or 0, cost,
            input_tokens, output_tokens, total_latency_ms,
        )
    except Exception as e:
        logger.warning("[TELEMETRY] Record failed (non-fatal): %s", e)


# ═══════════════════════════════════════════════════════════════════
# Intent Decision Telemetry — Classification observability (v1.0)
# ═══════════════════════════════════════════════════════════════════

import json as _json

MODEL_COST_PER_1K = {
    "gemini-2.5-flash-lite": (0.000075, 0.0003),
    "gpt-4o-mini-2024-07-18": (0.00015, 0.0006),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "calc_engine": (0, 0),
    "pipeline": (0, 0),
}


def estimate_cost(model: str, input_tokens: int = 0, output_tokens: int = 0) -> float:
    rates = MODEL_COST_PER_1K.get(model, (0.00015, 0.0006))
    return round((input_tokens * rates[0] + output_tokens * rates[1]) / 1000, 6)


class IntentTelemetry:
    """Async, non-blocking intent decision logger."""

    def __init__(self, db_pool, tenant_id: str):
        self.pool = db_pool
        self.tenant_id = tenant_id

    async def log_decision(self, **kw):
        try:
            await self.pool.execute(
                """
                INSERT INTO intent_decision_log (
                    tenant_id, conversation_id, session_id,
                    user_text, user_text_length,
                    gemini_intent, gemini_confidence, gemini_entities, gemini_latency_ms,
                    guard_triggered, guard_from, guard_to,
                    guard_conflict, guard_conflict_detail,
                    final_intent, final_confidence, decision_source,
                    context_hint_used, last_action_type,
                    pipeline_or_agent, model_used, total_latency_ms,
                    estimated_cost_usd, input_tokens, output_tokens,
                    response_type, response_length
                ) VALUES (
                    $1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13, $14, $15, $16, $17, $18, $19,
                    $20, $21, $22, $23, $24, $25, $26, $27
                )
                """,
                self.tenant_id,
                kw.get("conversation_id"),
                kw.get("session_id"),
                (kw.get("user_text") or "")[:500],
                len(kw.get("user_text") or ""),
                kw.get("gemini_intent"),
                kw.get("gemini_confidence", 0.0),
                _json.dumps(kw.get("gemini_entities") or {}, default=str),
                kw.get("gemini_latency_ms", 0),
                kw.get("guard_triggered", "none"),
                kw.get("guard_from"),
                kw.get("guard_to"),
                kw.get("guard_conflict", False),
                _json.dumps(kw.get("guard_conflict_detail") or {}) if kw.get("guard_conflict") else None,
                kw.get("final_intent", "unknown"),
                kw.get("final_confidence", 0.0),
                kw.get("decision_source", "unknown"),
                kw.get("context_hint_used", False),
                kw.get("last_action_type"),
                kw.get("pipeline_or_agent", "unknown"),
                kw.get("model_used", "unknown"),
                kw.get("total_latency_ms", 0),
                kw.get("estimated_cost_usd", 0.0),
                kw.get("input_tokens", 0),
                kw.get("output_tokens", 0),
                kw.get("response_type", "TEXT"),
                kw.get("response_length", 0),
            )
        except Exception as e:
            logger.warning("[TELEMETRY] intent log failed (non-fatal): %s", e)

    async def record_feedback(self, session_id: str, feedback: int):
        try:
            await self.pool.execute(
                """
                UPDATE intent_decision_log
                SET user_feedback = $1, feedback_ts = NOW()
                WHERE id = (
                    SELECT id FROM intent_decision_log
                    WHERE session_id = $2::uuid AND tenant_id = $3
                    ORDER BY ts DESC LIMIT 1
                )
                """,
                feedback, session_id, self.tenant_id,
            )
        except Exception as e:
            logger.warning("[TELEMETRY] feedback failed (non-fatal): %s", e)

    async def detect_correction(self, session_id: str):
        try:
            await self.pool.execute(
                """
                UPDATE intent_decision_log
                SET is_correction = TRUE
                WHERE id = (
                    SELECT id FROM intent_decision_log
                    WHERE session_id = $1::uuid AND tenant_id = $2
                      AND ts > NOW() - INTERVAL '30 seconds'
                    ORDER BY ts DESC LIMIT 1
                )
                """,
                session_id, self.tenant_id,
            )
        except Exception as e:
            logger.warning("[TELEMETRY] correction detect failed (non-fatal): %s", e)
