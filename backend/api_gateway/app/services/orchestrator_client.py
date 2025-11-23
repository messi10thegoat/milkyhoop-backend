"""
Real gRPC Orchestrator Client - SuperIntelligent Customer Service
Uses proper protobuf stubs for communication with cust_orchestrator service
"""
import asyncio
import logging
import grpc
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class OrchestratorClient:
    """
    Real gRPC Client for SuperIntelligent Customer Orchestrator
    
    Communicates with cust_orchestrator service using proper protobuf definitions
    Implements 4-Tier SuperIntelligent routing system
    """
    
    def __init__(self, host: str = "milkyhoop-dev-cust_orchestrator-1", port: int = 5013):
        self.endpoint = f"{host}:{port}"
        self.timeout = 15
        self.max_retries = 2
        logger.info(f"Real gRPC OrchestratorClient initialized: {self.endpoint}")
    
    async def process_customer_query(
        self, 
        message: str, 
        tenant_id: str, 
        session_id: Optional[str] = None
    ) -> str:
        """
        Process customer query via real gRPC call to SuperIntelligent orchestrator
        
        Returns processed response with 4-tier intelligence routing
        """
        session_id = session_id or f"api_gw_{id(asyncio.current_task())}"
        
        logger.info(f"[Real gRPC] Processing query: {tenant_id} '{message[:50]}...'")
        
        # Import protobuf stubs
        try:
            import sys
            sys.path.append('/app/backend/api_gateway/libs')
            from milkyhoop_protos.cust_orchestrator_pb2 import ProcessCustomerQueryRequest
            from milkyhoop_protos.cust_orchestrator_pb2_grpc import CustOrchestratorServiceStub
        except ImportError as e:
            logger.error(f"[Real gRPC] Protobuf import failed: {e}")
            raise Exception(f"Protobuf stubs not available: {e}")
        
        for attempt in range(self.max_retries + 1):
            try:
                # Create gRPC channel with keepalive configuration
                options = [
                    ('grpc.keepalive_time_ms', 30000),
                    ('grpc.keepalive_timeout_ms', 10000),
                    ('grpc.keepalive_permit_without_calls', 1),
                    ('grpc.http2.max_pings_without_data', 0),
                    ('grpc.http2.min_time_between_pings_ms', 30000),
                    ('grpc.http2.min_ping_interval_without_data_ms', 30000),
                ]
                channel = grpc.aio.insecure_channel(self.endpoint, options=options)
                
                try:
                    # Create gRPC stub
                    stub = CustOrchestratorServiceStub(channel)
                    
                    # Create request message
                    request = ProcessCustomerQueryRequest()
                    request.message = message
                    request.tenant_id = tenant_id
                    request.session_id = session_id
                    
                    # Add metadata for SuperIntelligent routing
                    request.metadata["source"] = "api_gateway"
                    request.metadata["version"] = "3.0.0"
                    request.metadata["intelligence_mode"] = "superintelligent"
                    
                    # Make gRPC call with timeout
                    logger.info(f"[Real gRPC] Calling ProcessCustomerQuery on attempt {attempt + 1}")
                    
                    response = await asyncio.wait_for(
                        stub.ProcessCustomerQuery(request),
                        timeout=self.timeout
                    )
                    
                    # Extract response
                    if response.response:
                        # Log SuperIntelligent metadata if available
                        if hasattr(response, 'superintelligent_metadata') and response.superintelligent_metadata:
                            meta = response.superintelligent_metadata
                            logger.info(f"[SuperIntelligent] Tier {meta.tier}, Route: {meta.route}, "
                                      f"Confidence: {meta.confidence:.3f}, Cost: Rp {meta.cost_rp}")
                        
                        logger.info(f"[Real gRPC] Success on attempt {attempt + 1}")
                        return response.response
                    else:
                        raise Exception("Empty response from orchestrator")
                        
                finally:
                    await channel.close()
                    
            except asyncio.TimeoutError:
                if attempt == self.max_retries:
                    logger.error(f"[Real gRPC] Timeout after {self.timeout}s")
                    raise Exception(f"SuperIntelligent orchestrator timeout after {self.timeout}s")
                else:
                    logger.warning(f"[Real gRPC] Timeout on attempt {attempt + 1}, retrying...")
                    await asyncio.sleep(0.5 * (attempt + 1))
                    
            except grpc.RpcError as e:
                if attempt == self.max_retries:
                    logger.error(f"[Real gRPC] gRPC error: {e.code()} - {e.details()}")
                    raise Exception(f"SuperIntelligent orchestrator gRPC error: {e.details()}")
                else:
                    logger.warning(f"[Real gRPC] gRPC error on attempt {attempt + 1}: {e.details()}")
                    await asyncio.sleep(0.5 * (attempt + 1))
                    
            except Exception as e:
                if attempt == self.max_retries:
                    logger.error(f"[Real gRPC] Final failure: {e}")
                    raise Exception(f"SuperIntelligent orchestrator unavailable: {e}")
                else:
                    logger.warning(f"[Real gRPC] Attempt {attempt + 1} failed: {e}")
                    await asyncio.sleep(0.5 * (attempt + 1))
    
    async def health_check(self) -> bool:
        """
        Health check via gRPC HealthCheck service
        """
        try:
            # Import protobuf stubs
            import sys
            sys.path.append('/app/backend/api_gateway/libs')
            from milkyhoop_protos.cust_orchestrator_pb2_grpc import CustOrchestratorServiceStub
            from google.protobuf.empty_pb2 import Empty

            options = [
                ('grpc.keepalive_time_ms', 30000),
                ('grpc.keepalive_timeout_ms', 10000),
                ('grpc.keepalive_permit_without_calls', 1),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.http2.min_time_between_pings_ms', 30000),
                ('grpc.http2.min_ping_interval_without_data_ms', 30000),
            ]
            channel = grpc.aio.insecure_channel(self.endpoint, options=options)
            
            try:
                stub = CustOrchestratorServiceStub(channel)
                
                # Call health check method
                response = await asyncio.wait_for(
                    stub.HealthCheck(Empty()),
                    timeout=3.0
                )
                
                logger.info("[Real gRPC] Health check: healthy")
                return True
                
            finally:
                await channel.close()
                
        except Exception as e:
            logger.warning(f"[Real gRPC] Health check failed: {e}")
            return False
    
    async def get_service_info(self) -> Dict[str, Any]:
        """
        Get service information via gRPC call
        """
        try:
            # Import protobuf stubs
            import sys
            sys.path.append('/app/backend/api_gateway/libs')
            from milkyhoop_protos.cust_orchestrator_pb2_grpc import CustOrchestratorServiceStub
            from google.protobuf.empty_pb2 import Empty

            options = [
                ('grpc.keepalive_time_ms', 30000),
                ('grpc.keepalive_timeout_ms', 10000),
                ('grpc.keepalive_permit_without_calls', 1),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.http2.min_time_between_pings_ms', 30000),
                ('grpc.http2.min_ping_interval_without_data_ms', 30000),
            ]
            channel = grpc.aio.insecure_channel(self.endpoint, options=options)
            
            try:
                stub = CustOrchestratorServiceStub(channel)
                
                response = await asyncio.wait_for(
                    stub.GetServiceInfo(Empty()),
                    timeout=5.0
                )
                
                return {
                    "service": response.service_name,
                    "endpoint": self.endpoint,
                    "protocol": "gRPC",
                    "version": response.version,
                    "features": list(response.features),
                    "grpc_version": response.grpc_version,
                    "status": "real_grpc_connection"
                }
                
            finally:
                await channel.close()
                
        except Exception as e:
            logger.warning(f"[Real gRPC] Service info failed: {e}")
            return {
                "service": "superintelligent_customer_orchestrator",
                "endpoint": self.endpoint,
                "protocol": "gRPC",
                "version": "unknown",
                "features": ["real_grpc_client"],
                "status": f"connection_error: {e}"
            }