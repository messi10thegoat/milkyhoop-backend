"""
Enhanced Tenant Parser gRPC Server with Confidence Engine Integration
Implements proper Two-Entity System Separation per roadmap
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

# Import existing tenant parser functionality
from app.services.tenant_parser_service import TenantParserService

# Import proto definitions
from app import tenant_parser_pb2_grpc as pb_grpc
from app import tenant_parser_pb2 as pb

logger = logging.getLogger(__name__)

class EnhancedTenantParserService(pb_grpc.IntentParserServiceServicer):
    """
    Enhanced Tenant Parser implementing confidence engine methods
    Proper separation: Customer intent classification + confidence scoring
    """
    
    def __init__(self):
        # Initialize confidence engine
        self.confidence_engine = create_enhanced_confidence_engine()
        logger.info("Confidence engine initialized in tenant_parser")
        
        # Initialize existing tenant parser functionality
        self.tenant_parser = TenantParserService()
        logger.info("Tenant parser service initialized")
    
    # Legacy methods (maintain compatibility)
    async def DoSomething(self, request, context):
        """Legacy method - delegate to existing tenant parser"""
        return await self.tenant_parser.DoSomething(request, context)
    
    async def HealthCheck(self, request, context):
        """Health check with confidence engine status"""
        try:
            # Test confidence engine
            test_confidence = self.confidence_engine.calculate_super_confidence(
                "health check", [], "health_tenant"
            )
            
            # Delegate to existing health check
            response = await self.tenant_parser.HealthCheck(request, context)
            logger.info(f"Health check passed - confidence engine operational: {test_confidence}")
            return response
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            raise
    
    # Core confidence engine methods
    async def CalculateConfidence(self, request, context):
        """Calculate confidence score for customer query"""
        try:
            logger.info(f"CalculateConfidence called for tenant: {request.tenant_id}")
            
            # Convert proto FAQ results to expected format
            faq_results = []
            for proto_faq in request.faq_results:
                # Create mock FAQ object with expected attributes
                class MockFAQ:
                    def __init__(self, proto_faq):
                        self.question = proto_faq.question
                        self.answer = proto_faq.answer
                        self.content = proto_faq.content
                        self.similarity_score = proto_faq.similarity_score
                        self.score = proto_faq.score
                
                faq_results.append(MockFAQ(proto_faq))
            
            # Calculate confidence using engine
            confidence = self.confidence_engine.calculate_super_confidence(
                request.query, faq_results, request.tenant_id
            )
            
            # Get tier decision
            decision = self.confidence_engine.super_decision_engine(confidence)
            
            # Build response
            response = pb.ConfidenceResponse()
            response.confidence = confidence
            response.tier_name = f"TIER {decision['tier']}: {decision['intelligence_level'].title()}"
            response.route = decision['route']
            response.cost_per_query = decision['cost_per_query']
            response.intelligence_level = decision['intelligence_level']
            response.tier_number = decision['tier']
            response.api_call_required = decision['api_call']
            response.faq_count = decision['faq_count']
            response.model = decision.get('model', 'none')
            
            logger.info(f"Confidence calculated: {confidence:.3f}, Tier: {decision['tier']}")
            return response
            
        except Exception as e:
            logger.error(f"CalculateConfidence error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Confidence calculation failed: {str(e)}")
            raise
    
    async def MakeDecision(self, request, context):
        """Make tier routing decision based on confidence"""
        try:
            logger.info(f"MakeDecision called with confidence: {request.confidence}")
            
            # Get decision from confidence engine
            decision = self.confidence_engine.super_decision_engine(request.confidence)
            
            # Build response
            response = pb.DecisionResponse()
            response.route = decision['route']
            response.tier = decision['tier']
            response.api_call = decision['api_call']
            response.model = decision.get('model', 'none')
            response.cost_per_query = decision['cost_per_query']
            response.faq_count = decision['faq_count']
            response.intelligence_level = decision['intelligence_level']
            
            logger.info(f"Decision made: {decision['route']}, Tier: {decision['tier']}")
            return response
            
        except Exception as e:
            logger.error(f"MakeDecision error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Decision making failed: {str(e)}")
            raise
    
    async def ExtractFaqAnswer(self, request, context):
        """Extract answer from FAQ content"""
        try:
            logger.info("ExtractFaqAnswer called")
            
            # Extract answer using confidence engine
            extracted = self.confidence_engine.extract_faq_answer(request.faq_content)
            
            # Build response
            response = pb.FaqExtractionResponse()
            response.extracted_answer = extracted
            
            return response
            
        except Exception as e:
            logger.error(f"ExtractFaqAnswer error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"FAQ extraction failed: {str(e)}")
            raise
    
    async def GetPoliteDeflection(self, request, context):
        """Get polite deflection message for tenant"""
        try:
            logger.info(f"GetPoliteDeflection called for tenant: {request.tenant_id}")
            
            # Get deflection message using confidence engine
            deflection = self.confidence_engine.get_polite_deflection(request.tenant_id)
            
            # Build response
            response = pb.DeflectionResponse()
            response.deflection_message = deflection
            
            return response
            
        except Exception as e:
            logger.error(f"GetPoliteDeflection error: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Deflection generation failed: {str(e)}")
            raise

# Server setup function
async def serve():
    """Start enhanced tenant parser gRPC server"""
    server = aio.server()
    
    # Add enhanced service
    enhanced_service = EnhancedTenantParserService()
    pb_grpc.add_IntentParserServiceServicer_to_server(enhanced_service, server)
    
    # Bind to port
    listen_addr = '[::]:5012'
    server.add_insecure_port(listen_addr)
    
    logger.info("Enhanced Tenant Parser gRPC server starting on port 5012")
    logger.info("Available methods: CalculateConfidence, MakeDecision, ExtractFaqAnswer, GetPoliteDeflection")
    
    await server.start()
    await server.wait_for_termination()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Enhanced Tenant Parser Service with Confidence Engine")
    asyncio.run(serve())
