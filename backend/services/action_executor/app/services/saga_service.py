"""
Saga Service

Orchestrates the two-phase commit pattern for action execution:
1. PrepareAction: Create pending_action, return preview + confirmation token
2. ExecuteAction: Confirm and execute via kernel router
3. CancelAction: Cancel pending action
4. GetActionStatus: Query action status

IRON LAW 0: All writes go through Kernel (API Gateway endpoints).
IRON LAW 13: Optimistic locking prevents concurrent execution.
IRON LAW 14: Idempotency keys prevent duplicate execution.
"""
import json
import logging
from typing import Any, Dict

from .pending_action_store import PendingActionStore
from .kernel_router import KernelRouter
from ..config import settings

logger = logging.getLogger(__name__)


class SagaService:
    """Two-phase commit orchestration for action execution."""

    def __init__(self, store: PendingActionStore, router: KernelRouter):
        self.store = store
        self.router = router

    async def prepare(
        self,
        tenant_id: str,
        user_id: str,
        action_id: str,
        action_type: str,
        category: str,
        draft_payload: dict,
        idempotency_key: str = "",
        confidence: float = 0.0,
        assumptions: list = None,
        preview_data: dict = None,
    ) -> Dict[str, Any]:
        """
        Phase 1: Create pending action with preview data.
        Returns confirmation token + preview.
        """
        try:
            result = await self.store.create_pending_action(
                tenant_id=tenant_id,
                user_id=user_id,
                action_id=action_id,
                action_type=action_type,
                category=category,
                draft_payload=draft_payload,
                idempotency_key=idempotency_key,
                confidence=confidence,
                assumptions=assumptions or [],
                preview_data=preview_data or {},
                ttl_minutes=settings.PENDING_ACTION_TTL_MINUTES,
            )

            return {
                "success": True,
                "pending_action_id": result["id"],
                "confirmation_token": result["confirmation_token"],
                "trace_id": result["trace_id"],
                "expires_at": result["expires_at"],
                "error_message": "",
            }

        except Exception as e:
            logger.error(f"Prepare failed: {e}", exc_info=True)
            return {
                "success": False,
                "pending_action_id": "",
                "confirmation_token": "",
                "trace_id": "",
                "expires_at": None,
                "error_message": str(e),
            }

    async def execute(
        self,
        tenant_id: str,
        user_id: str,
        pending_action_id: str,
        confirmation_token: str = "",
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        """
        Phase 2: Execute a prepared action.
        1. Verify pending action exists and is valid
        2. Verify confirmation token matches
        3. Transition to EXECUTING (optimistic lock)
        4. Call kernel router
        5. Mark COMPLETED or FAILED
        """
        # 1. Get pending action
        action = await self.store.get_pending_action(pending_action_id, tenant_id)
        if action is None:
            return {
                "success": False,
                "status": "FAILED",
                "result": None,
                "error_message": f"Pending action not found: {pending_action_id}",
                "error_code": "NOT_FOUND",
            }

        # 2. Check status
        if action["status"] == "COMPLETED":
            # Idempotent return — already done
            result_data = json.loads(action["result_data"]) if action.get("result_data") else {}
            return {
                "success": True,
                "status": "COMPLETED",
                "result": result_data,
                "error_message": "",
                "error_code": "",
            }

        if action["status"] != "PENDING":
            return {
                "success": False,
                "status": action["status"],
                "result": None,
                "error_message": f"Action is in state {action['status']}, expected PENDING",
                "error_code": "INVALID_STATE",
            }

        # 3. Verify confirmation token
        if confirmation_token and action["confirmation_token"] != confirmation_token:
            return {
                "success": False,
                "status": "FAILED",
                "result": None,
                "error_message": "Invalid confirmation token",
                "error_code": "INVALID_TOKEN",
            }

        # 4. Transition to EXECUTING with optimistic lock
        transitioned = await self.store.transition_to_executing(
            pending_action_id, tenant_id, action["version"]
        )
        if not transitioned:
            return {
                "success": False,
                "status": "FAILED",
                "result": None,
                "error_message": "Concurrent modification detected or action expired",
                "error_code": "CONCURRENCY_ERROR",
            }

        # 5. Execute via kernel router
        payload = json.loads(action["draft_payload"]) if isinstance(action["draft_payload"], str) else action["draft_payload"]
        action_type = action["action_type"]

        try:
            kernel_result = await self.router.execute(
                action_type=action_type,
                tenant_id=tenant_id,
                user_id=user_id,
                payload=payload,
            )

            if kernel_result["success"]:
                # 6a. Mark COMPLETED
                result_data = {
                    "entity_id": kernel_result["entity_id"],
                    "entity_number": kernel_result["entity_number"],
                    "entity_type": kernel_result["entity_type"],
                    "journal_entry_id": kernel_result["journal_entry_id"],
                    "message": kernel_result["message"],
                }
                await self.store.mark_completed(pending_action_id, tenant_id, result_data)

                return {
                    "success": True,
                    "status": "COMPLETED",
                    "result": result_data,
                    "error_message": "",
                    "error_code": "",
                }
            else:
                # 6b. Mark FAILED
                await self.store.mark_failed(
                    pending_action_id, tenant_id,
                    kernel_result["message"], "KERNEL_ERROR"
                )
                return {
                    "success": False,
                    "status": "FAILED",
                    "result": None,
                    "error_message": kernel_result["message"],
                    "error_code": "KERNEL_ERROR",
                }

        except Exception as e:
            # Saga compensation: mark as FAILED
            logger.error(f"Execution error for {pending_action_id}: {e}", exc_info=True)
            await self.store.mark_failed(
                pending_action_id, tenant_id,
                str(e), "EXECUTION_ERROR"
            )
            return {
                "success": False,
                "status": "FAILED",
                "result": None,
                "error_message": str(e),
                "error_code": "EXECUTION_ERROR",
            }

    async def cancel(
        self, tenant_id: str, user_id: str, pending_action_id: str, reason: str = ""
    ) -> Dict[str, Any]:
        """Cancel a pending action."""
        cancelled = await self.store.mark_cancelled(
            pending_action_id, tenant_id, reason
        )
        if cancelled:
            return {
                "success": True,
                "status": "CANCELLED",
                "message": "Action cancelled successfully",
            }
        else:
            # Check if it exists at all
            action = await self.store.get_pending_action(pending_action_id, tenant_id)
            if action is None:
                return {
                    "success": False,
                    "status": "FAILED",
                    "message": f"Action not found: {pending_action_id}",
                }
            return {
                "success": False,
                "status": action["status"],
                "message": f"Cannot cancel action in state: {action['status']}",
            }

    async def get_status(
        self, tenant_id: str, action_id: str
    ) -> Dict[str, Any]:
        """Get the status of an action."""
        action = await self.store.get_pending_action(action_id, tenant_id)
        if action is None:
            return {
                "action_id": action_id,
                "status": "NOT_FOUND",
                "error_message": f"Action not found: {action_id}",
            }
        return {
            "action_id": action_id,
            "status": action["status"],
            "error_message": action.get("error_message") or "",
        }

    async def close(self):
        """Cleanup resources."""
        await self.router.close()
