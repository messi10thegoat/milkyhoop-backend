"""
RAG LLM gRPC Client - Complete Implementation
Handles contextual response generation with FAQ context via gRPC
"""
import grpc
from grpc import aio
import logging
from typing import Dict, Any, List, Optional
import sys

# Import proto files - FIXED: Use correct proto name
sys.path.append('/app/protos')
import ragllm_service_pb2 as pb
import ragllm_service_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)


class RAGLLMClient:
    """
    Complete gRPC client for RAG LLM service
    Handles narrative response generation with FAQ context
    """
    
    def __init__(self, host: str = "milkyhoop-dev-ragllm_service-1", port: int = 5000):
        """Initialize RAG LLM gRPC client"""
        self.endpoint = f"{host}:{port}"
        self.channel = None
        self.stub = None
        logger.info(f"RAGLLMClient initialized with endpoint: {self.endpoint}")
    
    async def _ensure_connection(self):
        """Ensure gRPC channel and stub are initialized"""
        if self.channel is None or self.stub is None:
            self.channel = aio.insecure_channel(self.endpoint)
            self.stub = pb_grpc.RagLlmServiceStub(self.channel)
            logger.debug(f"gRPC channel established to {self.endpoint}")
    
    async def generate_response(
        self,
        query: str,
        faq_context: List[Any],
        intelligence_level: str,
        tenant_id: str,
        model: Optional[str] = None
    ) -> str:
        """
        Generate contextual narrative response using LLM with FAQ context
        
        Args:
            query: Customer query text
            faq_context: List of relevant FAQ results for context
            intelligence_level: "synthesis" (Tier 2) or "deep" (Tier 3)
            tenant_id: Tenant identifier
            model: Optional specific model to use (gpt-3.5-turbo, gpt-4, etc.)
            
        Returns:
            Generated narrative response text
        """
        try:
            await self._ensure_connection()
            
            # Determine model based on intelligence level if not specified
            if model is None:
                model = "gpt-4" if intelligence_level == "deep" else "gpt-3.5-turbo"
            
            # Convert FAQ context to string format
            context_text = self._format_faq_context(faq_context)
            
            # Create request
            request = pb.GenerateAnswerRequest(
                question=query,
                tenant_id=tenant_id,
                mode="synthesis",
                faq_context=context_text,
                intelligence_level=intelligence_level,
                model=model
            )
            
            # Make gRPC call
            response = await self.stub.GenerateAnswer(request)
            
            logger.info(f"Generated response using {model} for {tenant_id}")
            return response.answer

        except grpc.RpcError as e:
            logger.error(f"gRPC GenerateAnswer failed: {e.code()} - {e.details()}")
            # Fallback to simple FAQ extraction if LLM fails
            return self._fallback_response(query, faq_context, tenant_id)
        except Exception as e:
            logger.error(f"Generate response error: {str(e)}")
            return self._fallback_response(query, faq_context, tenant_id)
    
    async def synthesize_with_context(
        self,
        query: str,
        faq_results: List[Any],
        tenant_id: str,
        synthesis_type: str = "friendly"
    ) -> Dict[str, Any]:
        """
        Synthesize response with specific synthesis type
        
        Args:
            query: Customer query text
            faq_results: FAQ search results
            tenant_id: Tenant identifier
            synthesis_type: Type of synthesis ("friendly", "professional", "concise")
            
        Returns:
            Dict with synthesized response and metadata
        """
        try:
            await self._ensure_connection()
            
            context = self._format_faq_context(faq_results)
            
            request = pb.SynthesizeRequest(
                query=query,
                faq_context=context,
                synthesis_type=synthesis_type
            )
            
            response = await self.stub.Synthesize(request)
            
            return {
                "response": response.synthesized_text,
                "model_used": response.model_used,
                "context_used": True,
                "confidence": response.confidence,
                "synthesis_type": synthesis_type
            }
            
        except grpc.RpcError as e:
            logger.error(f"gRPC SynthesizeResponse failed: {e.code()} - {e.details()}")
            # Fallback
            fallback_text = self._fallback_response(query, faq_results, tenant_id)
            return {
                "response": fallback_text,
                "model_used": "fallback",
                "context_used": len(faq_results) > 0,
                "confidence": 0.5,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Synthesize error: {str(e)}")
            fallback_text = self._fallback_response(query, faq_results, tenant_id)
            return {
                "response": fallback_text,
                "model_used": "fallback",
                "context_used": False,
                "confidence": 0.0,
                "error": str(e)
            }
    
    def _format_faq_context(self, faq_results: List[Any]) -> str:
        """
        Format FAQ results into context string for LLM
        
        Args:
            faq_results: List of FAQ objects
            
        Returns:
            Formatted context string
        """
        if not faq_results:
            return ""
        
        context_parts = []
        for i, faq in enumerate(faq_results[:5], 1):  # Use 5 FAQs
            question = getattr(faq, 'question', '')
            answer = getattr(faq, 'answer', '')
            
            # Skip empty FAQs
            if not question and not answer:
                continue
                
            # Format with full content
            faq_text = f"Q: {question}\nA: {answer}"
            context_parts.append(faq_text)

        return "\n\n".join(context_parts)
    
    def _fallback_response(
        self,
        query: str,
        faq_context: List[Any],
        tenant_id: str
    ) -> str:
        """
        Generate fallback response when LLM fails
        
        Args:
            query: Customer query
            faq_context: FAQ results
            tenant_id: Tenant identifier
            
        Returns:
            Fallback response text
        """
        if faq_context and len(faq_context) > 0:
            # Extract answer from first FAQ
            first_faq = faq_context[0]
            answer = getattr(first_faq, 'answer', '')
            content = getattr(first_faq, 'content', '')
            
            response_text = content if content else answer
            
            if response_text:
                return response_text
        
        # Generic fallback if no FAQ available
        return f"Maaf, saya sedang mengalami kendala teknis untuk menjawab pertanyaan Anda tentang {tenant_id}. Silakan coba lagi dalam beberapa saat atau hubungi customer service kami untuk bantuan lebih lanjut."
    
    async def health_check(self) -> bool:
        """
        Check if RAG LLM service is healthy
        
        Returns:
            True if service is reachable and healthy
        """
        try:
            await self._ensure_connection()
            state = self.channel.get_state(try_to_connect=True)
            return state == grpc.ChannelConnectivity.READY
        except Exception as e:
            logger.error(f"RAG LLM health check failed: {str(e)}")
            return False
    
    async def close(self):
        """Close gRPC channel"""
        if self.channel:
            await self.channel.close()
            logger.info("RAGLLMClient channel closed")