"""
Action Validator gRPC Client

Connects to the action_validator microservice for:
- 6-layer validation pipeline (security, accounting, invariants, idempotency, dry-run, policy)
- Dry-run journal preview

Iron Law 0: All validation runs through dedicated service, not inline in API gateway.
"""
import grpc
import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from milkyhoop_protos import action_validator_pb2, action_validator_pb2_grpc, action_plan_pb2

logger = logging.getLogger(__name__)

# Config from environment
ACTION_VALIDATOR_HOST = os.getenv("ACTION_VALIDATOR_GRPC_HOST", "action_validator")
ACTION_VALIDATOR_PORT = int(os.getenv("ACTION_VALIDATOR_GRPC_PORT", "5091"))

GRPC_CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 10000),
    ("grpc.keepalive_timeout_ms", 5000),
    ("grpc.keepalive_permit_without_calls", True),
    ("grpc.http2.max_pings_without_data", 0),
]


class ActionValidatorClient:
    """
    gRPC client for the ActionValidator microservice.
    Thread-safe, persistent channel with lazy initialization.
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        timeout: float = 10.0,
    ):
        self.host = host or ACTION_VALIDATOR_HOST
        self.port = port or ACTION_VALIDATOR_PORT
        self.target = f"{self.host}:{self.port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[action_validator_pb2_grpc.ActionValidatorServiceStub] = None
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        """Establish persistent gRPC channel."""
        async with self._connect_lock:
            if self.channel is None or self.stub is None:
                self.channel = grpc.aio.insecure_channel(
                    self.target, options=GRPC_CHANNEL_OPTIONS
                )
                self.stub = action_validator_pb2_grpc.ActionValidatorServiceStub(
                    self.channel
                )
                logger.info(f"Connected to ActionValidator gRPC at {self.target}")

    async def ensure_connected(self):
        if self.channel is None or self.stub is None:
            await self.connect()

    async def close(self):
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None

    # =========================================================
    # VALIDATE ACTION
    # =========================================================
    async def validate_action(
        self,
        tenant_id: str,
        user_id: str,
        action_id: str,
        action_type: str,
        category: str,
        draft_payload: dict,
        idempotency_key: str = "",
        confidence: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Run full 6-layer validation pipeline on an action plan.

        Returns: {
            "valid": bool,
            "errors": [{"layer", "code", "message", "blocking", "field"}],
            "dry_run": {"journal_entries": [...], "total_debit", "total_credit", "balanced", "impact_summary", "currency"},
            "requires_confirmation": bool,
            "confirmation_message": str,
            "risk_level": int
        }
        """
        await self.ensure_connected()

        # Map string action_type to proto enum
        action_type_enum = _map_action_type(action_type)
        category_enum = _map_category(category)

        action_plan = action_plan_pb2.ActionPlan(
            action_id=action_id or action_type,
            action_type=action_type_enum,
            category=category_enum,
            draft_payload_json=json.dumps(draft_payload) if isinstance(draft_payload, dict) else str(draft_payload),
            requires_confirmation=True,
            idempotency_key=idempotency_key or "",
            confidence=confidence,
        )

        try:
            resp = await self.stub.ValidateAction(
                action_validator_pb2.ValidateActionRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    action_plan=action_plan,
                ),
                timeout=self.timeout,
            )

            errors = []
            for e in resp.errors:
                errors.append({
                    "layer": e.layer,
                    "code": e.code,
                    "message": e.message,
                    "blocking": e.blocking,
                    "field": e.field,
                })

            dry_run = None
            if resp.dry_run_result:
                journal_entries = []
                for j in resp.dry_run_result.journal_entries:
                    journal_entries.append({
                        "account_code": j.account_code,
                        "account_name": j.account_name,
                        "debit": j.debit,
                        "credit": j.credit,
                        "description": j.description,
                    })
                dry_run = {
                    "journal_entries": journal_entries,
                    "total_debit": resp.dry_run_result.total_debit,
                    "total_credit": resp.dry_run_result.total_credit,
                    "balanced": resp.dry_run_result.balanced,
                    "impact_summary": dict(resp.dry_run_result.impact_summary),
                    "currency": resp.dry_run_result.currency or "IDR",
                }

            return {
                "valid": resp.valid,
                "errors": errors,
                "dry_run": dry_run,
                "requires_confirmation": resp.requires_confirmation,
                "confirmation_message": resp.confirmation_message,
                "risk_level": resp.risk_level,
            }
        except grpc.aio.AioRpcError as e:
            logger.error(f"ValidateAction gRPC error: {e.code()} - {e.details()}")
            return {
                "valid": False,
                "errors": [{"layer": "SYSTEM", "code": "GRPC_ERROR", "message": f"Validation service error: {e.code()}", "blocking": True, "field": ""}],
                "dry_run": None,
                "requires_confirmation": False,
                "confirmation_message": "",
                "risk_level": 0,
            }
        except Exception as e:
            logger.error(f"ValidateAction error: {e}")
            return {
                "valid": False,
                "errors": [{"layer": "SYSTEM", "code": "INTERNAL_ERROR", "message": str(e), "blocking": True, "field": ""}],
                "dry_run": None,
                "requires_confirmation": False,
                "confirmation_message": "",
                "risk_level": 0,
            }

    # =========================================================
    # DRY-RUN ACTION
    # =========================================================
    async def dry_run_action(
        self,
        tenant_id: str,
        user_id: str,
        action_type: str,
        draft_payload: dict,
    ) -> Optional[Dict[str, Any]]:
        """
        Run dry-run only (journal preview without full validation).
        Returns dry_run dict or None on failure.
        """
        await self.ensure_connected()

        action_type_enum = _map_action_type(action_type)

        action_plan = action_plan_pb2.ActionPlan(
            action_id=action_type,
            action_type=action_type_enum,
            draft_payload_json=json.dumps(draft_payload),
        )

        try:
            resp = await self.stub.DryRunAction(
                action_validator_pb2.DryRunActionRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    action_plan=action_plan,
                ),
                timeout=self.timeout,
            )

            if not resp.success:
                return None

            journal_entries = []
            for j in resp.dry_run_result.journal_entries:
                journal_entries.append({
                    "account_code": j.account_code,
                    "account_name": j.account_name,
                    "debit": j.debit,
                    "credit": j.credit,
                    "description": j.description,
                })

            return {
                "journal_entries": journal_entries,
                "total_debit": resp.dry_run_result.total_debit,
                "total_credit": resp.dry_run_result.total_credit,
                "balanced": resp.dry_run_result.balanced,
                "impact_summary": dict(resp.dry_run_result.impact_summary),
                "currency": resp.dry_run_result.currency or "IDR",
            }
        except grpc.aio.AioRpcError as e:
            logger.error(f"DryRunAction gRPC error: {e.code()} - {e.details()}")
            return None
        except Exception as e:
            logger.error(f"DryRunAction error: {e}")
            return None

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
    """Map string action_type to proto ActionType enum value."""
    mapping = {
        "CREATE_CUSTOMER": 0,
        "UPDATE_CUSTOMER": 1,
        "CREATE_VENDOR": 2,
        "CREATE_PRODUCT": 3,
        "CREATE_SALES_INVOICE": 10,
        "CREATE_PURCHASE_INVOICE": 11,
        "CREATE_EXPENSE": 12,
        "RECEIVE_PAYMENT": 20,
        "MAKE_PAYMENT": 21,
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
    """Map string category to proto ActionCategory enum value."""
    mapping = {
        "MASTER_DATA": 0,
        "DOCUMENT": 1,
        "PAYMENT": 2,
        "ACCOUNTING": 3,
        "READ": 4,
    }
    return mapping.get(category, 1)


# Singleton instance
_client: Optional[ActionValidatorClient] = None


def get_action_validator_client() -> ActionValidatorClient:
    """Get or create the singleton ActionValidatorClient."""
    global _client
    if _client is None:
        _client = ActionValidatorClient()
    return _client
