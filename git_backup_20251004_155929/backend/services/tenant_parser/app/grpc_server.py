"""
Fixed Enhanced Tenant Parser gRPC Server
Resolves TypeError in proto response and health check issues
"""
import asyncio
import logging
import grpc
from grpc import aio
import sys
import os
from concurrent import futures

# Import confidence engine  
sys.path.append('/app/backend/services/tenant_parser/app/services')
from enhanced_confidence_engine import create_enhanced_confidence_engine

# Import existing services
try:
    from app.services.tenant_parser_service import TenantParserService
    TENANT_PARSER_AVAILABLE = True
except ImportError:
    TENANT_PARSER_AVAILABLE = False

# Import proto definitions
import tenant_parser_pb2 as pb
import tenant_parser_pb2_grpc as pb_grpc
from google.protobuf import empty_pb2
from grpc_health.v1 import health_pb2_grpc
from grpc_health.v1 import health_pb2

logger = logging.getLogger(__name__)

class FixedEnhancedTenantParserService(pb_grpc.IntentParserServiceServicer):
    """
    Fixed Enhanced Tenant Parser with proper proto data types and health check
    """
    
    def __init__(self):
        try:
            # Initialize confidence engine
            self.confidence_engine = create_enhanced_confidence_engine()
            logger.info("Confidence engine initialized in tenant_parser")
            
            # Initialize existing tenant parser if available
            if TENANT_PARSER_AVAILABLE:
                self.tenant_parser = TenantParserService()
                logger.info("Tenant parser service initialized")
            else:
                self.tenant_parser = None
                logger.warning("Tenant parser service not available")
                
        except Exception as e:
            logger.error(f"Service initialization failed: {e}")
            raise
    
    # Legacy methods with correct signatures
    async def DoSomething(self, request: pb.IntentParserRequest, context) -> pb.IntentParserResponse:
        """Legacy method - maintain compatibility"""
        try:
            if self.tenant_parser:
                return await self.tenant_parser.DoSomething(request, context)
            else:
                # Fallback response
                response = pb.IntentParserResponse()
                response.status = "success"
                response.result = "Fixed enhanced tenant parser active"
                return response
        except Exception as e:
            logger.error(f"DoSomething error: {e}")
            response = pb.IntentParserResponse()
            response.status = "error"
            response.result = str(e)
            return response
    
    async def HealthCheck(self, request: empty_pb2.Empty, context) -> empty_pb2.Empty:
        """FIXED: Proper health check implementation"""
        try:
            # Test confidence engine directly instead of calling unimplemented method
            test_confidence = self.confidence_engine.calculate_super_confidence(
                "health check", [], "health_tenant"
            )
            logger.info(f"Health check passed - confidence engine operational: {test_confidence}")
            
            # Return success without calling unimplemented legacy method
            return empty_pb2.Empty()
                
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Health check failed: {str(e)}")
            raise
    
    # Core confidence engine methods with FIXED data types
    async def CalculateConfidence(self, request: pb.ConfidenceRequest, context) -> pb.ConfidenceResponse:
        """FIXED: Calculate confidence with proper proto data types"""
        try:
            logger.info(f"CalculateConfidence called for tenant: {request.tenant_id}")
            
            # Validate request data
            query = str(request.query) if request.query else ""
            tenant_id = str(request.tenant_id) if request.tenant_id else "default"
            
            if not query:
                raise ValueError("Query cannot be empty")
            
            # Convert proto FAQ results
            faq_results = []
            for i, proto_faq in enumerate(request.faq_results):
                try:
                    class MockFAQ:
                        def __init__(self, proto_faq, index):
                            self.question = str(getattr(proto_faq, 'question', f'FAQ {index}'))
                            self.answer = str(getattr(proto_faq, 'answer', ''))
                            self.content = str(getattr(proto_faq, 'content', f'{self.question} {self.answer}'))
                            self.similarity_score = float(getattr(proto_faq, 'similarity_score', 0.0))
                            self.score = float(getattr(proto_faq, 'score', 0.0))
                    
                    faq_results.append(MockFAQ(proto_faq, i))
                    
                except Exception as faq_error:
                    logger.warning(f"FAQ {i} conversion error: {faq_error}")
                    continue
            
            # Calculate confidence using engine
            confidence = self.confidence_engine.calculate_super_confidence(
                query, faq_results, tenant_id
            )
            confidence = float(confidence) if confidence >= 0 else 0.0
            
            # Get tier decision
            decision = self.confidence_engine.super_decision_engine(confidence)
            
            # FIXED: Build response with proper data type conversions
            response = pb.ConfidenceResponse()
            response.confidence = float(confidence)
            
            # FIXED: Ensure all string fields are properly converted
            tier_num = int(decision.get('tier', 4))
            intelligence_level = str(decision.get('intelligence_level', 'unknown'))
            response.tier_name = str(f"TIER {tier_num}: {intelligence_level.title()}")
            response.route = str(decision.get('route', 'polite_deflection'))
            response.cost_per_query = float(decision.get('cost_per_query', 0.0))
            response.intelligence_level = str(intelligence_level)
            response.tier = int(tier_num)
            response.api_call_required = bool(decision.get('api_call', False))
            response.faq_count = int(decision.get('faq_count', 0))
            
            # FIXED: Handle model field with safe string conversion
            model_value = decision.get('model', 'none')
            response.model = str(model_value) if model_value is not None else 'none'
            
            logger.info(f"Response prepared: confidence={confidence:.3f}, tier={tier_num}, route={decision.get('route', 'unknown')}")
            return response
            
        except Exception as e:
            logger.error(f"CalculateConfidence error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Confidence calculation failed: {str(e)}")
            raise

    async def MakeDecision(self, request: pb.DecisionRequest, context) -> pb.DecisionResponse:
        """FIXED: Make tier decision with proper data types"""
        try:
            confidence = float(request.confidence)
            decision = self.confidence_engine.super_decision_engine(confidence)
            
            # FIXED: Build response with safe data type conversions
            response = pb.DecisionResponse()
            response.tier = int(decision.get('tier', 4))
            response.intelligence_level = str(decision.get('intelligence_level', 'deflection'))
            response.route = str(decision.get('route', 'polite_deflection'))
            response.api_call_required = bool(decision.get('api_call', False))
            response.cost_per_query = float(decision.get('cost_per_query', 0.0))
            response.faq_count = int(decision.get('faq_count', 0))
            
            # FIXED: Safe model field assignment
            model_value = decision.get('model', 'none')
            response.model = str(model_value) if model_value is not None else 'none'
            response.response_time_ms = int(decision.get('response_time_ms', 50))
            
            logger.info(f"Decision made: tier={response.tier}, route={response.route}")
            return response
            
        except Exception as e:
            logger.error(f"MakeDecision error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            raise

    async def ExtractFaqAnswer(self, request: pb.FaqExtractionRequest, context) -> pb.FaqExtractionResponse:
        """FIXED: Extract FAQ answer with proper data types"""
        try:
            logger.info(f"ExtractFaqAnswer called with content length: {len(request.faq_content)}")
            
            # Find best FAQ match
            best_faq = None
            best_score = 0.0
            
            for proto_faq in request.faq_results:
                try:
                    score = float(getattr(proto_faq, 'similarity_score', 0.0))
                    if score > best_score:
                        best_score = score
                        best_faq = proto_faq
                except (ValueError, TypeError):
                    continue
            
            if not best_faq or best_score < 0.85:
                raise ValueError(f"No high-confidence FAQ match found (best: {best_score:.3f})")
            
            # FIXED: Build response with safe conversions
            response = pb.FaqExtractionResponse()
            response.answer = str(getattr(best_faq, 'answer', 'Answer not available'))
            response.question = str(getattr(best_faq, 'question', 'Question not available'))
            response.confidence = float(best_score)
            response.direct_response = True
            
            logger.info(f"FAQ answer extracted: confidence={best_score:.3f}")
            return response
            
        except Exception as e:
            logger.error(f"ExtractFaqAnswer error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            raise

    async def GetPoliteDeflection(self, request: pb.DeflectionRequest, context) -> pb.DeflectionResponse:
        """FIXED: Generate polite deflection with proper data types"""
        try:
            logger.info(f"GetPoliteDeflection called for tenant: {request.tenant_id}")
            
            # Use confidence engine's deflection logic
            # Use confidence engine's generic deflection logic
            tenant_id = str(request.tenant_id) if request.tenant_id else "default"
            
            # Generate contextual deflection message for any tenant
            deflection_message = self.confidence_engine.get_polite_deflection(tenant_id)

            # FIXED: Build response with generic message
            response = pb.DeflectionResponse()
            response.message = str(deflection_message)
            response.is_deflection = True
            response.suggested_contact = str("customer service")
            
            logger.info(f"Polite deflection generated for tenant: {tenant_id}")
            return response
            
        except Exception as e:
            logger.error(f"GetPoliteDeflection error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            raise


async def serve():
    """Start the fixed enhanced gRPC server"""
    try:
        # Create server
        server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
        
        # Create fixed service
        enhanced_service = FixedEnhancedTenantParserService()
        
        # Add to server
        pb_grpc.add_IntentParserServiceServicer_to_server(enhanced_service, server)


        # Manual health servicer implementation
        class HealthServicer(health_pb2_grpc.HealthServicer):
            def Check(self, request, context):
                return health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVING)

        health_servicer = HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
        
        # Start server
        listen_addr = '0.0.0.0:5012'
        server.add_insecure_port(listen_addr)
        await server.start()
        
        logger.info(f"FIXED enhanced tenant_parser gRPC server started on {listen_addr}")
        logger.info("Available methods: CalculateConfidence, MakeDecision, ExtractFaqAnswer, GetPoliteDeflection")
        logger.info("FIXES: Proto data type errors resolved, Health check implemented")
        
        await server.wait_for_termination()
        
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        raise


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
    except Exception as e:
        logger.error(f"Server failed: {e}")
        raise
