"""
Customer Reference Resolution gRPC Server
"""
import asyncio
import logging
import grpc
from concurrent import futures
import os
import sys
from datetime import datetime

# Add paths for imports (same as cust_context)
sys.path.append('/app/backend/services/cust_reference/app')
sys.path.append('/app/backend/api_gateway/libs')

# Import generated protobuf classes (same pattern as cust_context)
import cust_reference_pb2
import cust_reference_pb2_grpc

# Import our reference resolver
from services.reference_resolver import IndonesianReferenceResolver

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CustReferenceServicer(cust_reference_pb2_grpc.Cust_referenceServicer):
    """gRPC Servicer for Indonesian Reference Resolution"""
    
    def __init__(self):
        self.resolver = IndonesianReferenceResolver()
        logger.info("🧠 Reference Resolution Servicer initialized")
    
    async def ResolveReference(self, request, context):
        """Resolve Indonesian pronoun references to entities"""
        try:
            logger.info(f"🎯 Resolving reference: '{request.reference_text}' for session {request.session_id}")
            
            # Validate request
            if not request.session_id or not request.tenant_id or not request.reference_text:
                return cust_reference_pb2.ReferenceResponse(
                    success=False,
                    error_message="Missing required fields: session_id, tenant_id, or reference_text"
                )
            
            # Resolve reference using our resolver
            resolution = await self.resolver.resolve_reference(
                session_id=request.session_id,
                tenant_id=request.tenant_id,
                reference_text=request.reference_text,
                context_query=request.context_query
            )
            
            # Convert to protobuf response
            response = cust_reference_pb2.ReferenceResponse(
                success=resolution.get('success', False),
                resolved_entity=resolution.get('resolved_entity', ''),
                entity_type=resolution.get('entity_type', ''),
                resolution_method=resolution.get('resolution_method', ''),
                candidates=resolution.get('candidates', []),
                error_message=resolution.get('error_message', '')
            )
            
            if resolution.get('success'):
                logger.info(f"✅ Reference resolved: '{request.reference_text}' → '{resolution.get('resolved_entity')}' (method: {resolution.get('resolution_method')})")
            else:
                logger.warning(f"❌ Reference resolution failed: {resolution.get('error_message')}")
            
            return response
            
        except Exception as e:
            logger.error(f"💥 Reference resolution error: {str(e)}")
            return cust_reference_pb2.ReferenceResponse(
                success=False,
                error_message=f"Internal server error: {str(e)}"
            )
    
    async def Health(self, request, context):
        """Health check endpoint"""
        return cust_reference_pb2.HealthResponse(
            status="healthy",
            service="cust_reference",
            timestamp=int(datetime.now().timestamp())
        )

async def serve():
    """Start the gRPC server"""
    port = os.getenv('GRPC_PORT', '5013')
    
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    cust_reference_pb2_grpc.add_Cust_referenceServicer_to_server(
        CustReferenceServicer(), server
    )
    
    listen_addr = f'[::]:{port}'
    server.add_insecure_port(listen_addr)
    
    logger.info(f"🚀 Starting Customer Reference Resolution gRPC server on port {port}")
    await server.start()
    logger.info(f"✅ Server running at {listen_addr}")
    logger.info("🧠 Ready to resolve Indonesian pronoun references!")
    
    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down server...")
        await server.stop(5)

if __name__ == '__main__':
    asyncio.run(serve())
