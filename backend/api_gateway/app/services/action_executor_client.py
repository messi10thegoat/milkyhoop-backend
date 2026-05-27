"""
Action Executor gRPC Client

Connects to the action_executor microservice for:
- Preparing actions (two-phase commit)
- Executing confirmed actions
- Cancelling pending actions
- Querying action status

Iron Law 0: All execution goes through dedicated service with saga pattern.
Iron Law 14: All operations are idempotent via idempotency_key.
"""
import grpc
import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from milkyhoop_protos import (
    action_executor_pb2,
    action_executor_pb2_grpc,
    action_plan_pb2,
)

logger = logging.getLogger(__name__)

# Config from environment
ACTION_EXECUTOR_HOST = os.getenv("ACTION_EXECUTOR_GRPC_HOST", "action_executor")
ACTION_EXECUTOR_PORT = int(os.getenv("ACTION_EXECUTOR_GRPC_PORT", "5092"))

GRPC_CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 60000),
    ("grpc.keepalive_timeout_ms", 20000),
    ("grpc.keepalive_permit_without_calls", False),
    ("grpc.http2.max_pings_without_data", 0),
]


class ActionExecutorClient:
    """
    gRPC client for the ActionExecutor microservice.
    Thread-safe, persistent channel with lazy initialization.
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        timeout: float = 15.0,
    ):
        self.host = host or ACTION_EXECUTOR_HOST
        self.port = port or ACTION_EXECUTOR_PORT
        self.target = f"{self.host}:{self.port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[action_executor_pb2_grpc.ActionExecutorServiceStub] = None
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        async with self._connect_lock:
            if self.channel is None or self.stub is None:
                self.channel = grpc.aio.insecure_channel(
                    self.target, options=GRPC_CHANNEL_OPTIONS
                )
                self.stub = action_executor_pb2_grpc.ActionExecutorServiceStub(
                    self.channel
                )
                logger.info(f"Connected to ActionExecutor gRPC at {self.target}")

    async def ensure_connected(self):
        if self.channel is None or self.stub is None:
            await self.connect()

    async def close(self):
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None

    # =========================================================
    # PREPARE ACTION
    # =========================================================
    async def prepare_action(
        self,
        tenant_id: str,
        user_id: str,
        action_type: str,
        category: str,
        draft_payload: dict,
        idempotency_key: str = "",
        confidence: float = 0.0,
        assumptions: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Prepare an action for execution (two-phase commit, phase 1).
        Creates pending_action record, returns preview + confirmation token.

        Returns: {
            "success": bool,
            "pending_action_id": str,
            "confirmation_token": str,
            "preview_message": str,
            "preview": {...},
            "expires_at": str (ISO),
            "error_message": str
        }
        """
        await self.ensure_connected()

        action_type_enum = _map_action_type(action_type)
        category_enum = _map_category(category)

        action_plan = action_plan_pb2.ActionPlan(
            action_id=action_type,
            action_type=action_type_enum,
            category=category_enum,
            draft_payload_json=json.dumps(draft_payload)
            if isinstance(draft_payload, dict)
            else str(draft_payload),
            assumptions=assumptions or [],
            requires_confirmation=True,
            idempotency_key=idempotency_key or "",
            confidence=confidence,
        )

        try:
            resp = await self.stub.PrepareAction(
                action_executor_pb2.PrepareActionRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    action_plan=action_plan,
                ),
                timeout=self.timeout,
            )

            preview = None
            if resp.preview:
                journal_entries = []
                for j in resp.preview.journal_entries:
                    journal_entries.append(
                        {
                            "account_code": j.account_code,
                            "account_name": j.account_name,
                            "debit": j.debit,
                            "credit": j.credit,
                            "description": j.description,
                        }
                    )
                entities = []
                for e in resp.preview.entities:
                    entities.append(
                        {
                            "entity_type": e.entity_type,
                            "operation": e.operation,
                            "summary": e.summary,
                        }
                    )
                preview = {
                    "summary": resp.preview.summary,
                    "journal_entries": journal_entries,
                    "entities": entities,
                    "total_amount": resp.preview.total_amount,
                    "currency": resp.preview.currency or "IDR",
                    "impact_summary": dict(resp.preview.impact_summary),
                }

            expires_at_str = ""
            if resp.expires_at and resp.expires_at.seconds > 0:
                from datetime import datetime, timezone

                expires_at_str = datetime.fromtimestamp(
                    resp.expires_at.seconds, tz=timezone.utc
                ).isoformat()

            return {
                "success": resp.success,
                "pending_action_id": resp.pending_action_id,
                "confirmation_token": resp.confirmation_token,
                "preview_message": resp.preview_message,
                "preview": preview,
                "expires_at": expires_at_str,
                "error_message": resp.error_message,
            }
        except grpc.aio.AioRpcError as e:
            logger.error(f"PrepareAction gRPC error: {e.code()} - {e.details()}")
            return {
                "success": False,
                "pending_action_id": "",
                "confirmation_token": "",
                "preview_message": "",
                "preview": None,
                "expires_at": "",
                "error_message": f"Service error: {e.code()}",
            }
        except Exception as e:
            logger.error(f"PrepareAction error: {e}")
            return {
                "success": False,
                "pending_action_id": "",
                "confirmation_token": "",
                "preview_message": "",
                "preview": None,
                "expires_at": "",
                "error_message": str(e),
            }

    # =========================================================
    # EXECUTE ACTION
    # =========================================================
    async def execute_action(
        self,
        tenant_id: str,
        user_id: str,
        pending_action_id: str,
        confirmation_token: str = "",
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        """
        Execute a previously prepared action (two-phase commit, phase 2).

        Returns: {
            "success": bool,
            "status": str,
            "result": {"entity_id", "entity_number", "entity_type", "journal_entry_id", "impact", "message"},
            "error_message": str,
            "error_code": str
        }
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.ExecuteAction(
                action_executor_pb2.ExecuteActionRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    pending_action_id=pending_action_id,
                    confirmation_token=confirmation_token,
                    idempotency_key=idempotency_key,
                ),
                timeout=self.timeout,
            )

            result = None
            if resp.result:
                completed_at = ""
                if resp.result.completed_at and resp.result.completed_at.seconds > 0:
                    from datetime import datetime, timezone

                    completed_at = datetime.fromtimestamp(
                        resp.result.completed_at.seconds, tz=timezone.utc
                    ).isoformat()

                result = {
                    "entity_id": resp.result.entity_id,
                    "entity_number": resp.result.entity_number,
                    "entity_type": resp.result.entity_type,
                    "journal_entry_id": resp.result.journal_entry_id,
                    "impact": dict(resp.result.impact),
                    "message": resp.result.message,
                    "completed_at": completed_at,
                }

            # Map proto enum to string
            status_map = {
                0: "PENDING",
                1: "EXECUTING",
                2: "COMPLETED",
                3: "FAILED",
                4: "CANCELLED",
                5: "EXPIRED",
            }

            return {
                "success": resp.success,
                "status": status_map.get(resp.status, "UNKNOWN"),
                "result": result,
                "error_message": resp.error_message,
                "error_code": resp.error_code,
            }
        except grpc.aio.AioRpcError as e:
            logger.error(f"ExecuteAction gRPC error: {e.code()} - {e.details()}")
            return {
                "success": False,
                "status": "FAILED",
                "result": None,
                "error_message": f"Service error: {e.code()}",
                "error_code": "GRPC_ERROR",
            }
        except Exception as e:
            logger.error(f"ExecuteAction error: {e}")
            return {
                "success": False,
                "status": "FAILED",
                "result": None,
                "error_message": str(e),
                "error_code": "INTERNAL_ERROR",
            }

    # =========================================================
    # CANCEL ACTION
    # =========================================================
    async def cancel_action(
        self,
        tenant_id: str,
        user_id: str,
        pending_action_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        Cancel a pending action.
        Returns: {"success": bool, "status": str, "message": str}
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.CancelAction(
                action_executor_pb2.CancelActionRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    pending_action_id=pending_action_id,
                    reason=reason,
                ),
                timeout=self.timeout,
            )
            status_map = {
                0: "PENDING",
                1: "EXECUTING",
                2: "COMPLETED",
                3: "FAILED",
                4: "CANCELLED",
                5: "EXPIRED",
            }
            return {
                "success": resp.success,
                "status": status_map.get(resp.status, "UNKNOWN"),
                "message": resp.message,
            }
        except grpc.aio.AioRpcError as e:
            logger.error(f"CancelAction gRPC error: {e.code()} - {e.details()}")
            return {
                "success": False,
                "status": "FAILED",
                "message": f"Service error: {e.code()}",
            }
        except Exception as e:
            logger.error(f"CancelAction error: {e}")
            return {"success": False, "status": "FAILED", "message": str(e)}

    # =========================================================
    # GET ACTION STATUS
    # =========================================================
    async def get_action_status(
        self,
        tenant_id: str,
        action_id: str,
    ) -> Dict[str, Any]:
        """
        Query the status of a pending/executed action.
        Returns: {"action_id": str, "status": str, "error_message": str}
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.GetActionStatus(
                action_executor_pb2.GetActionStatusRequest(
                    tenant_id=tenant_id,
                    action_id=action_id,
                ),
                timeout=self.timeout,
            )
            status_map = {
                0: "PENDING",
                1: "EXECUTING",
                2: "COMPLETED",
                3: "FAILED",
                4: "CANCELLED",
                5: "EXPIRED",
            }
            return {
                "action_id": resp.action_id,
                "status": status_map.get(resp.status, "UNKNOWN"),
                "error_message": resp.error_message,
            }
        except grpc.aio.AioRpcError as e:
            logger.error(f"GetActionStatus gRPC error: {e.code()} - {e.details()}")
            return {
                "action_id": action_id,
                "status": "UNKNOWN",
                "error_message": f"Service error: {e.code()}",
            }
        except Exception as e:
            logger.error(f"GetActionStatus error: {e}")
            return {
                "action_id": action_id,
                "status": "UNKNOWN",
                "error_message": str(e),
            }

    # =========================================================
    # HEALTH CHECK
    # =========================================================
    async def health_check(self) -> Dict[str, Any]:
        await self.ensure_connected()
        try:
            from google.protobuf import empty_pb2

            resp = await self.stub.HealthCheck(
                empty_pb2.Empty(),
                timeout=5.0,
            )
            return {"status": resp.status, "timestamp": resp.timestamp}
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}


