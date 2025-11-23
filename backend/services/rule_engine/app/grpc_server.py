"""
Rule Engine Service gRPC Server
MilkyHoop 4.0 - Conversational Financial Management

Implements:
- EvaluateRule (evaluate rules against context)
- GetTenantRules (get all rules for a tenant)
- UpdateTenantRules (create/update tenant rules)
- HealthCheck

Features:
- Deterministic rule evaluation
- AND/OR condition support
- Priority-based rule matching
- 5-minute rule cache
- Multi-tenant isolation
"""

import asyncio
import signal
import logging
import os
from typing import Optional
import grpc
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

import sys
sys.path.insert(0, '/app/backend/services/rule_engine/app')

from config import settings
import rule_engine_pb2 as pb
import rule_engine_pb2_grpc as pb_grpc
from handlers.rule_handler import RuleHandler
from core.rule_cache import RuleCache
from storage.prisma_client import get_prisma, disconnect_prisma

# Logging configuration
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(settings.SERVICE_NAME)


# ==========================================
# GRPC SERVICE IMPLEMENTATION
# ==========================================

class RuleEngineServicer(pb_grpc.RuleEngineServicer):
    """gRPC servicer for Rule Engine"""

    def __init__(self):
        """Initialize servicer with cache and handler"""
        self.cache = RuleCache(ttl_seconds=settings.CACHE_TTL_SECONDS)
        self.handler = RuleHandler(cache=self.cache)
        logger.info(f"{settings.SERVICE_NAME} servicer initialized")

    async def EvaluateRule(
        self,
        request: pb.RuleRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.RuleResponse:
        """
        Evaluate rules against context data

        Args:
            request: RuleRequest proto
            context: gRPC context

        Returns:
            RuleResponse proto
        """
        trace_id = request.trace_id or "no-trace"
        logger.info(f"[{trace_id}] EvaluateRule called | tenant={request.tenant_id}, type={request.rule_type}")

        try:
            response = await self.handler.evaluate_rule(
                tenant_id=request.tenant_id,
                rule_context=request.rule_context,
                rule_type=request.rule_type,
                trace_id=trace_id
            )
            return response

        except Exception as e:
            logger.error(f"[{trace_id}] Error in EvaluateRule: {e}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, f"Internal error: {str(e)}")

    async def GetTenantRules(
        self,
        request: pb.TenantRulesRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.TenantRulesResponse:
        """
        Get all rules for a tenant

        Args:
            request: TenantRulesRequest proto
            context: gRPC context

        Returns:
            TenantRulesResponse proto
        """
        logger.info(f"GetTenantRules called | tenant={request.tenant_id}, type={request.rule_type}")

        try:
            response = await self.handler.get_tenant_rules(
                tenant_id=request.tenant_id,
                rule_type=request.rule_type
            )
            return response

        except Exception as e:
            logger.error(f"Error in GetTenantRules: {e}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, f"Internal error: {str(e)}")

    async def UpdateTenantRules(
        self,
        request: pb.UpdateRulesRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.UpdateRulesResponse:
        """
        Update/create a tenant rule

        Args:
            request: UpdateRulesRequest proto
            context: gRPC context

        Returns:
            UpdateRulesResponse proto
        """
        logger.info(f"UpdateTenantRules called | tenant={request.tenant_id}, type={request.rule_type}")

        try:
            response = await self.handler.update_tenant_rules(
                tenant_id=request.tenant_id,
                rule_yaml=request.rule_yaml,
                rule_type=request.rule_type,
                priority=request.priority
            )
            return response

        except Exception as e:
            logger.error(f"Error in UpdateTenantRules: {e}", exc_info=True)
            await context.abort(grpc.StatusCode.INTERNAL, f"Internal error: {str(e)}")

    async def HealthCheck(
        self,
        request: pb.HealthCheckRequest,
        context: grpc.aio.ServicerContext
    ) -> pb.HealthCheckResponse:
        """Health check endpoint"""
        cache_stats = self.handler.cache.get_stats()
        return pb.HealthCheckResponse(
            status=f"OK | cache_entries={cache_stats['entries']}",
            service_name=settings.SERVICE_NAME
        )


# ==========================================
# SERVER LIFECYCLE
# ==========================================

async def serve():
    """Start gRPC server"""
    server = aio.server()

    # Add Rule Engine service
    servicer = RuleEngineServicer()
    pb_grpc.add_RuleEngineServicer_to_server(servicer, server)

    # Add health check service
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    # Bind to port
    listen_addr = f'[::]:{settings.GRPC_PORT}'
    server.add_insecure_port(listen_addr)

    # Connect to database
    logger.info("Connecting to database...")
    await get_prisma()

    # Start server
    await server.start()
    logger.info(f"✅ {settings.SERVICE_NAME} gRPC server started on {listen_addr}")
    logger.info(f"Cache TTL: {settings.CACHE_TTL_SECONDS}s")

    # Graceful shutdown handler
    async def shutdown(sig):
        logger.info(f"Received signal {sig.name}, shutting down...")
        health_servicer.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
        await server.stop(grace=5)
        await disconnect_prisma()
        logger.info("Server stopped gracefully")

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

    # Keep server running
    await server.wait_for_termination()


# ==========================================
# ENTRY POINT
# ==========================================

if __name__ == '__main__':
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        exit(1)
