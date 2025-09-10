from backend.api_gateway.libs.milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os
from google.protobuf import empty_pb2, timestamp_pb2

import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from backend.services.tenant_manager.app.config import settings
from . import tenant_manager_pb2_grpc as pb_grpc
from . import tenant_manager_pb2 as pb
from .prisma_client import prisma

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


class TenantManagerServicer(pb_grpc.TenantManagerServicer):
    async def CreateTenant(self, request, context):
        tenant = await prisma.tenant.create(
            data={
                "alias": request.alias,
                "display_name": request.display_name,
                "menu_items": request.menu_items,
                "address": request.address,
            }
        )
        logger.info("✅ Tenant created: %s", tenant.alias)
        return pb.TenantResponse(status="ok", tenant=self._to_proto(tenant))

    async def GetTenant(self, request, context):
        tenant = await prisma.tenant.find_unique(where={"alias": request.alias})
        if not tenant:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Tenant not found")
            return pb.TenantResponse(status="not_found")
        return pb.TenantResponse(status="ok", tenant=self._to_proto(tenant))

    async def UpdateTenant(self, request, context):
        tenant = await prisma.tenant.update(
            where={"alias": request.alias},
            data={
                "display_name": request.display_name,
                "menu_items": request.menu_items,
                "address": request.address,
                "status": request.status,
            }
        )
        logger.info("✅ Tenant updated: %s", tenant.alias)
        return pb.TenantResponse(status="ok", tenant=self._to_proto(tenant))

    async def DeleteTenant(self, request, context):
        await prisma.tenant.update(
            where={"alias": request.alias},
            data={"status": "INACTIVE"}
        )
        logger.info("🗑️ Tenant marked as INACTIVE: %s", request.alias)
        return empty_pb2.Empty()

    async def ListTenants(self, request, context):
        tenants = await prisma.tenant.find_many(where={"status": "ACTIVE"})
        proto_tenants = [self._to_proto(t) for t in tenants]
        return pb.ListTenantsResponse(tenants=proto_tenants)

    async def HealthCheck(self, request, context):
        return empty_pb2.Empty()

    def _to_proto(self, tenant):
        created_at = timestamp_pb2.Timestamp()
        created_at.FromDatetime(tenant.created_at)
        updated_at = timestamp_pb2.Timestamp()
        updated_at.FromDatetime(tenant.updated_at)
        return pb.Tenant(
            id=tenant.id,
            alias=tenant.alias,
            display_name=tenant.display_name,
            menu_items=tenant.menu_items,
            address=tenant.address or "",
            status=tenant.status,
            created_at=created_at,
            updated_at=updated_at
        )


async def serve() -> None:
    if "DATABASE_URL" in os.environ:
        logger.info("🔌 Connecting to Prisma...")
        await prisma.connect()
        logger.info("✅ Prisma connected")

    server = aio.server()
    pb_grpc.add_TenantManagerServicer_to_server(TenantManagerServicer(), server)

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
