"""
Complete Fixed Enhanced Tenant Parser gRPC Server
Resolves syntax error and provides all confidence engine methods
"""
import asyncio
import logging
import grpc
from grpc import aio
import sys
import os

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

class CompleteEnhancedTenantParserService(pb_grpc.IntentParserServiceServicer):
    """
    Complete Enhanced Tenant Parser with all confidence engine methods
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
                response.result = "Enhanced tenant parser active"
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
    
    # Core confidence engine methods
    async def CalculateConfidence(self, request: pb.ConfidenceRequest, context) -> pb.ConfidenceResponse:
        """Calculate confidence score for customer query"""
        try:
            logger.info(f"CalculateConfidence called for tenant: {request.tenant_id}")
            
            # Robust data validation and conversion
            query = str(request.query) if request.query else ""
            tenant_id = str(request.tenant_id) if request.tenant_id else "default"
            
            if not query:
                raise ValueError("Query cannot be empty")
                
            # Convert proto FAQ results with type safety
            faq_results = []
            for i, proto_faq in enumerate(request.faq_results):
                try:
                    class SafeMockFAQ:
                        def __init__(self, proto_faq, index):
                            # Safe string conversion
                            self.question = str(proto_faq.question) if hasattr(proto_faq, "question") else f"FAQ {index}"
                            self.answer = str(proto_faq.answer) if hasattr(proto_faq, "answer") else ""
                            self.content = str(proto_faq.content) if hasattr(proto_faq, "content") else f"{self.question} {self.answer}"
                            
                            # Safe numeric conversion with defaults
                            try:
                                self.similarity_score = float(proto_faq.similarity_score) if hasattr(proto_faq, "similarity_score") else 0.0
                            except (ValueError, TypeError):
                                self.similarity_score = 0.0
                                
                            try:
                                self.score = float(proto_faq.score) if hasattr(proto_faq, "score") else 0.0
                            except (ValueError, TypeError):
                                self.score = 0.0
                            
                            logger.debug(f"FAQ {i}: score={self.score}, similarity={self.similarity_score}")
                    
                    faq_results.append(SafeMockFAQ(proto_faq, i))
                    
                except Exception as faq_error:
                    logger.warning(f"FAQ {i} conversion error: {faq_error}, skipping")
                    continue
            
            logger.info(f"Processed {len(faq_results)} FAQ results for confidence calculation")
            
            # Calculate confidence using engine
            confidence = self.confidence_engine.calculate_super_confidence(
                query, faq_results, tenant_id
            )
            
            # Ensure confidence is a valid float
            if not isinstance(confidence, (int, float)) or confidence < 0:
                confidence = 0.0
                
            confidence = float(confidence)
            logger.info(f"Calculated confidence: {confidence:.3f}")
            
            # Get tier decision
            decision = self.confidence_engine.super_decision_engine(confidence)
            
            # Build response with type safety
            response = pb.ConfidenceResponse()
            response.confidence = confidence
            response.tier_name = str(f"TIER {decision.get('tier', 4)}: {decision.get('intelligence_level', 'unknown').title()}")
            response.route = str(decision.get('route', 'polite_deflection'))
            response.cost_per_query = float(decision.get('cost_per_query', 0.0))
            response.intelligence_level = str(decision.get('intelligence_level', 'deflection'))
            response.tier_number = int(decision.get('tier', 4))
            response.api_call_required = bool(decision.get('api_call', False))
            response.faq_count = int(decision.get('faq_count', 0))
            response.model = str(decision.get('model', 'none'))
            
            logger.info(f"Response prepared: confidence={confidence:.3f}, tier={decision.get('tier', 4)}, route={decision.get('route', 'unknown')}")
            return response
            
        except ValueError as ve:
            logger.error(f"CalculateConfidence validation error: {ve}")
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"Invalid request data: {str(ve)}")
            raise
            
        except Exception as e:
            logger.error(f"CalculateConfidence unexpected error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Confidence calculation failed: {str(e)}")
            raise

    async def MakeDecision(self, request: pb.DecisionRequest, context) -> pb.DecisionResponse:
        """Make intelligence tier decision based on confidence score"""
        try:
            logger.info(f"MakeDecision called with confidence: {request.confidence}")
            
            # Validate confidence score
            confidence = float(request.confidence)
            if confidence < 0 or confidence > 1:
                raise ValueError(f"Confidence must be between 0 and 1, got: {confidence}")
            
            # Get tier decision from engine
            decision = self.confidence_engine.super_decision_engine(confidence)
            
            # Build response
            response = pb.DecisionResponse()
            response.tier_number = int(decision.get('tier', 4))
            response.intelligence_level = str(decision.get('intelligence_level', 'deflection'))
            response.route = str(decision.get('route', 'polite_deflection'))
            response.api_call_required = bool(decision.get('api_call', False))
            response.cost_per_query = float(decision.get('cost_per_query', 0.0))
            response.faq_count = int(decision.get('faq_count', 0))
            response.model = str(decision.get('model', 'none'))
            response.response_time_ms = int(decision.get('response_time_ms', 50))
            
            logger.info(f"Decision made: tier={response.tier_number}, route={response.route}")
            return response
            
        except ValueError as ve:
            logger.error(f"MakeDecision validation error: {ve}")
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"Invalid confidence score: {str(ve)}")
            raise
            
        except Exception as e:
            logger.error(f"MakeDecision error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Decision making failed: {str(e)}")
            raise

    async def ExtractFaqAnswer(self, request: pb.FaqAnswerRequest, context) -> pb.FaqAnswerResponse:
        """Extract direct FAQ answer for high confidence matches"""
        try:
            logger.info(f"ExtractFaqAnswer called for tenant: {request.tenant_id}")
            
            # Validate request
            if not request.faq_results:
                raise ValueError("No FAQ results provided")
            
            # Find best FAQ match
            best_faq = None
            best_score = 0.0
            
            for proto_faq in request.faq_results:
                try:
                    score = float(proto_faq.similarity_score) if hasattr(proto_faq, "similarity_score") else 0.0
                    if score > best_score:
                        best_score = score
                        best_faq = proto_faq
                except (ValueError, TypeError):
                    continue
            
            if not best_faq or best_score < 0.85:
                raise ValueError(f"No high-confidence FAQ match found (best: {best_score:.3f})")
            
            # Build response
            response = pb.FaqAnswerResponse()
            response.answer = str(best_faq.answer) if hasattr(best_faq, "answer") else "Answer not available"
            response.question = str(best_faq.question) if hasattr(best_faq, "question") else "Question not available"
            response.confidence = float(best_score)
            response.direct_response = True
            
            logger.info(f"FAQ answer extracted: confidence={best_score:.3f}")
            return response
            
        except ValueError as ve:
            logger.error(f"ExtractFaqAnswer validation error: {ve}")
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"FAQ extraction failed: {str(ve)}")
            raise
            
        except Exception as e:
            logger.error(f"ExtractFaqAnswer error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"FAQ answer extraction failed: {str(e)}")
            raise

    async def GetPoliteDeflection(self, request: pb.DeflectionRequest, context) -> pb.DeflectionResponse:
        """Generate polite deflection for out-of-scope queries"""
        try:
            logger.info(f"GetPoliteDeflection called for tenant: {request.tenant_id}")
            
            # Use confidence engine's deflection logic
            deflection_messages = self.confidence_engine.get_polite_deflection_messages()
            
            # Select appropriate message based on tenant or use default
            tenant_id = str(request.tenant_id) if request.tenant_id else "default"
            
            # Build response
            response = pb.DeflectionResponse()
            response.message = deflection_messages.get(tenant_id, deflection_messages.get("default", 
                "Maaf, pertanyaan ini belum bisa saya jawab. Silakan hubungi customer service untuk bantuan lebih lanjut."))
            response.is_deflection = True
            response.suggested_contact = "customer service"
            
            logger.info(f"Polite deflection generated for tenant: {tenant_id}")
            return response
            
        except Exception as e:
            logger.error(f"GetPoliteDeflection error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Deflection generation failed: {str(e)}")
            raise


async def serve():
    """Start the enhanced gRPC server"""
    try:
        # Create gRPC server
        server = grpc.aio.server(
            futures.ThreadPoolExecutor(max_workers=10),
            options=[
                ('grpc.keepalive_time_ms', 60000),
                ('grpc.keepalive_timeout_ms', 5000),
                ('grpc.keepalive_permit_without_calls', True),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.http2.min_time_between_pings_ms', 10000),
                ('grpc.http2.min_ping_interval_without_data_ms', 300000)
            ]
        )
        
        # Create enhanced service
        enhanced_service = CompleteEnhancedTenantParserService()
        
        # Add service to server
        pb_grpc.add_IntentParserServiceServicer_to_server(enhanced_service, server)
        
        # Configure server address
        listen_addr = '0.0.0.0:5012'
        server.add_insecure_port(listen_addr)
        
        # Log available methods
        available_methods = [method for method in dir(enhanced_service) if not method.startswith('_')]
        confidence_methods = [m for m in available_methods if any(keyword in m for keyword in ['Confidence', 'Decision', 'Faq', 'Deflection'])]
        logger.info(f"Enhanced methods available: {confidence_methods}")
        
        # Start server
        await server.start()
        logger.info(f"Complete enhanced tenant_parser gRPC server started on {listen_addr}")
        logger.info("Confidence engine methods: CalculateConfidence, MakeDecision, ExtractFaqAnswer, GetPoliteDeflection")
        
        # Keep server running
        await server.wait_for_termination()
        
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        raise


if __name__ == '__main__':
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Import futures for server
    from concurrent import futures
    
    try:
        # Run server
        asyncio.run(serve())
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
    except Exception as e:
        logger.error(f"Server failed: {e}")
        raise