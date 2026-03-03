"""
Observability correlation IDs for turn-level and tool-call-level tracing.
"""
import uuid
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolCallContext:
    """Context for a single tool call within a turn."""
    tool_call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    retry_attempt: int = 0
    status: str = "pending"  # pending | success | failed | retried
    latency_ms: int = 0
    error_type: Optional[str] = None
    idempotency_key: Optional[str] = None
    started_at: float = field(default_factory=time.time)

    def complete(self, status: str, error_type: Optional[str] = None):
        self.status = status
        self.error_type = error_type
        self.latency_ms = int((time.time() - self.started_at) * 1000)


@dataclass
class TurnContext:
    """Correlation context for a single turn (one user message -> response cycle)."""
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    model_id_used: str = ""
    fallback_used: bool = False
    fsm_state_before: str = ""
    fsm_state_after: str = ""
    tool_calls: list = field(default_factory=list)

    def new_tool_call(self, tool_name: str, retry_attempt: int = 0, idempotency_key: Optional[str] = None) -> ToolCallContext:
        ctx = ToolCallContext(
            tool_name=tool_name,
            retry_attempt=retry_attempt,
            idempotency_key=idempotency_key,
        )
        self.tool_calls.append(ctx)
        return ctx

    def to_log_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "model_id_used": self.model_id_used,
            "fallback_used": self.fallback_used,
            "fsm_state_before": self.fsm_state_before,
            "fsm_state_after": self.fsm_state_after,
            "tool_call_count": len(self.tool_calls),
        }
