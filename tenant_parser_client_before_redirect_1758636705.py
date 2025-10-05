"""
Enhanced Tenant Parser gRPC Client
Extended with confidence engine methods for migration
"""
import grpc
import asyncio
import logging
from typing import Dict, Any, Optional, List

# Import generated proto stubs
import tenant_parser_pb2 as pb
import tenant_parser_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)

class TenantParserClient:
    """Enhanced client for tenant parser service communication with confidence methods"""
    
    def __init__(self, host: str = "tenant_parser", port: int = 5012):
        self.endpoint = f"{host}:{port}"
        self.channel = None
        self.stub = None
        logger.info(f"Enhanced TenantParserClient configured for: {self.endpoint}")
    
    async def _ensure_connection(self):
        """Ensure gRPC connection is established"""
        if not self.channel:
            self.channel = grpc.aio.insecure_channel(self.endpoint)
            self.stub = pb_grpc.IntentParserServiceStub(self.channel)
            logger.debug(f"gRPC channel established to {self.endpoint}")

    async def close(self):
        """Close gRPC connection"""
        if self.channel:
            await self.channel.close()
            self.channel = None
            self.stub = None
            logger.debug("gRPC channel closed")

    # Legacy method - maintain compatibility
    async def parse_intent(self, message: str, tenant_id: str) -> Dict[str, Any]:
        """Legacy intent parsing method"""
        try:
            await self._ensure_connection()
            
            request = pb.IntentParserRequest()
            request.message = message
            request.tenant_id = tenant_id
            
            response = await self.stub.DoSomething(request)
            
            return {
                "status": response.status,
                "result": response.result,
                "intent": "customer_inquiry"  # Default fallback
            }
            
        except Exception as e:
            logger.error(f"Intent parsing failed: {e}")
            return {
                "status": "error",
                "result": str(e),
                "intent": "customer_inquiry"
            }

    # NEW CONFIDENCE ENGINE METHODS
    async def calculate_confidence(self, query: str, tenant_id: str, faq_results: List[Any]) -> pb.ConfidenceResponse:
        """Calculate confidence score using tenant_parser confidence engine"""
        try:
            await self._ensure_connection()
            
            # Build confidence request
            request = pb.ConfidenceRequest()
            request.query = query
            request.tenant_id = tenant_id
            
            # Convert FAQ results to proto format
            for faq in faq_results:
                proto_faq = request.faq_results.add()
                
                # Safe attribute access with defaults
                proto_faq.question = getattr(faq, 'question', '')
                proto_faq.answer = getattr(faq, 'answer', '')
                proto_faq.content = getattr(faq, 'content', f"{proto_faq.question} {proto_faq.answer}")
                proto_faq.similarity_score = float(getattr(faq, 'similarity_score', 0.0))
                proto_faq.score = float(getattr(faq, 'score', 0.0))
            
            # Make gRPC call
            response = await self.stub.CalculateConfidence(request)
            logger.info(f"Confidence calculated: {response.confidence:.3f} (Tier {response.tier_number})")
            
            return response
            
        except Exception as e:
            logger.error(f"Confidence calculation failed: {e}")
            raise

    async def make_decision(self, confidence: float) -> pb.DecisionResponse:
        """Make tier decision based on confidence score"""
        try:
            await self._ensure_connection()
            
            # Build decision request
            request = pb.DecisionRequest()
            request.confidence = float(confidence)
            
            # Make gRPC call
            response = await self.stub.MakeDecision(request)
            logger.info(f"Decision made: Tier {response.tier_number} ({response.intelligence_level})")
            
            return response
            
        except Exception as e:
            logger.error(f"Decision making failed: {e}")
            raise

    async def extract_faq_answer(self, faq_results: List[Any], tenant_id: str) -> pb.FaqExtractionResponse:
        """Extract direct FAQ answer for high confidence matches"""
        try:
            await self._ensure_connection()
            
            # Build extraction request
            request = pb.FaqExtractionRequest()
            request.tenant_id = tenant_id
            
            # Convert FAQ results
            for faq in faq_results:
                proto_faq = request.faq_results.add()
                proto_faq.question = getattr(faq, 'question', '')
                proto_faq.answer = getattr(faq, 'answer', '')
                proto_faq.content = getattr(faq, 'content', f"{proto_faq.question} {proto_faq.answer}")
                proto_faq.similarity_score = float(getattr(faq, 'similarity_score', 0.0))
                proto_faq.score = float(getattr(faq, 'score', 0.0))
            
            # Make gRPC call
            response = await self.stub.ExtractFaqAnswer(request)
            logger.info(f"FAQ answer extracted: {response.confidence:.3f}")
            
            return response
            
        except Exception as e:
            logger.error(f"FAQ answer extraction failed: {e}")
            raise

    async def get_polite_deflection(self, tenant_id: str) -> pb.DeflectionResponse:
        """Get polite deflection message for out-of-scope queries"""
        try:
            await self._ensure_connection()
            
            # Build deflection request
            request = pb.DeflectionRequest()
            request.tenant_id = tenant_id
            
            # Make gRPC call
            response = await self.stub.GetPoliteDeflection(request)
            logger.info(f"Polite deflection generated for tenant: {tenant_id}")
            
            return response
            
        except Exception as e:
            logger.error(f"Polite deflection failed: {e}")
            raise

    # Health check method
    async def health_check(self) -> bool:
        """Check if tenant_parser service is healthy"""
        try:
            await self._ensure_connection()
            
            from google.protobuf import empty_pb2
            request = empty_pb2.Empty()
            
            await self.stub.HealthCheck(request)
            logger.debug("tenant_parser health check: OK")
            return True
            
        except Exception as e:
            logger.warning(f"tenant_parser health check failed: {e}")
            return False


# Alias for backward compatibility tests
class EnhancedTenantParserClient(TenantParserClient):
    """Alias class for backward compatibility with test expectations"""
    pass
