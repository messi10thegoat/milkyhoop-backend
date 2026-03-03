"""
Retry Controller for MilkyHoop tool execution.

Deterministic retry decisions based on tool metadata flags:
  - Idempotent tools: auto-retry with exponential backoff
  - Non-idempotent tools: verify-first (read-after-write) before retry
  - Non-retryable errors (400, 401, 409, DB constraint): immediate abort

Consumed by: ToolExecutor in the agent loop.
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Awaitable, Any

from .tool_metadata import get_tool_metadata, get_action_metadata
from .circuit_breaker import circuit_breaker, CircuitBreaker

logger = logging.getLogger("unified_agent.retry_controller")


class ErrorCategory(str, Enum):
    RETRYABLE_NETWORK = "RETRYABLE_NETWORK"
    RETRYABLE_SERVER = "RETRYABLE_SERVER"
    NON_RETRYABLE_CLIENT = "NON_RETRYABLE_CLIENT"
    NON_RETRYABLE_AUTH = "NON_RETRYABLE_AUTH"
    NON_RETRYABLE_CONFLICT = "NON_RETRYABLE_CONFLICT"
    NON_RETRYABLE_NOT_FOUND = "NON_RETRYABLE_NOT_FOUND"
    NON_RETRYABLE_DB = "NON_RETRYABLE_DB"
    NON_RETRYABLE_VALIDATION = "NON_RETRYABLE_VALIDATION"


NON_RETRYABLE_CATEGORIES = {
    ErrorCategory.NON_RETRYABLE_CLIENT,
    ErrorCategory.NON_RETRYABLE_AUTH,
    ErrorCategory.NON_RETRYABLE_CONFLICT,
    ErrorCategory.NON_RETRYABLE_NOT_FOUND,
    ErrorCategory.NON_RETRYABLE_DB,
    ErrorCategory.NON_RETRYABLE_VALIDATION,
}


@dataclass
class RetryDecision:
    should_retry: bool
    wait_ms: int = 0
    reason: str = ""
    verify_first: bool = False
    use_same_idempotency_key: bool = True


class RetryController:
    """Deterministic retry controller. Decisions based on metadata, not LLM."""

    @staticmethod
    def classify_error(
        error: Optional[Exception] = None,
        status_code: Optional[int] = None,
        error_type: Optional[str] = None,
    ) -> ErrorCategory:
        """Classify error into retry category."""
        if error_type:
            mapping = {
                "timeout": ErrorCategory.RETRYABLE_NETWORK,
                "connection_error": ErrorCategory.RETRYABLE_NETWORK,
                "connection_refused": ErrorCategory.RETRYABLE_NETWORK,
                "db_constraint": ErrorCategory.NON_RETRYABLE_DB,
                "validation_error": ErrorCategory.NON_RETRYABLE_VALIDATION,
            }
            return mapping.get(error_type, ErrorCategory.NON_RETRYABLE_CLIENT)

        if status_code:
            if status_code in (500, 502, 503, 504):
                return ErrorCategory.RETRYABLE_SERVER
            elif status_code == 400:
                return ErrorCategory.NON_RETRYABLE_CLIENT
            elif status_code in (401, 403):
                return ErrorCategory.NON_RETRYABLE_AUTH
            elif status_code == 404:
                return ErrorCategory.NON_RETRYABLE_NOT_FOUND
            elif status_code == 409:
                return ErrorCategory.NON_RETRYABLE_CONFLICT

        if error:
            error_name = type(error).__name__
            if error_name in ("TimeoutError", "ConnectTimeout", "ReadTimeout", "asyncio.TimeoutError"):
                return ErrorCategory.RETRYABLE_NETWORK
            if error_name in ("ConnectionError", "ConnectionRefusedError", "OSError"):
                return ErrorCategory.RETRYABLE_NETWORK

        return ErrorCategory.NON_RETRYABLE_CLIENT

    @staticmethod
    def compute_backoff(base_ms: int, attempt: int) -> int:
        """Exponential backoff with jitter."""
        delay = base_ms * (2 ** attempt)
        jitter = random.randint(0, 100)
        return delay + jitter

    @staticmethod
    def decide(
        tool_name: str,
        error_category: ErrorCategory,
        attempt: int,
        action_type: Optional[str] = None,
    ) -> RetryDecision:
        """Core decision function. Deterministic."""

        # Non-retryable -> immediate return
        if error_category in NON_RETRYABLE_CATEGORIES:
            return RetryDecision(
                should_retry=False,
                reason=f"Non-retryable error: {error_category.value}",
            )

        # Get tool metadata
        tool_meta = get_tool_metadata(tool_name)

        # Determine max retries (action-type-specific for propose_action)
        max_retries = tool_meta.max_retries
        if action_type:
            action_meta = get_action_metadata(action_type)
            if action_meta:
                max_retries = action_meta.max_retries

        # Check attempt limit
        if attempt >= max_retries:
            return RetryDecision(
                should_retry=False,
                reason=f"Max retries ({max_retries}) exceeded for {tool_name}",
            )

        # Compute backoff
        wait_ms = RetryController.compute_backoff(tool_meta.base_backoff_ms, attempt)

        # Determine verify-first requirement
        verify_first = False
        if not tool_meta.idempotent:
            if action_type:
                action_meta = get_action_metadata(action_type)
                verify_first = action_meta.verify_on_timeout if action_meta else True
            else:
                verify_first = True

        return RetryDecision(
            should_retry=True,
            wait_ms=wait_ms,
            reason=f"Retry {attempt + 1}/{max_retries} for {tool_name} after {wait_ms}ms",
            verify_first=verify_first,
            use_same_idempotency_key=True,
        )


async def execute_with_retry(
    tool_name: str,
    execute_fn: Callable[..., Awaitable[Any]],
    args: dict,
    action_type: Optional[str] = None,
    verify_fn: Optional[Callable[..., Awaitable[Optional[dict]]]] = None,
) -> dict:
    """Execute a tool call with automatic retry handling.

    Args:
        tool_name: Name of the tool being called
        execute_fn: Async function that executes the tool (takes **args)
        args: Arguments to pass to execute_fn
        action_type: If propose_action, the specific action type
        verify_fn: Optional async function for read-after-write check.
                   Should return existing result dict if found, None otherwise.

    Returns:
        Tool result dict (success or final error)
    """
    controller = RetryController()
    tool_meta = get_tool_metadata(tool_name)
    max_attempts = tool_meta.max_retries + 1  # +1 for initial attempt

    if action_type:
        action_meta = get_action_metadata(action_type)
        if action_meta:
            max_attempts = action_meta.max_retries + 1

    # Circuit breaker: reject early if circuit is OPEN
    if not circuit_breaker.can_execute(tool_name):
        logger.warning(f"[CIRCUIT] {tool_name}: Circuit OPEN, rejecting request")
        return {
            "success": False,
            "error": CircuitBreaker.FRIENDLY_ERROR,
            "error_type": "CIRCUIT_OPEN",
        }

    for attempt in range(max_attempts):
        try:
            result = await execute_fn(**args)

            # Check for application-level errors
            if isinstance(result, dict) and not result.get("success", True):
                error_type = result.get("error_type")
                status_code = result.get("status_code")

                if error_type or status_code:
                    error_cat = controller.classify_error(
                        error_type=error_type, status_code=status_code
                    )
                    decision = controller.decide(tool_name, error_cat, attempt, action_type)

                    if decision.should_retry:
                        # Verify-first for non-idempotent tools
                        if decision.verify_first and verify_fn:
                            existing = await verify_fn()
                            if existing:
                                logger.info(
                                    f"[RETRY] {tool_name}: Found existing result via verify, skipping retry"
                                )
                                return existing

                        logger.info(f"[RETRY] {decision.reason}")
                        await asyncio.sleep(decision.wait_ms / 1000)
                        continue

                # Non-retryable application error or no retry needed
                return result

            # Success
            circuit_breaker.record_success(tool_name)
            return result

        except Exception as e:
            error_cat = controller.classify_error(error=e)
            decision = controller.decide(tool_name, error_cat, attempt, action_type)

            if not decision.should_retry:
                logger.warning(
                    f"[RETRY] {tool_name}: Non-retryable error after {attempt + 1} attempts: {e}"
                )
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": error_cat.value,
                    "retries_attempted": attempt,
                }

            # Verify-first for non-idempotent
            if decision.verify_first and verify_fn:
                existing = await verify_fn()
                if existing:
                    logger.info(
                        f"[RETRY] {tool_name}: Found existing result via verify after error"
                    )
                    return existing

            logger.info(f"[RETRY] {decision.reason} (error: {type(e).__name__})")
            await asyncio.sleep(decision.wait_ms / 1000)

    # All attempts exhausted — record failure for circuit breaker
    circuit_breaker.record_failure(tool_name)
    logger.warning(f"[CIRCUIT] {tool_name}: Recording failure (all {max_attempts} attempts exhausted)")
    return {
        "success": False,
        "error": f"All {max_attempts} attempts failed for {tool_name}",
        "error_type": "MAX_RETRIES_EXCEEDED",
        "retries_attempted": max_attempts - 1,
    }
