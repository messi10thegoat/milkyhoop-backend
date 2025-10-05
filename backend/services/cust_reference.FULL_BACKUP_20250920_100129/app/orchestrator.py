"""
Customer Service Orchestrator Core Logic
Orchestrates customer queries through: Tenant Parser -> RAG CRUD -> RAG LLM -> Response
"""
import asyncio
import logging
from typing import Dict, Any, Optional
import grpc
from clients.tenant_parser_client import TenantParserClient
from clients.ragcrud_client import RAGCRUDClient
from clients.ragllm_client import RAGLLMClient

logger = logging.getLogger(__name__)

class CustomerServiceOrchestrator:
    """Main orchestrator for customer service pipeline"""
    
    def __init__(self):
        self.tenant_parser = TenantParserClient()
        self.ragcrud = RAGCRUDClient()
        self.ragllm = RAGLLMClient()
    
    async def process_customer_query(
        self, 
        query: str, 
        tenant_id: str, 
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process customer query through complete pipeline
        
        Flow: Tenant Parser (intent + confidence) -> RAG CRUD (FAQ search) -> RAG LLM (response generation)
        """
        try:
            logger.info(f"Processing query for tenant {tenant_id}: {query[:50]}...")
            
            # Step 1: Intent classification and confidence scoring
            logger.info("Step 1: Calling Tenant Parser for intent classification...")
            intent_result = await self.tenant_parser.classify_intent(
                query=query,
                tenant_id=tenant_id,
                session_id=session_id
            )
            
            confidence = intent_result.get('confidence', 0.0)
            intent = intent_result.get('intent', 'general_inquiry')
            logger.info(f"Intent: {intent}, Confidence: {confidence}")
            
            # Step 2: FAQ search and context retrieval
            logger.info("Step 2: Calling RAG CRUD for FAQ search...")
            faq_result = await self.ragcrud.search_faq(
                query=query,
                tenant_id=tenant_id,
                intent=intent
            )
            
            faq_context = faq_result.get('context', '')
            faq_confidence = faq_result.get('confidence', 0.0)
            logger.info(f"FAQ search completed, context length: {len(faq_context)}")
            
            # Step 3: Response generation based on confidence
            if confidence >= 0.75 and faq_result.get('direct_answer'):
                # High confidence: return direct FAQ answer
                logger.info("High confidence: Returning direct FAQ answer")
                response = faq_result['direct_answer']
                response_type = "direct_faq"
                
            elif confidence >= 0.4:
                # Medium/Low confidence: Use LLM with FAQ context
                logger.info("Medium/Low confidence: Calling RAG LLM for contextualized response...")
                llm_result = await self.ragllm.generate_response(
                    query=query,
                    context=faq_context,
                    tenant_id=tenant_id,
                    model="gpt-3.5-turbo" if confidence >= 0.4 else "gpt-4"
                )
                response = llm_result.get('response', 'I apologize, but I encountered an issue generating a response.')
                response_type = "llm_generated"
                
            else:
                # Very low confidence: Polite deflection
                logger.info("Very low confidence: Returning polite deflection")
                response = f"I understand you're asking about {query}, but I don't have enough information to provide a helpful answer. Could you please provide more details or rephrase your question?"
                response_type = "deflection"
            
            # Prepare final response
            result = {
                "status": "success",
                "response": response,
                "response_type": response_type,
                "confidence": confidence,
                "intent": intent,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "processing_time_ms": 0  # TODO: Add timing
            }
            
            logger.info(f"Query processed successfully, response type: {response_type}")
            return result
            
        except Exception as e:
            logger.error(f"Error processing customer query: {str(e)}")
            return {
                "status": "error",
                "response": "I apologize, but I'm experiencing technical difficulties. Please try again in a moment.",
                "error": str(e),
                "tenant_id": tenant_id,
                "session_id": session_id
            }
