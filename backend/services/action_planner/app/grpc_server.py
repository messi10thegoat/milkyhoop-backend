"""
action_planner gRPC Server

Async gRPC server with health check and graceful shutdown.
Uses proto-generated stubs from action_planner.proto.

IRON LAW 0 & 10: This server ONLY plans. It NEVER writes accounting data.
"""
import asyncio
import json
import logging
import signal
from concurrent import futures
from datetime import datetime

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

# Proto-generated imports (co-located stubs)
from . import action_planner_pb2, action_planner_pb2_grpc

from .config import settings
from .services.plan_generator import PlanGenerator

logger = logging.getLogger(__name__)


class ActionPlannerServicer(action_planner_pb2_grpc.ActionPlannerServiceServicer):
    """
    gRPC servicer for ActionPlannerService.
    Delegates all LLM operations to PlanGenerator.
    """

    def __init__(self):
        self.plan_generator = PlanGenerator()
        logger.info("ActionPlannerServicer initialized")

    async def shutdown(self):
        logger.info("ActionPlannerServicer shutting down")

    # ----- ClassifyIntent -----
    async def ClassifyIntent(self, request, context):
        try:
            result = await self.plan_generator.classify_intent(request.text)
            return action_planner_pb2.ClassifyIntentResponse(
                intent=result.get("intent", "UNCLEAR"),
                action_type=result.get("action_type", ""),
                confidence=result.get("confidence", 0.0),
                reason=result.get("reason", ""),
                source=result.get("source", "llm"),
            )
        except Exception as e:
            logger.error(f"ClassifyIntent error: {e}", exc_info=True)
            return action_planner_pb2.ClassifyIntentResponse(
                intent="UNCLEAR",
                confidence=0.0,
                reason=str(e),
                source="error",
            )

    # ----- GeneratePlan -----
    async def GeneratePlan(self, request, context):
        try:
            intent_data = {
                "intent": request.intent,
                "action_type": request.action_type,
            }
            ctx = None
            if request.context_json:
                try:
                    ctx = json.loads(request.context_json)
                except json.JSONDecodeError:
                    pass

            plan = await self.plan_generator.generate_plan(request.text, intent_data, ctx)
            return action_planner_pb2.GeneratePlanResponse(
                action_plan_json=json.dumps(plan),
                success=True,
                error="",
            )
        except Exception as e:
            logger.error(f"GeneratePlan error: {e}", exc_info=True)
            return action_planner_pb2.GeneratePlanResponse(
                action_plan_json="",
                success=False,
                error=str(e),
            )

    # ----- ParseDocumentText -----
    async def ParseDocumentText(self, request, context):
        try:
            parsed = await self.plan_generator.parse_document_text(
                request.text, request.action_type or "CREATE_PURCHASE_INVOICE"
            )
            return action_planner_pb2.ParseDocumentTextResponse(
                parsed_json=json.dumps(parsed),
                success=True,
                error="",
            )
        except Exception as e:
            logger.error(f"ParseDocumentText error: {e}", exc_info=True)
            return action_planner_pb2.ParseDocumentTextResponse(
                parsed_json="",
                success=False,
                error=str(e),
            )

    # ----- GenerateResponse -----
    async def GenerateResponse(self, request, context):
        try:
            response_text = await self.plan_generator.generate_response(
                request.text, context=request.context if request.context else None
            )
            return action_planner_pb2.GenerateResponseResponse(
                response_text=response_text or "",
                success=True,
            )
        except Exception as e:
            logger.error(f"GenerateResponse error: {e}", exc_info=True)
            return action_planner_pb2.GenerateResponseResponse(
                response_text="Maaf, terjadi kesalahan.",
                success=False,
            )

    # ----- ExtractEntities -----
    async def ExtractEntities(self, request, context):
        try:
            result = await self.plan_generator.extract_entities(request.text)
            return action_planner_pb2.ExtractEntitiesResponse(
                vendor_names=result.get("vendors", []),
                product_names=result.get("products", []),
                success=True,
                error="",
            )
        except Exception as e:
            logger.error(f"ExtractEntities error: {e}", exc_info=True)
            return action_planner_pb2.ExtractEntitiesResponse(
                success=False,
                error=str(e),
            )

    # ----- HealthCheck -----
    async def HealthCheck(self, request, context):
        from . import action_plan_pb2
        has_api_key = bool(settings.OPENAI_API_KEY)
        return action_plan_pb2.HealthResponse(
            status="healthy" if has_api_key else "degraded",
            timestamp=datetime.utcnow().isoformat() + "Z",
        )


class HealthServicer(health_pb2_grpc.HealthServicer):
    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING
        )


async def serve():
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=settings.GRPC_MAX_WORKERS)
    )

    servicer = ActionPlannerServicer()
    action_planner_pb2_grpc.add_ActionPlannerServiceServicer_to_server(servicer, server)

    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)

    logger.info(f"action_planner gRPC server starting on {listen_addr}")
    logger.info(f"  Service: {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}")
    logger.info(f"  Model: {settings.OPENAI_MODEL}")

    await server.start()
    logger.info("action_planner gRPC server STARTED (proto-wired)")

    stop_event = asyncio.Event()
    def _signal_handler(signame):
        logger.info(f"Received {signame}, initiating graceful shutdown...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler, sig.name)

    await stop_event.wait()

    logger.info("Shutting down gRPC server (5s grace period)...")
    await servicer.shutdown()
    await server.stop(5)
    logger.info("action_planner gRPC server stopped cleanly")


def main():
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing action_planner gRPC server...")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