# =========================================================
# HELPER: Map string action types to proto enums
# =========================================================
def _map_action_type(action_type: str) -> int:
    mapping = {
        "CREATE_CUSTOMER": 0,
        "UPDATE_CUSTOMER": 1,
        "CREATE_VENDOR": 2,
        "CREATE_PRODUCT": 3,
        "CREATE_SALES_INVOICE": 10,
        "CREATE_PURCHASE_INVOICE": 11,
        "CREATE_EXPENSE": 12,
        "CREATE_CREDIT_NOTE": 13,
        "CREATE_PURCHASE_ORDER": 14,
        "RECEIVE_PAYMENT": 20,
        "MAKE_PAYMENT": 21,
        "BANK_TRANSFER": 22,
        "POST_GENERAL_JOURNAL": 30,
        "REVERSE_JOURNAL": 31,
        "CLOSE_PERIOD": 32,
        "REOPEN_PERIOD": 33,
        "GET_BALANCE": 40,
        "GET_TRIAL_BALANCE": 41,
        "GET_AR_AGING": 42,
    }
    return mapping.get(action_type, 0)


def _map_category(category: str) -> int:
    mapping = {
        "MASTER_DATA": 0,
        "DOCUMENT": 1,
        "PAYMENT": 2,
        "ACCOUNTING": 3,
        "READ": 4,
    }
    return mapping.get(category, 1)


# Singleton instance
_client: Optional[ActionExecutorClient] = None


def get_action_executor_client() -> ActionExecutorClient:
    """Get or create the singleton ActionExecutorClient."""
    global _client
    if _client is None:
        _client = ActionExecutorClient()
    return _client
