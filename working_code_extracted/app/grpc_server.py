"""
SuperIntelligent Customer Orchestrator gRPC Server
Implements proper protobuf servicer for 4-Tier Intelligence System
"""
import asyncio
import grpc
from concurrent import futures
import logging
from typing import Dict, Any, Optional
from datetime import datetime

# Import protobuf stubs
from cust_orchestrator_pb2 import (
    ProcessCustomerQueryRequest,
    ProcessCustomerQueryResponse,
    SuperIntelligentMetadata,
    HealthResponse,
    ServiceInfoResponse
)
from cust_orchestrator_pb2_grpc import (
    CustOrchestratorServiceServicer,
    add_CustOrchestratorServiceServicer_to_server
)
from google.protobuf.empty_pb2 import Empty
from grpc_health.v1 import health_pb2_grpc
from grpc_health.v1 import health_pb2

# Import SuperIntelligent orchestrator implementation
try:
    from app.orchestrator import SuperIntelligentCustomerOrchestrator
    ORCHESTRATOR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"Orchestrator import failed: {e}")
    ORCHESTRATOR_AVAILABLE = False

logger = logging.getLogger(__name__)

class SuperIntelligentOrchestratorServicer(CustOrchestratorServiceServicer):
    """
    Real gRPC Servicer for SuperIntelligent Customer Orchestrator
    
    Implements 4-Tier Intelligence System:
    - Tier 1 (≥0.85): Direct FAQ Response (No API call) - Cost: Rp 0
    - Tier 2 (0.60-0.84): GPT-3.5 Synthesis - Cost: Rp 9  
    - Tier 3 (0.30-0.59): Deep Understanding - Cost: Rp 18
    - Tier 4 (<0.30): Polite Deflection - Cost: Rp 0
    """
    
    def __init__(self):
        if ORCHESTRATOR_AVAILABLE:
            self.orchestrator = SuperIntelligentCustomerOrchestrator()
            logger.info("SuperIntelligent orchestrator initialized successfully")
        else:
            self.orchestrator = None
            logger.error("SuperIntelligent orchestrator not available")
    
    async def ProcessCustomerQuery(self, request: ProcessCustomerQueryRequest, context) -> ProcessCustomerQueryResponse:
        """
        Process customer query through SuperIntelligent 4-Tier system
        
        Args:
            request: ProcessCustomerQueryRequest with message, tenant_id, session_id, metadata
            context: gRPC context
            
        Returns:
            ProcessCustomerQueryResponse with response, intent, and SuperIntelligent metadata
        """
        try:
            if not self.orchestrator:
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details("SuperIntelligent orchestrator service not available")
                return self._create_error_response("Service unavailable")
            
            # Extract request data
            message = request.message
            tenant_id = request.tenant_id
            session_id = request.session_id or f"grpc_{id(request)}"
            
            # Validate required fields
            if not message or not tenant_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Missing required fields: message and tenant_id")
                return self._create_error_response("Invalid request")
            
            logger.info(f"[SuperIntelligent gRPC] Processing query for {tenant_id}: '{message[:50]}...'")
            
            # Process through SuperIntelligent orchestrator
            result = await self.orchestrator.process_customer_query(
                query=message,
                tenant_id=tenant_id,
                session_id=session_id
            )
            
            # Create response
            response = ProcessCustomerQueryResponse()
            
            if isinstance(result, dict):
                # Extract response data from orchestrator result
                response.response = result.get("response", str(result))
                response.intent = result.get("intent", "customer_inquiry")
                response.session_id = session_id
                response.trace_id = result.get("trace_id", session_id)
                
                # Extract SuperIntelligent metadata
                if "superintelligent_metadata" in result:
                    meta_data = result["superintelligent_metadata"]
                    
                    # Create SuperIntelligentMetadata protobuf message
                    si_metadata = SuperIntelligentMetadata()
                    si_metadata.tier = meta_data.get("tier", 2)
                    si_metadata.route = meta_data.get("route", "superintelligent_processing")
                    si_metadata.confidence = meta_data.get("confidence", 0.75)
                    si_metadata.cost_rp = meta_data.get("cost_rp", 9.0)
                    si_metadata.processing_method = meta_data.get("processing_method", "4_tier_intelligence")
                    
                    # Add FAQ sources if available
                    faq_sources = meta_data.get("faq_sources", [])
                    if faq_sources:
                        si_metadata.faq_sources.extend(faq_sources)
                    
                    response.superintelligent_metadata.CopyFrom(si_metadata)
                    
                    logger.info(f"[SuperIntelligent] Tier {si_metadata.tier}, "
                              f"Confidence: {si_metadata.confidence:.3f}, "
                              f"Cost: Rp {si_metadata.cost_rp}")
            else:
                # Handle string response
                response.response = str(result)
                response.intent = "customer_inquiry"
                response.session_id = session_id
                response.trace_id = session_id
                
                # Default metadata for string responses
                si_metadata = SuperIntelligentMetadata()
                si_metadata.tier = 2
                si_metadata.route = "string_response"
                si_metadata.confidence = 0.75
                si_metadata.cost_rp = 9.0
                si_metadata.processing_method = "superintelligent_direct"
                response.superintelligent_metadata.CopyFrom(si_metadata)
            
            logger.info(f"[SuperIntelligent gRPC] Successfully processed query for {tenant_id}")
            return response
            
        except Exception as e:
            logger.error(f"[SuperIntelligent gRPC] Processing failed: {str(e)}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"SuperIntelligent processing error: {str(e)}")
            return self._create_error_response(str(e))
    
    async def HealthCheck(self, request: Empty, context) -> HealthResponse:
        """
        Health check for SuperIntelligent orchestrator service
        
        Returns:
            HealthResponse with status and timestamp
        """
        try:
            response = HealthResponse()
            
            if self.orchestrator:
                response.status = "healthy"
                response.timestamp = datetime.utcnow().isoformat() + "Z"
                logger.info("[SuperIntelligent gRPC] Health check: healthy")
            else:
                response.status = "unhealthy"
                response.timestamp = datetime.utcnow().isoformat() + "Z"
                logger.warning("[SuperIntelligent gRPC] Health check: unhealthy - orchestrator not available")
            
            return response
            
        except Exception as e:
            logger.error(f"[SuperIntelligent gRPC] Health check failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Health check error: {str(e)}")
            
            response = HealthResponse()
            response.status = "error"
            response.timestamp = datetime.utcnow().isoformat() + "Z"
            return response
    
    async def GetServiceInfo(self, request: Empty, context) -> ServiceInfoResponse:
        """
        Get SuperIntelligent orchestrator service information
        
        Returns:
            ServiceInfoResponse with service details
        """
        try:
            response = ServiceInfoResponse()
            response.service_name = "SuperIntelligent Customer Orchestrator"
            response.version = "3.0.0-superintelligent"
            response.grpc_version = "1.71.0"
            
            # Add features
            features = [
                "4_tier_intelligence_system",
                "cost_optimized_routing",
                "faq_confidence_scoring",
                "llm_synthesis",
                "real_grpc_communication",
                "superintelligent_metadata"
            ]
            response.features.extend(features)
            
            logger.info("[SuperIntelligent gRPC] Service info requested")
            return response
            
        except Exception as e:
            logger.error(f"[SuperIntelligent gRPC] Service info failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Service info error: {str(e)}")
            
            # Return minimal response on error
            response = ServiceInfoResponse()
            response.service_name = "SuperIntelligent Customer Orchestrator"
            response.version = "error"
            response.grpc_version = "unknown"
            return response
    
    def _create_error_response(self, error_message: str) -> ProcessCustomerQueryResponse:
        """
        Create standardized error response
        
        Args:
            error_message: Error description
            
        Returns:
            ProcessCustomerQueryResponse with error details
        """
        response = ProcessCustomerQueryResponse()
        response.response = f"Maaf, terjadi kesalahan: {error_message}"
        response.intent = "error"
        response.session_id = "error"
        response.trace_id = "error"
        
        # Error metadata
        error_metadata = SuperIntelligentMetadata()
        error_metadata.tier = 0
        error_metadata.route = "error_response"
        error_metadata.confidence = 0.0
        error_metadata.cost_rp = 0.0
        error_metadata.processing_method = "error_handling"
        response.superintelligent_metadata.CopyFrom(error_metadata)
        
        return response

async def serve():
    """
    Start SuperIntelligent gRPC server
    
    Serves on port 5013 with proper protobuf servicer
    """
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    
    # Add SuperIntelligent orchestrator service
    orchestrator_servicer = SuperIntelligentOrchestratorServicer()
    add_CustOrchestratorServiceServicer_to_server(orchestrator_servicer, server)

    
    # Manual health servicer implementation
    class HealthServicer(health_pb2_grpc.HealthServicer):
        def Check(self, request, context):
            return health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVING)

    health_servicer = HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    
    # Configure server address
    listen_addr = '[::]:5013'
    server.add_insecure_port(listen_addr)
    
    logger.info(f"SuperIntelligent Customer Orchestrator gRPC server starting on {listen_addr}")
    logger.info(f"4-Tier Intelligence System: {'✅ Available' if ORCHESTRATOR_AVAILABLE else '❌ Unavailable'}")
    logger.info("gRPC Methods: ProcessCustomerQuery, HealthCheck, GetServiceInfo")
    
    # Start server
    await server.start()
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down SuperIntelligent gRPC server...")
        await server.stop(0)

if __name__ == '__main__':
    # Configure logging for production
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # Start SuperIntelligent orchestrator server
    logger.info("Initializing SuperIntelligent Customer Orchestrator gRPC Server...")
    asyncio.run(serve())
