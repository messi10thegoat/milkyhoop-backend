"""
Enhanced Tenant Parser gRPC Client
Real implementation with confidence engine methods
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
    """Enhanced client for tenant parser service communication"""
    
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
    
    async def calculate_confidence(
        self, 
        query: str, 
        tenant_id: str,
        faq_results: List = None
    ) -> Dict[str, Any]:
        """
        Calculate confidence score via tenant_parser gRPC
        """
        try:
            await self._ensure_connection()
            
            # Build request
            request = pb.ConfidenceRequest()
            request.query = query
            request.tenant_id = tenant_id
            
            # Add FAQ results if provided
            if faq_results:
                for faq in faq_results[:4]:  # Limit to 4 FAQs
                    faq_proto = pb.FaqResult()
                    faq_proto.question = getattr(faq, 'question', '')
                    faq_proto.answer = getattr(faq, 'answer', '')
                    faq_proto.content = getattr(faq, 'content', str(faq))
                    faq_proto.similarity_score = getattr(faq, 'similarity_score', 0.0)
                    faq_proto.score = getattr(faq, 'score', 0.0)
                    request.faq_results.append(faq_proto)
            
            # Make gRPC call
            response = await self.stub.CalculateConfidence(request)
            
            # Convert to expected format
            result = {
                'confidence': response.confidence,
                'tier_name': response.tier_name,
                'route': response.route,
                'cost_per_query': response.cost_per_query,
                'intelligence_level': response.intelligence_level,
                'tier_number': response.tier_number,
                'api_call_required': response.api_call_required,
                'faq_count': response.faq_count,
                'model': response.model
            }
            
            logger.info(f"Confidence calculated via gRPC: {response.confidence:.3f}")
            return result
            
        except grpc.RpcError as e:
            logger.error(f"gRPC error in calculate_confidence: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in calculate_confidence: {e}")
            raise
    
    async def make_decision(self, confidence: float) -> Dict[str, Any]:
        """
        Make tier routing decision via tenant_parser gRPC
        """
        try:
            await self._ensure_connection()
            
            # Build request
            request = pb.DecisionRequest()
            request.confidence = confidence
            
            # Make gRPC call
            response = await self.stub.MakeDecision(request)
            
            # Convert to expected format
            result = {
                'route': response.route,
                'tier': response.tier,
                'api_call': response.api_call,
                'model': response.model,
                'cost_per_query': response.cost_per_query,
                'faq_count': response.faq_count,
                'intelligence_level': response.intelligence_level
            }
            
            logger.info(f"Decision made via gRPC: {response.route}")
            return result
            
        except grpc.RpcError as e:
            logger.error(f"gRPC error in make_decision: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in make_decision: {e}")
            raise
    
    async def extract_faq_answer(self, faq_content: str) -> str:
        """
        Extract FAQ answer via tenant_parser gRPC
        """
        try:
            await self._ensure_connection()
            
            # Build request
            request = pb.FaqExtractionRequest()
            request.faq_content = faq_content
            
            # Make gRPC call
            response = await self.stub.ExtractFaqAnswer(request)
            
            logger.debug("FAQ answer extracted via gRPC")
            return response.extracted_answer
            
        except grpc.RpcError as e:
            logger.error(f"gRPC error in extract_faq_answer: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in extract_faq_answer: {e}")
            raise
    
    async def get_polite_deflection(self, tenant_id: str) -> str:
        """
        Get polite deflection message via tenant_parser gRPC
        """
        try:
            await self._ensure_connection()
            
            # Build request
            request = pb.DeflectionRequest()
            request.tenant_id = tenant_id
            
            # Make gRPC call
            response = await self.stub.GetPoliteDeflection(request)
            
            logger.debug(f"Polite deflection retrieved for {tenant_id}")
            return response.deflection_message
            
        except grpc.RpcError as e:
            logger.error(f"gRPC error in get_polite_deflection: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in get_polite_deflection: {e}")
            raise
    
    async def close(self):
        """Close gRPC connection"""
        if self.channel:
            await self.channel.close()
            logger.debug("gRPC channel closed")
    
    # Legacy method for compatibility
    async def classify_intent(
        self, 
        query: str, 
        tenant_id: str, 
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Legacy method - now calls confidence calculation
        """
        result = await self.calculate_confidence(query, tenant_id)
        return {
            "intent": "customer_inquiry",
            "confidence": result['confidence'],
            "tenant_id": tenant_id,
            "session_id": session_id,
            "enhanced_query": query
        }
