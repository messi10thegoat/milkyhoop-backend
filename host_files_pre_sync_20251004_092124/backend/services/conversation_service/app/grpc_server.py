from backend.api_gateway.libs.milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.config import settings
from app import conversation_service_pb2_grpc as pb_grpc
from app import conversation_service_pb2 as pb

# ✅ Jika Prisma dipakai:
from app.prisma_client import prisma

# ✅ Logging config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)

# ✅ gRPC handler implementasi
class Conversation_serviceServicer(pb_grpc.Conversation_serviceServicer):
    async def DoSomething(self, request, context):
        logger.info("📥 DoSomething request received: %s", request.input)
        # Contoh logika tambahan: query DB (aktifkan jika Prisma dipakai)
        # example_data = await prisma.examplemodel.find_first()
        return pb.Conversation_serviceResponse(
            status="ok",
            result=f"Processed input: {request.input}"
        )

async def serve() -> None:
    # ✅ Koneksi Prisma (opsional)
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await prisma.connect()
        logger.info("✅ Prisma connected")

    server = aio.server()
    pb_grpc.add_Conversation_serviceServicer_to_server(Conversation_serviceServicer(), server)

    # ✅ Health check
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
        # ✅ Prisma disconnect (opsional)
        if "DATABASE_URL" in os.environ:
            logger.info("🧹 Disconnecting Prisma...")
            await prisma.disconnect()
            logger.info("✅ Prisma disconnected")
        logger.info("✅ gRPC server shut down cleanly.")

if __name__ == "__main__":
    asyncio.run(serve())
