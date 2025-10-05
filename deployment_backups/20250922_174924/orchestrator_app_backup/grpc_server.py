"""
Customer Service Orchestrator gRPC Server
Provides gRPC interface for customer query processing
"""
import asyncio
import grpc
from concurrent import futures
import logging
from grpc_health.v1 import health_pb2_grpc, health_pb2

# Import orchestrator implementation
try:
    from app.orchestrator import CustomerServiceOrchestrator
    ORCHESTRATOR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"Orchestrator import failed: {e}")
    ORCHESTRATOR_AVAILABLE = False

logger = logging.getLogger(__name__)

class CustomerOrchestratorServicer:
    """gRPC servicer for customer orchestrator"""
    
    def __init__(self):
        if ORCHESTRATOR_AVAILABLE:
            self.orchestrator = CustomerServiceOrchestrator()
        else:
            self.orchestrator = None
            logger.warning("Orchestrator not available - running in health-check only mode")
    
    async def ProcessCustomerQuery(self, request, context):
        """Process customer query through complete pipeline"""
        if not self.orchestrator:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Orchestrator service not available")
            return {}
        
        try:
            result = await self.orchestrator.process_customer_query(
                query=request.query,
                tenant_id=request.tenant_id,
                session_id=request.session_id if hasattr(request, 'session_id') else None
            )
            
            logger.info(f"Successfully processed query for tenant: {request.tenant_id}")
            return result
            
        except Exception as e:
            logger.error(f"gRPC call failed: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return {}

class HealthServicer(health_pb2_grpc.HealthServicer):
    """Health check servicer"""
    
    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING
        )

async def serve():
    """Start the gRPC server"""
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    
    # Add health check service
    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    
    # Add orchestrator service if available
    if ORCHESTRATOR_AVAILABLE:
        orchestrator_servicer = CustomerOrchestratorServicer()
        # TODO: Add orchestrator servicer to server when proto is defined
        # orchestrator_pb2_grpc.add_CustomerOrchestratorServicer_to_server(orchestrator_servicer, server)
        logger.info("Orchestrator service registered")
    else:
        logger.warning("Orchestrator service not available - health check only")
    
    # Configure server address
    listen_addr = '[::]:5013'
    server.add_insecure_port(listen_addr)
    
    logger.info(f"Customer Service Orchestrator gRPC server starting on {listen_addr}")
    
    # Start server
    await server.start()
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down gRPC server...")
        await server.stop(0)

if __name__ == '__main__':
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # Start server
    logger.info("Initializing Customer Service Orchestrator...")
    asyncio.run(serve())