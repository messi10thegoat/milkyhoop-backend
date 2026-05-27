"""
Action Planner gRPC Client

Connects to the action_planner microservice for:
- Intent classification
- Plan generation
- Document text parsing
- Conversational response generation
- Entity extraction

Iron Law 0 & 10: This client delegates LLM operations to the action_planner service.
The API gateway NEVER calls LLMs directly for action-mode operations.
"""
import grpc
import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from milkyhoop_protos import action_planner_pb2, action_planner_pb2_grpc

logger = logging.getLogger(__name__)

# Config from environment
ACTION_PLANNER_HOST = os.getenv("ACTION_PLANNER_GRPC_HOST", "action_planner")
ACTION_PLANNER_PORT = int(os.getenv("ACTION_PLANNER_GRPC_PORT", "5090"))

GRPC_CHANNEL_OPTIONS = [
    ("grpc.keepalive_time_ms", 60000),
    ("grpc.keepalive_timeout_ms", 20000),
    ("grpc.keepalive_permit_without_calls", False),
    ("grpc.http2.max_pings_without_data", 0),
]


class ActionPlannerClient:
    """
    gRPC client for the ActionPlanner microservice.
    Thread-safe, persistent channel with lazy initialization.
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        timeout: float = 15.0,
    ):
        self.host = host or ACTION_PLANNER_HOST
        self.port = port or ACTION_PLANNER_PORT
        self.target = f"{self.host}:{self.port}"
        self.timeout = timeout
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[action_planner_pb2_grpc.ActionPlannerServiceStub] = None
        self._connect_lock = asyncio.Lock()

    async def connect(self):
        """Establish persistent gRPC channel."""
        async with self._connect_lock:
            if self.channel is None or self.stub is None:
                self.channel = grpc.aio.insecure_channel(
                    self.target, options=GRPC_CHANNEL_OPTIONS
                )
                self.stub = action_planner_pb2_grpc.ActionPlannerServiceStub(
                    self.channel
                )
                logger.info(f"Connected to ActionPlanner gRPC at {self.target}")

    async def ensure_connected(self):
        """Ensure connection before each call."""
        if self.channel is None or self.stub is None:
            await self.connect()

    async def close(self):
        """Close the gRPC channel."""
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None

    # =========================================================
    # CLASSIFY INTENT
    # =========================================================
    async def classify_intent(
        self, text: str, tenant_id: str = "", user_id: str = ""
    ) -> Dict[str, Any]:
        """
        Classify user intent via ActionPlanner service.

        Returns: {"intent": str, "action_type": str, "confidence": float, "reason": str}
        Falls back to UNCLEAR on error.
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.ClassifyIntent(
                action_planner_pb2.ClassifyIntentRequest(
                    text=text,
                    tenant_id=tenant_id,
                    user_id=user_id,
                ),
                timeout=self.timeout,
            )
            return {
                "intent": resp.intent,
                "action_type": resp.action_type,
                "confidence": resp.confidence,
                "reason": resp.reason,
                "source": resp.source,
            }
        except grpc.aio.AioRpcError as e:
            logger.error(f"ClassifyIntent gRPC error: {e.code()} - {e.details()}")
            return {
                "intent": "UNCLEAR",
                "action_type": "",
                "confidence": 0.0,
                "reason": f"gRPC error: {e.code()}",
            }
        except Exception as e:
            logger.error(f"ClassifyIntent error: {e}")
            return {
                "intent": "UNCLEAR",
                "action_type": "",
                "confidence": 0.0,
                "reason": str(e),
            }

    # =========================================================
    # GENERATE RESPONSE
    # =========================================================
    async def generate_response(self, text: str, context: str = "") -> Optional[str]:
        """
        Generate a natural conversational response via ActionPlanner service.

        Returns response text, or None on failure.
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.GenerateResponse(
                action_planner_pb2.GenerateResponseRequest(
                    text=text,
                    context=context or "",
                ),
                timeout=self.timeout,
            )
            if resp.success:
                return resp.response_text
            return None
        except grpc.aio.AioRpcError as e:
            logger.error(f"GenerateResponse gRPC error: {e.code()} - {e.details()}")
            return None
        except Exception as e:
            logger.error(f"GenerateResponse error: {e}")
            return None

    # =========================================================
    # PARSE DOCUMENT TEXT
    # =========================================================
    async def parse_document_text(
        self, text: str, action_type: str = "CREATE_PURCHASE_INVOICE"
    ) -> Optional[Dict[str, Any]]:
        """
        Parse free-form text into structured document data.

        Returns parsed dict, or None on failure.
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.ParseDocumentText(
                action_planner_pb2.ParseDocumentTextRequest(
                    text=text,
                    action_type=action_type,
                ),
                timeout=self.timeout,
            )
            if resp.success and resp.parsed_json:
                return json.loads(resp.parsed_json)
            return None
        except grpc.aio.AioRpcError as e:
            logger.error(f"ParseDocumentText gRPC error: {e.code()} - {e.details()}")
            return None
        except Exception as e:
            logger.error(f"ParseDocumentText error: {e}")
            return None

    # =========================================================
    # EXTRACT ENTITIES
    # =========================================================
    async def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """
        Extract vendor and product names from text.

        Returns: {"vendors": [...], "products": [...]}
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.ExtractEntities(
                action_planner_pb2.ExtractEntitiesRequest(text=text),
                timeout=self.timeout,
            )
            if resp.success:
                return {
                    "vendors": list(resp.vendor_names),
                    "products": list(resp.product_names),
                }
            return {"vendors": [], "products": []}
        except grpc.aio.AioRpcError as e:
            logger.error(f"ExtractEntities gRPC error: {e.code()} - {e.details()}")
            return {"vendors": [], "products": []}
        except Exception as e:
            logger.error(f"ExtractEntities error: {e}")
            return {"vendors": [], "products": []}

    # =========================================================
    # GENERATE PLAN
    # =========================================================
    async def generate_plan(
        self,
        text: str,
        tenant_id: str,
        user_id: str,
        intent: str,
        action_type: str,
        context: dict = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate an ActionPlan for the given intent.

        Returns plan dict, or None on failure.
        """
        await self.ensure_connected()
        try:
            resp = await self.stub.GeneratePlan(
                action_planner_pb2.GeneratePlanRequest(
                    text=text,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    intent=intent,
                    action_type=action_type,
                    context_json=json.dumps(context) if context else "",
                ),
                timeout=self.timeout,
            )
            if resp.success and resp.action_plan_json:
                return json.loads(resp.action_plan_json)
            return None
        except grpc.aio.AioRpcError as e:
            logger.error(f"GeneratePlan gRPC error: {e.code()} - {e.details()}")
            return None
        except Exception as e:
            logger.error(f"GeneratePlan error: {e}")
            return None

    # =========================================================
    # HEALTH CHECK
    # =========================================================
    async def health_check(self) -> Dict[str, Any]:
        """Check ActionPlanner service health."""
        await self.ensure_connected()
        try:
            resp = await self.stub.HealthCheck(
                action_planner_pb2.HealthCheckRequest(),
                timeout=5.0,
            )
            return {"status": resp.status, "timestamp": resp.timestamp}
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}


# Singleton instance
_client: Optional[ActionPlannerClient] = None


def get_action_planner_client() -> ActionPlannerClient:
    """Get or create the singleton ActionPlannerClient."""
    global _client
    if _client is None:
        _client = ActionPlannerClient()
    return _client
