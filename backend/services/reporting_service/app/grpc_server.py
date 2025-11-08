"""
Reporting Service gRPC Server - THIN ROUTING LAYER
MilkyHoop 4.0 - Conversational Financial Management

This is the entry point that routes requests to handlers.
NO business logic here - all logic is in handlers/*.py

Handlers:
- LabaRugiHandler: GetLabaRugi (Income Statement)
- NeracaHandler: GetNeraca (Balance Sheet)
- ArusKasHandler: GetArusKas (Cash Flow Statement)
- PerubahanEkuitasHandler: GetPerubahanEkuitas (Changes in Equity)
- HealthHandler: HealthCheck
"""

import asyncio
import signal
import logging
import os
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2

from app.config import settings
from app import reporting_service_pb2 as pb
from app import reporting_service_pb2_grpc as pb_grpc
from app.prisma_client import prisma, connect_prisma, disconnect_prisma

# Import handlers
from handlers import (
    LabaRugiHandler,
    NeracaHandler,
    ArusKasHandler,
    PerubahanEkuitasHandler,
    HealthHandler
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


# ==========================================
# gRPC SERVICE IMPLEMENTATION (ROUTING ONLY)
# ==========================================

class ReportingServiceServicer(pb_grpc.ReportingServiceServicer):
    """
    Reporting Service gRPC handler - THIN ROUTING LAYER.
    All business logic is delegated to handlers.
    """
    
    async def GetLabaRugi(
        self,
        request: pb.ReportRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.LaporanLabaRugi:
        """Route to LabaRugiHandler"""
        return await LabaRugiHandler.handle_get_laba_rugi(
            request=request,
            context=context,
            pb=pb
        )
    
    async def GetNeraca(
        self,
        request: pb.ReportRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.LaporanNeraca:
        """Route to NeracaHandler"""
        return await NeracaHandler.handle_get_neraca(
            request=request,
            context=context,
            pb=pb
        )
    
    async def GetArusKas(
        self,
        request: pb.ReportRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.LaporanArusKas:
        """Route to ArusKasHandler"""
        return await ArusKasHandler.handle_get_arus_kas(
            request=request,
            context=context,
            pb=pb
        )
    
    async def GetPerubahanEkuitas(
        self,
        request: pb.ReportRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.LaporanPerubahanEkuitas:
        """Route to PerubahanEkuitasHandler"""
        return await PerubahanEkuitasHandler.handle_get_perubahan_ekuitas(
            request=request,
            context=context,
            pb=pb
        )
    
    async def HealthCheck(
        self,
        request: empty_pb2.Empty,
        context: grpc.aio.ServicerContext
    ) -> pb.HealthResponse:
        """Route to HealthHandler"""
        return await HealthHandler.handle_health_check(
            request=request,
            context=context,
            prisma=prisma,
            pb=pb
        )


# ==========================================
# SERVER STARTUP
# ==========================================

async def serve() -> None:
    """Start gRPC server"""
    
    # Connect Prisma
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await connect_prisma()
        logger.info("✅ Prisma connected")
    
    # Create server
    server = aio.server()
    
    # Add services
    pb_grpc.add_ReportingServiceServicer_to_server(
        ReportingServiceServicer(),
        server
    )

    # Enable reflection (for grpcurl debugging)
    from grpc_reflection.v1alpha import reflection
    SERVICE_NAMES = (
        pb.DESCRIPTOR.services_by_name['ReportingService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(SERVICE_NAMES, server)
    
    # Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)
    
    # Listen
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} listening on port {settings.GRPC_PORT}")
    logger.info(f"📍 Service: reporting_service.ReportingService")
    
    # Shutdown handling
    stop_event = asyncio.Event()
    
    def handle_shutdown(*_):
        logger.info("🛑 Shutdown signal received")
        stop_event.set()
    
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    try:
        await server.start()
        await stop_event.wait()
    finally:
        logger.info("🧹 Shutting down server...")
        await server.stop(5)
        if "DATABASE_URL" in os.environ:
            logger.info("🧹 Disconnecting Prisma...")
            await disconnect_prisma()
            logger.info("✅ Prisma disconnected")
        logger.info("✅ Server shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())