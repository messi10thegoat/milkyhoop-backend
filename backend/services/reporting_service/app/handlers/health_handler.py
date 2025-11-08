"""
Health Handler
Handles health check RPC method for Reporting Service
"""

import logging
from datetime import datetime
import grpc
from google.protobuf import empty_pb2

logger = logging.getLogger(__name__)


class HealthHandler:
    """Handler for health check operations"""
    
    @staticmethod
    async def handle_health_check(
        request: empty_pb2.Empty,
        context: grpc.aio.ServicerContext,
        prisma,
        pb
    ):
        """
        Health check endpoint
        
        Args:
            request: Empty proto message
            context: gRPC context
            prisma: Prisma client instance
            pb: reporting_service_pb2 module
            
        Returns:
            HealthResponse with status and timestamp
        """
        try:
            # Check Prisma connection
            await prisma.transaksiharian.count(take=1)
            return pb.HealthResponse(
                status="healthy",
                version="1.0.0",
                timestamp=int(datetime.utcnow().timestamp() * 1000),
                database_connected=True
            )
        except Exception as e:
            logger.error(f"❌ Health check failed: {str(e)}")
            return pb.HealthResponse(
                status="unhealthy",
                version="1.0.0",
                timestamp=int(datetime.utcnow().timestamp() * 1000),
                database_connected=False
            )