# app/grpc_server.py

import asyncio
import signal
import logging

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.config import settings
from app.protos import template_service_pb2_grpc as service_pb2_grpc
from app.protos import template_service_pb2 as service_pb2

# ✅ Logging config
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(settings.SERVICE_NAME)

# ✅ gRPC handler implementasi
class PaymentAdapterServicer(service_pb2_grpc.PaymentAdapterServicer):
    async def DoSomething(self, request, context):
        logger.info("📥 DoSomething request received: %s", request.input)
        return service_pb2.PaymentAdapterResponse(
            status="ok",
            result=f"Processed input: {request.input}"
        )

async def serve() -> None:
    server = aio.server()

    # ✅ Register PaymentAdapter handler
    service_pb2_grpc.add_PaymentAdapterServicer_to_server(PaymentAdapterServicer(), server)

    # ✅ Health check setup
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)

    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 PaymentAdapter gRPC server listening on port {settings.GRPC_PORT}")

    stop_event = asyncio.Event()

    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received. Cleaning up...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        # await prisma.connect()  # Uncomment if using Prisma
        await server.start()
        await stop_event.wait()
    finally:
        await server.stop(5)
        logger.info("✅ gRPC server shut down cleanly.")
        # await prisma.disconnect()  # Uncomment if using Prisma

if __name__ == "__main__":
    asyncio.run(serve())
