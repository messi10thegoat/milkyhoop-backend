"""
Customer Service Orchestrator - Production Implementation (Fixed)
Compatible method signatures for existing RAG service clients
"""
import asyncio
import logging
import time
from typing import Dict, Any, Optional, List
import grpc
from app.clients.tenant_parser_client import TenantParserClient
from app.clients.ragcrud_client import RAGCRUDClient
from app.clients.ragllm_client import RAGLLMClient

logger = logging.getLogger(__name__)

class CustomerServiceOrchestrator:
    """
    Production-ready orchestrator with compatible method signatures
    
    Method signatures matched to actual client implementations:
    - TenantParserClient.classify_intent(query, tenant_id, session_id=None)
    - RAGCRUDClient.search_faq(query, tenant_id, intent='general_inquiry')
    - RAGLLMClient.generate_response(query, context, tenant_id, model='gpt-3.5-turbo')
    """
    
    def __init__(self):
        """Initialize service clients with connection pooling"""
        try:
            self.tenant_parser = TenantParserClient()
            self.ragcrud = RAGCRUDClient()
            self.ragllm = RAGLLMClient()
            
            # Configuration
            self.high_confidence_threshold = 0.75
            self.medium_confidence_threshold = 0.40
            self.max_processing_time = 10.0  # seconds
            self.enable_parallel_processing = True
            
            logger.info("CustomerServiceOrchestrator initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize CustomerServiceOrchestrator: {str(e)}")
            raise
    
    async def process_customer_query(
        self, 
        query: str, 
        tenant_id: str, 
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process customer query through complete orchestration pipeline
        
        Args:
            query: Customer query message
            tenant_id: Tenant identifier for context
            session_id: Optional session identifier for tracking
            
        Returns:
            Dict containing orchestrated response with metadata
        """
        start_time = time.time()
        processing_steps = []
        
        try:
            logger.info(f"[{session_id}] Processing query for tenant {tenant_id}: {query[:50]}...")
            
            # Step 1: Intent classification and entity extraction
            processing_steps.append("intent_classification")
            logger.info(f"[{session_id}] Step 1: Intent classification via Tenant Parser")
            
            intent_result = await self._classify_intent(query, tenant_id, session_id)
            
            confidence = intent_result.get('confidence', 0.0)
            intent = intent_result.get('intent', 'general_inquiry')
            entities = intent_result.get('entities', {})
            
            logger.info(f"[{session_id}] Intent: {intent}, Confidence: {confidence:.3f}")
            
            # Step 2: Knowledge retrieval with compatible parameters
            processing_steps.append("knowledge_retrieval")
            logger.info(f"[{session_id}] Step 2: Knowledge retrieval via RAG CRUD")
            
            faq_result = await self._retrieve_knowledge(query, tenant_id, intent, session_id)
            
            faq_confidence = faq_result.get('confidence', 0.0)
            faq_context = faq_result.get('context', '')
            direct_answer = faq_result.get('direct_answer', '')
            
            logger.info(f"[{session_id}] Knowledge retrieval completed, FAQ confidence: {faq_confidence:.3f}")
            
            # Step 3: Response generation with compatible parameters
            processing_steps.append("response_generation")
            logger.info(f"[{session_id}] Step 3: Response generation")
            
            response_data = await self._generate_response(
                query=query,
                tenant_id=tenant_id,
                session_id=session_id,
                intent=intent,
                confidence=max(confidence, faq_confidence),
                faq_context=faq_context,
                direct_answer=direct_answer
            )
            
            # Calculate processing time
            processing_time = (time.time() - start_time) * 1000  # milliseconds
            
            # Prepare final orchestrated response
            result = {
                "status": "success",
                "response": response_data["response"],
                "response_type": response_data["response_type"],
                "confidence": max(confidence, faq_confidence),
                "intent": intent,
                "entities": entities,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "processing_time_ms": round(processing_time, 2),
                "processing_steps": processing_steps,
                "metadata": {
                    "intent_confidence": confidence,
                    "faq_confidence": faq_confidence,
                    "model_used": response_data.get("model_used", "none"),
                    "context_length": len(faq_context),
                    "orchestrator_version": "3.0.1"
                }
            }
            
            logger.info(f"[{session_id}] Query processed successfully in {processing_time:.1f}ms, type: {response_data['response_type']}")
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"[{session_id}] Processing timeout after {self.max_processing_time}s")
            return self._create_error_response(
                "Processing timeout. Please try again.",
                tenant_id, session_id, processing_steps, time.time() - start_time
            )
            
        except Exception as e:
            logger.error(f"[{session_id}] Error processing customer query: {str(e)}")
            return self._create_error_response(
                "I apologize, but I'm experiencing technical difficulties. Please try again in a moment.",
                tenant_id, session_id, processing_steps, time.time() - start_time, str(e)
            )
    
    async def _classify_intent(
        self, 
        query: str, 
        tenant_id: str, 
        session_id: str
    ) -> Dict[str, Any]:
        """
        Classify intent via Tenant Parser (compatible signature)
        """
        try:
            # Call with compatible signature: (query, tenant_id, session_id)
            intent_result = await asyncio.wait_for(
                self.tenant_parser.classify_intent(
                    query=query,
                    tenant_id=tenant_id,
                    session_id=session_id
                ),
                timeout=3.0
            )
            
            return intent_result
            
        except asyncio.TimeoutError:
            logger.warning(f"[{session_id}] Tenant parser timeout, using fallback")
            return {
                "intent": "general_inquiry",
                "confidence": 0.3,
                "entities": {},
                "source": "fallback"
            }
        except Exception as e:
            logger.error(f"[{session_id}] Tenant parser error: {str(e)}")
            return {
                "intent": "general_inquiry", 
                "confidence": 0.2,
                "entities": {},
                "error": str(e)
            }
    
    async def _retrieve_knowledge(
        self,
        query: str,
        tenant_id: str, 
        intent: str,
        session_id: str
    ) -> Dict[str, Any]:
        """
        Retrieve knowledge via RAG CRUD (compatible signature)
        """
        try:
            # Call with compatible signature: (query, tenant_id, intent)
            faq_result = await asyncio.wait_for(
                self.ragcrud.search_faq(
                    query=query,
                    tenant_id=tenant_id,
                    intent=intent
                ),
                timeout=4.0
            )
            
            return faq_result
            
        except asyncio.TimeoutError:
            logger.warning(f"[{session_id}] RAG CRUD timeout, proceeding without context")
            return {
                "context": "",
                "confidence": 0.0,
                "direct_answer": "",
                "source": "timeout"
            }
        except Exception as e:
            logger.error(f"[{session_id}] RAG CRUD error: {str(e)}")
            return {
                "context": "",
                "confidence": 0.0, 
                "direct_answer": "",
                "error": str(e)
            }
    
    async def _generate_response(
        self,
        query: str,
        tenant_id: str,
        session_id: str,
        intent: str,
        confidence: float,
        faq_context: str,
        direct_answer: str
    ) -> Dict[str, Any]:
        """
        Generate response with compatible method signatures
        """
        try:
            # High confidence: Direct FAQ response (skip LLM for efficiency)
            if confidence >= self.high_confidence_threshold and direct_answer:
                logger.info(f"[{session_id}] High confidence ({confidence:.3f}): Using direct FAQ answer")
                return {
                    "response": direct_answer,
                    "response_type": "direct_faq",
                    "model_used": "none"
                }
            
            # Medium/Low confidence: Use LLM with context
            elif confidence >= self.medium_confidence_threshold and faq_context:
                # Choose model based on confidence
                model = "gpt-3.5-turbo" if confidence >= 0.6 else "gpt-4"
                logger.info(f"[{session_id}] Medium confidence ({confidence:.3f}): Using {model} with context")
                
                # Call with compatible signature: (query, context, tenant_id, model)
                llm_result = await asyncio.wait_for(
                    self.ragllm.generate_response(
                        query=query,
                        context=faq_context,
                        tenant_id=tenant_id,
                        model=model
                    ),
                    timeout=5.0
                )
                
                return {
                    "response": llm_result.get('response', 'I apologize, but I encountered an issue generating a response.'),
                    "response_type": "llm_contextualized",
                    "model_used": model
                }
            
            # Very low confidence or no context: Polite deflection
            else:
                logger.info(f"[{session_id}] Low confidence ({confidence:.3f}): Using deflection response")
                deflection_response = self._create_deflection_response(query, intent, tenant_id)
                return {
                    "response": deflection_response,
                    "response_type": "polite_deflection", 
                    "model_used": "none"
                }
                
        except asyncio.TimeoutError:
            logger.warning(f"[{session_id}] LLM timeout, using fallback response")
            return {
                "response": "I understand your question, but I'm taking longer than usual to process it. Could you please try rephrasing your question?",
                "response_type": "timeout_fallback",
                "model_used": "fallback"
            }
        except Exception as e:
            logger.error(f"[{session_id}] Response generation error: {str(e)}")
            return {
                "response": "I apologize, but I encountered an issue while processing your question. Please try again.",
                "response_type": "error_fallback",
                "model_used": "fallback"
            }
    
    def _create_deflection_response(self, query: str, intent: str, tenant_id: str) -> str:
        """
        Create contextual deflection response based on intent
        """
        deflection_templates = {
            "faq_query": f"I understand you're looking for information about {query}, but I don't have enough details in my knowledge base to provide a complete answer. Could you please provide more specific details or try rephrasing your question?",
            "product_inquiry": f"I'd be happy to help you with product information, but I need more specific details about what you're looking for regarding '{query}'. Could you please be more specific?",
            "complaint": f"I understand you have a concern about {query}. For the best assistance with your specific situation, I recommend contacting our support team directly who can provide personalized help.",
            "general_inquiry": f"I understand you're asking about {query}, but I don't have enough information to provide a helpful answer. Could you please provide more details or rephrase your question?"
        }
        
        return deflection_templates.get(intent, deflection_templates["general_inquiry"])
    
    def _create_error_response(
        self,
        message: str,
        tenant_id: str,
        session_id: str,
        processing_steps: List[str],
        processing_time: float,
        error_details: str = None
    ) -> Dict[str, Any]:
        """
        Create standardized error response
        """
        return {
            "status": "error",
            "response": message,
            "response_type": "system_error",
            "tenant_id": tenant_id,
            "session_id": session_id,
            "processing_time_ms": round(processing_time * 1000, 2),
            "processing_steps": processing_steps,
            "error_details": error_details,
            "metadata": {
                "orchestrator_version": "3.0.1",
                "error_timestamp": time.time()
            }
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Comprehensive health check for all orchestrator components
        """
        try:
            health_results = {}
            
            # Check tenant parser
            try:
                # Use compatible method call for health check
                test_result = await asyncio.wait_for(
                    self.tenant_parser.classify_intent("test", "test"), 
                    timeout=2.0
                )
                health_results["tenant_parser"] = "healthy"
            except:
                health_results["tenant_parser"] = "unhealthy"
            
            # Check RAG CRUD
            try:
                test_result = await asyncio.wait_for(
                    self.ragcrud.search_faq("test", "test"), 
                    timeout=2.0
                )
                health_results["ragcrud"] = "healthy"
            except:
                health_results["ragcrud"] = "unhealthy"
            
            # Check RAG LLM
            try:
                test_result = await asyncio.wait_for(
                    self.ragllm.generate_response("test", "test", "test"), 
                    timeout=2.0
                )
                health_results["ragllm"] = "healthy"
            except:
                health_results["ragllm"] = "unhealthy"
            
            # Overall health
            all_healthy = all(status == "healthy" for status in health_results.values())
            
            return {
                "status": "healthy" if all_healthy else "degraded",
                "services": health_results,
                "orchestrator": "healthy",
                "version": "3.0.1",
                "capabilities": [
                    "intent_classification",
                    "confidence_scoring", 
                    "knowledge_retrieval",
                    "response_generation",
                    "confidence_based_routing"
                ]
            }
            
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "orchestrator": "error"
            }
