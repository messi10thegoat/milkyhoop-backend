"""
Compatible Enhanced Tenant Parser gRPC Server
Uses only existing proto message types
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

logger = logging.getLogger(__name__)

class CompatibleEnhancedTenantParserService(pb_grpc.IntentParserServiceServicer):
    """
    Compatible Enhanced Tenant Parser using existing proto message types
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
                response.result = "Compatible enhanced tenant parser active"
                return response
        except Exception as e:
            logger.error(f"DoSomething error: {e}")
            response = pb.IntentParserResponse()
            response.status = "error"
            response.result = str(e)
            return response
    
    async def HealthCheck(self, request: empty_pb2.Empty, context) -> empty_pb2.Empty:
        """Health check with confidence engine validation"""
        try:
            # Test confidence engine
            test_confidence = self.confidence_engine.calculate_super_confidence(
                "health check", [], "health_tenant"
            )
            logger.info(f"Health check passed - confidence engine operational: {test_confidence}")
            
            if self.tenant_parser:
                return await self.tenant_parser.HealthCheck(request, context)
            else:
                return empty_pb2.Empty()
                
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise
    
    # Core confidence engine methods using existing message types
    async def CalculateConfidence(self, request: pb.ConfidenceRequest, context) -> pb.ConfidenceResponse:
        """Calculate confidence score using existing message types"""
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
            
            # Build response with existing message type
            response = pb.ConfidenceResponse()
            response.confidence = confidence
            response.tier_name = f"TIER {decision.get('tier', 4)}: {decision.get('intelligence_level', 'unknown').title()}"
            response.route = decision.get('route', 'polite_deflection')
            response.cost_per_query = float(decision.get('cost_per_query', 0.0))
            response.intelligence_level = decision.get('intelligence_level', 'deflection')
            response.tier_number = int(decision.get('tier', 4))
            response.api_call_required = bool(decision.get('api_call', False))
            response.faq_count = int(decision.get('faq_count', 0))
            response.model = decision.get('model', 'none')
            
            logger.info(f"Confidence calculated: {confidence:.3f}, tier={decision.get('tier', 4)}")
            return response
            
        except Exception as e:
            logger.error(f"CalculateConfidence error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Confidence calculation failed: {str(e)}")
            raise

    # Use existing message types for remaining methods
    async def MakeDecision(self, request: pb.DecisionRequest, context) -> pb.DecisionResponse:
        """Make tier decision using existing message types"""
        try:
            confidence = float(request.confidence)
            decision = self.confidence_engine.super_decision_engine(confidence)
            
            response = pb.DecisionResponse()
            response.tier_number = int(decision.get('tier', 4))
            response.intelligence_level = decision.get('intelligence_level', 'deflection')
            response.route = decision.get('route', 'polite_deflection')
            response.api_call_required = bool(decision.get('api_call', False))
            response.cost_per_query = float(decision.get('cost_per_query', 0.0))
            response.faq_count = int(decision.get('faq_count', 0))
            response.model = decision.get('model', 'none')
            response.response_time_ms = int(decision.get('response_time_ms', 50))
            
            return response
            
        except Exception as e:
            logger.error(f"MakeDecision error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            raise


async def serve():
    """Start the compatible enhanced gRPC server"""
    try:
        # Create server
        server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
        
        # Create service
        enhanced_service = CompatibleEnhancedTenantParserService()
        
        # Add to server
        pb_grpc.add_IntentParserServiceServicer_to_server(enhanced_service, server)
        
        # Start server
        listen_addr = '0.0.0.0:5012'
        server.add_insecure_port(listen_addr)
        await server.start()
        
        logger.info(f"Compatible enhanced tenant_parser gRPC server started on {listen_addr}")
        logger.info("Available methods: CalculateConfidence, MakeDecision")
        
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
