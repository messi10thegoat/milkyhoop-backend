"""
Transaction Service gRPC Server - THIN ROUTING LAYER
MilkyHoop 4.0 - Conversational Financial Management

This is the entry point that routes requests to handlers.
NO business logic here - all logic is in handlers/*.py

Handlers:
- TransactionHandler: CreateTransaction, UpdateTransaction, DeleteTransaction, GetTransaction, ListTransactions
- AnalyticsHandler: GetTopProducts, GetLowSellProducts (Phase 2)
- HealthHandler: HealthCheck
"""

from milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from google.protobuf import empty_pb2

from app.config import settings
from app import transaction_service_pb2 as pb
from app import transaction_service_pb2_grpc as pb_grpc
from app.prisma_client import prisma, connect_prisma, disconnect_prisma

# Import handlers
from handlers import TransactionHandler, AnalyticsHandler, HealthHandler

# Import external service clients
from services.accounting_client import process_transaction_accounting
from app import inventory_service_pb2_grpc as inv_pb_grpc

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)

# ==========================================
# INVENTORY SERVICE CLIENT (GLOBAL)
# ==========================================
inventory_channel = None
inventory_stub = None

def get_inventory_client():
    """Get or create inventory service gRPC client"""
    global inventory_channel, inventory_stub
    
    if inventory_stub is None:
        from app.config import settings
        inventory_channel = grpc.aio.insecure_channel(settings.INVENTORY_SERVICE_URL)
        inventory_stub = inv_pb_grpc.InventoryServiceStub(inventory_channel)
        logger.info(f"🔗 Inventory client connected: {settings.INVENTORY_SERVICE_URL}")
    
    return inventory_stub


# ==========================================
# gRPC SERVICE IMPLEMENTATION (ROUTING ONLY)
# ==========================================
class TransactionServiceServicer(pb_grpc.TransactionServiceServicer):
    """
    Transaction Service gRPC handler - THIN ROUTING LAYER.
    All business logic is delegated to handlers.
    """
    
    async def CreateTransaction(
        self,
        request: pb.CreateTransactionRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.TransactionResponse:
        """Route to TransactionHandler"""
        return await TransactionHandler.handle_create_transaction(
            request=request,
            context=context,
            pb=pb,
            get_inventory_client_func=get_inventory_client,
            process_accounting_func=process_transaction_accounting
        )
    
    async def UpdateTransaction(
        self,
        request: pb.UpdateTransactionRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.TransactionResponse:
        """Route to TransactionHandler"""
        return await TransactionHandler.handle_update_transaction(
            request=request,
            context=context,
            prisma=prisma,
            pb=pb
        )
    
    async def DeleteTransaction(
        self,
        request: pb.DeleteTransactionRequest,
        context: grpc.aio.ServicerContext
    ) -> empty_pb2.Empty:
        """Route to TransactionHandler"""
        return await TransactionHandler.handle_delete_transaction(
            request=request,
            context=context,
            prisma=prisma,
            empty_pb2=empty_pb2
        )
    
    async def GetTransaction(
        self,
        request: pb.GetTransactionRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.TransactionResponse:
        """Route to TransactionHandler"""
        return await TransactionHandler.handle_get_transaction(
            request=request,
            context=context,
            prisma=prisma,
            pb=pb
        )
    
    async def ListTransactions(
        self,
        request: pb.ListTransactionsRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.ListTransactionsResponse:
        """Route to TransactionHandler"""
        return await TransactionHandler.handle_list_transactions(
            request=request,
            context=context,
            prisma=prisma,
            pb=pb
        )
    
    async def GetTopProducts(
        self,
        request: pb.GetTopProductsRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.GetTopProductsResponse:
        """Route to AnalyticsHandler"""
        return await AnalyticsHandler.handle_get_top_products(
            request=request,
            context=context,
            prisma=prisma,
            pb=pb
        )
    
    async def GetLowSellProducts(
        self,
        request: pb.GetLowSellProductsRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.GetLowSellProductsResponse:
        """Route to AnalyticsHandler"""
        return await AnalyticsHandler.handle_get_low_sell_products(
            request=request,
            context=context,
            prisma=prisma,
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
    """Start gRPC server with Prisma connection and graceful shutdown."""
    
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await connect_prisma()
        logger.info("✅ Prisma connected")
    
    server = aio.server()
    
    pb_grpc.add_TransactionServiceServicer_to_server(
        TransactionServiceServicer(),
        server
    )
    
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set('', health_pb2.HealthCheckResponse.SERVING)
    
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server.add_insecure_port(listen_addr)
    logger.info(f"🚀 {settings.SERVICE_NAME} listening on port {settings.GRPC_PORT}")
    logger.info(f"📍 Service: transaction_service.TransactionService")
    
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
        
        if inventory_channel:
            logger.info("🧹 Closing inventory service channel...")
            await inventory_channel.close()
            logger.info("✅ Inventory channel closed")
        
        logger.info("✅ Server shutdown complete")


if __name__ == "__main__":
    asyncio.run(serve())