"""
Health Service Implementation
Provides gRPC health checking functionality
"""

import grpc
import structlog
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

logger = structlog.get_logger(__name__)

class HealthService(health_pb2_grpc.HealthServicer):
    """
    Health Service Implementation
    Provides health status for the tenant parser service
    """
    
    def __init__(self):
        """Initialize health service"""
        self.logger = logger.bind(service="health")
        self._status = health_pb2.HealthCheckResponse.SERVING
        self.logger.info("HealthService initialized")
    
    def Check(self, request, context):
        """
        Perform health check
        """
        try:
            service = request.service if request.service else "tenant_parser"
            
            self.logger.debug(
                "Health check requested",
                service=service,
                status=self._status
            )
            
            return health_pb2.HealthCheckResponse(
                status=self._status
            )
            
        except Exception as e:
            self.logger.error("Health check failed", error=str(e))
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Health check failed: {str(e)}")
            raise
    
    def Watch(self, request, context):
        """
        Stream health status changes
        """
        try:
            service = request.service if request.service else "tenant_parser"
            
            self.logger.info(
                "Health watch started",
                service=service
            )
            
            # Send initial status
            yield health_pb2.HealthCheckResponse(
                status=self._status
            )
            
            # Keep connection alive (simplified implementation)
            while context.is_active():
                context.sleep(1)
                
        except Exception as e:
            self.logger.error("Health watch failed", error=str(e))
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Health watch failed: {str(e)}")
            raise
    
    def set_status(self, status):
        """Set service health status"""
        self._status = status
        self.logger.info("Health status changed", status=status)
