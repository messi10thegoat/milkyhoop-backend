from libs.milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os
import json

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.config import settings
from app import intent_parser_pb2_grpc as pb_grpc
from app import intent_parser_pb2 as pb

from app.services.llm_parser import parse_intent_entities  # async parser

prisma = Prisma()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


class IntentParserServiceServicer(pb_grpc.IntentParserServiceServicer):
    async def DoSomething(self, request, context):
        logger.info("📥 DoSomething request received: %s", request.input)
        try:
            # Panggil parser async
            result = parse_intent_entities(request.input)
            logger.info("📝 Intent: %s", result.get("intent"))
            logger.info("📝 Entities: %s", json.dumps(result.get("entities", {}), ensure_ascii=False))

            return pb.IntentParserResponse(
                status=result.get("intent", "unknown"),
                result=json.dumps({
                    "entities": result.get("entities", {})
                }, ensure_ascii=False)
            )


        except Exception as e:
            logger.error(f"🔥 Error in DoSomething handler: {e}", exc_info=True)
            return pb.IntentParserResponse(
                status="error",
                result='{"intent": null, "entities": {}}'
            )


async def serve() -> None:
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await prisma.connect()
        logger.info("✅ Prisma connected")

    server = aio.server()
    pb_grpc.add_IntentParserServiceServicer_to_server(IntentParserServiceServicer(), server)

    # Setup health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} gRPC server listening on port {settings.GRPC_PORT}")

    stop_event = asyncio.Event()

    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        await server.start()
        await stop_event.wait()
    finally:
        logger.info("🧹 Shutting down gRPC server...")
        await server.stop(5)
        if "DATABASE_URL" in os.environ:
            logger.info("🧹 Disconnecting Prisma...")
            await prisma.disconnect()
            logger.info("✅ Prisma disconnected")
        logger.info("✅ gRPC server shut down cleanly.")


if __name__ == "__main__":
    asyncio.run(serve())
