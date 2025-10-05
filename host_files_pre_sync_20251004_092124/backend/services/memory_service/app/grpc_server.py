from backend.api_gateway.libs.milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from app.config import settings
from app import memory_service_pb2_grpc as pb_grpc
from app import memory_service_pb2 as pb

# Import Memory CRUD Service
from app.services.memory_crud import MemoryCrudService

# ✅ Jika Prisma dipakai:
from app.prisma_client import prisma

# ✅ Logging config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)

# ✅ gRPC handler implementasi
class MemoryServiceServicer(pb_grpc.MemoryServiceServicer):
    
    def __init__(self):
        redis_url = os.getenv('REDIS_URL', 'redis://redis:6379')
        self.memory_service = MemoryCrudService(redis_url)
        logger.info(f"Memory service initialized with Redis: {redis_url}")
    
    async def StoreMemory(self, request, context):
        logger.info("📥 StoreMemory request received for user: %s", request.user_id)
        try:
            # Convert value (JSON string) to dict
            import json
            value_dict = json.loads(request.value) if request.value else {}
            
            success = await self.memory_service.store_memory(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                key=request.key,
                value=value_dict,
                ttl=request.ttl or 3600
            )
            
            return pb.StoreMemoryResponse(
                success=success,
                message="Memory stored successfully" if success else "Failed to store memory"
            )
            
        except Exception as e:
            logger.error(f"StoreMemory error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb.StoreMemoryResponse(
                success=False,
                message=f"Error: {e}"
            )
    
    async def GetMemory(self, request, context):
        logger.info("📥 GetMemory request received for key: %s", request.key)
        try:
            value = await self.memory_service.get_memory(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                key=request.key
            )
            
            if value is not None:
                import json
                return pb.GetMemoryResponse(
                    found=True,
                    value=json.dumps(value),
                    message="Memory found"
                )
            else:
                return pb.GetMemoryResponse(
                    found=False,
                    value="",
                    message="Memory not found or expired"
                )
                
        except Exception as e:
            logger.error(f"GetMemory error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb.GetMemoryResponse(
                found=False,
                value="",
                message=f"Error: {e}"
            )
    
    async def UpdateMemory(self, request, context):
        logger.info("📥 UpdateMemory request received for key: %s", request.key)
        try:
            import json
            value_dict = json.loads(request.value) if request.value else {}
            
            success = await self.memory_service.update_memory(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                key=request.key,
                value=value_dict
            )
            
            return pb.UpdateMemoryResponse(
                success=success,
                message="Memory updated successfully" if success else "Memory not found or update failed"
            )
            
        except Exception as e:
            logger.error(f"UpdateMemory error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb.UpdateMemoryResponse(
                success=False,
                message=f"Error: {e}"
            )
    
    async def ClearMemory(self, request, context):
        logger.info("📥 ClearMemory request received for user: %s", request.user_id)
        try:
            key = request.key if request.key else None
            
            success = await self.memory_service.clear_memory(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                key=key
            )
            
            message = "Memory cleared successfully" if success else "Failed to clear memory"
            if not key:
                message = "All user memories cleared" if success else "Failed to clear user memories"
            
            return pb.ClearMemoryResponse(
                success=success,
                message=message
            )
            
        except Exception as e:
            logger.error(f"ClearMemory error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb.ClearMemoryResponse(
                success=False,
                message=f"Error: {e}"
            )
    
    async def ListMemories(self, request, context):
        logger.info("📥 ListMemories request received for user: %s", request.user_id)
        try:
            memories = await self.memory_service.list_memories(
                user_id=request.user_id,
                tenant_id=request.tenant_id
            )
            
            memory_items = []
            for mem in memories:
                import json
                memory_items.append(
                    pb.MemoryItem(
                        key=mem["key"],
                        value=json.dumps(mem["value"]),
                        created_at=mem["created_at"],
                        expires_at=mem["expires_at"]
                    )
                )
            
            return pb.ListMemoriesResponse(
                memories=memory_items,
                count=len(memory_items),
                message=f"Found {len(memory_items)} memories"
            )
            
        except Exception as e:
            logger.error(f"ListMemories error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return pb.ListMemoriesResponse(
                memories=[],
                count=0,
                message=f"Error: {e}"
            )

async def serve() -> None:
    # ✅ Koneksi Prisma (opsional)
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await prisma.connect()
        logger.info("✅ Prisma connected")

    server = aio.server()
    pb_grpc.add_MemoryServiceServicer_to_server(MemoryServiceServicer(), server)

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