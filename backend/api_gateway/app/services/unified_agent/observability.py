"""
Persistence layer for observability data -- writes to turn_metrics and tool_call_logs.
"""
import logging
from typing import Optional
from .correlation import TurnContext, ToolCallContext

logger = logging.getLogger(__name__)


async def persist_turn_metrics(db_pool, tenant_id: str, session_id: str, turn_ctx: TurnContext, extra: Optional[dict] = None):
    """Update turn_metrics row with correlation fields."""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE turn_metrics 
                SET turn_id = $1, fallback_used = $2, idempotency_key = $3,
                    fsm_state_before = $4, fsm_state_after = $5
                WHERE session_id = $6 AND tenant_id = $7
                ORDER BY created_at DESC LIMIT 1
            """, turn_ctx.turn_id, turn_ctx.fallback_used,
                extra.get("idempotency_key") if extra else None,
                turn_ctx.fsm_state_before, turn_ctx.fsm_state_after,
                session_id, tenant_id)
    except Exception as e:
        logger.warning(f"[observability] Failed to persist turn_metrics: {e}")


async def persist_tool_call_log(db_pool, turn_ctx: TurnContext, tool_ctx: ToolCallContext):
    """Insert a tool_call_logs row."""
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO tool_call_logs (turn_id, tool_call_id, tool_name, retry_attempt, status, latency_ms, error_type, idempotency_key)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, turn_ctx.turn_id, tool_ctx.tool_call_id, tool_ctx.tool_name,
                tool_ctx.retry_attempt, tool_ctx.status, tool_ctx.latency_ms,
                tool_ctx.error_type, tool_ctx.idempotency_key)
    except Exception as e:
        logger.warning(f"[observability] Failed to persist tool_call_log: {e}")


async def persist_all_tool_calls(db_pool, turn_ctx: TurnContext):
    """Batch persist all tool calls from a turn."""
    for tc in turn_ctx.tool_calls:
        await persist_tool_call_log(db_pool, turn_ctx, tc)
