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
