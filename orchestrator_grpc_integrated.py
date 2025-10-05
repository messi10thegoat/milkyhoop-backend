"""
SuperIntelligent Customer Orchestrator - gRPC Integrated Version
Implements distributed architecture with gRPC confidence engine calls
"""
import asyncio
import logging
import time
import redis
import os
from typing import Dict, Any, Optional, List
import grpc
from app.clients.tenant_parser_client import TenantParserClient
from app.clients.ragcrud_client import RAGCRUDClient
from app.clients.ragllm_client import RAGLLMClient

logger = logging.getLogger(__name__)

class SuperIntelligentCustomerOrchestrator:
    """
    gRPC-Integrated SuperIntelligent Customer Orchestrator
    
    DISTRIBUTED ARCHITECTURE:
    - Confidence calculation via gRPC calls to tenant_parser service
    - 4-Tier routing based on distributed intelligence
    - Cost tracking and performance optimization
    - Fallback mechanisms for high availability
    """
    
    def __init__(self):
        """Initialize gRPC-integrated orchestrator with distributed architecture"""
        try:
            logger.info("Initializing gRPC-Integrated SuperIntelligent Customer Orchestrator...")
            
            # Initialize gRPC service clients (DISTRIBUTED ARCHITECTURE)
            self.tenant_parser = TenantParserClient()
            self.ragcrud = RAGCRUDClient()
            self.ragllm = RAGLLMClient()
            
            logger.info("gRPC clients initialized - distributed architecture active")
            
            # Initialize Redis for caching and cost tracking
            self.redis_client = self._initialize_redis()
            
            # Cost tracking and budget management
            self.daily_budget_limit = float(os.getenv('DAILY_BUDGET_LIMIT', '100000'))  # Rp 100k default
            
            logger.info("SuperIntelligentCustomerOrchestrator: FULLY OPERATIONAL (gRPC DISTRIBUTED)")
            
        except Exception as e:
            logger.error(f"Failed to initialize gRPC-integrated orchestrator: {str(e)}")
            raise
    
    def _initialize_redis(self):
        """Initialize Redis with production settings"""
        try:
            redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'redis'),
                port=int(os.getenv('REDIS_PORT', '6379')),
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            
            # Test connection
            redis_client.ping()
            logger.info("Redis connection: ESTABLISHED")
            return redis_client
            
        except Exception as e:
            logger.warning(f"Redis connection failed: {e} - Operating without cache")
            return None
    
    async def process_customer_query(
        self, 
        query: str, 
        tenant_id: str, 
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        gRPC-Integrated Customer Query Processing
        
        DISTRIBUTED FLOW:
        1. Cache check (if Redis available)
        2. FAQ retrieval via RAG CRUD
        3. gRPC confidence calculation via tenant_parser
        4. 4-Tier routing decision via gRPC
        5. Response generation based on distributed intelligence
        6. Cost tracking and caching
        """
        start_time = time.time()
        trace_id = session_id or f"trace_{int(time.time())}"
        
        try:
            logger.info(f"[{trace_id}] gRPC SuperIntelligent processing: '{query[:50]}...' for {tenant_id}")
            
            # Step 1: Check cache first (performance optimization)
            cached_response = self._get_cached_response(tenant_id, query)
            if cached_response:
                logger.info(f"[{trace_id}] Cache HIT - returning cached response")
                return cached_response
            
            # Step 2: Check daily budget before processing
            if self._is_budget_exceeded(tenant_id):
                logger.warning(f"[{trace_id}] Daily budget exceeded for {tenant_id}")
                return self._create_budget_exceeded_response(tenant_id, session_id)
            
            # Step 3: FAQ Knowledge Retrieval
            faq_results = await self._retrieve_faq_knowledge(query, tenant_id, trace_id)
            
            # Step 4: gRPC Distributed Confidence Calculation
            try:
                confidence_response = await self.tenant_parser.calculate_confidence(
                    query=query, 
                    tenant_id=tenant_id, 
                    faq_results=faq_results
                )
                
                confidence = confidence_response.confidence
                tier_number = confidence_response.tier_number
                route = confidence_response.route
                cost_per_query = confidence_response.cost_per_query
                api_call_required = confidence_response.api_call_required
                
                logger.info(f"[{trace_id}] gRPC Confidence: {confidence:.3f}, Tier {tier_number}, Route: {route}")
                logger.info(f"[{trace_id}] Distributed Intelligence Cost: Rp {cost_per_query}")
                
                # Track costs via gRPC response
                self._track_query_cost(tenant_id, cost_per_query)
                
            except Exception as grpc_error:
                logger.error(f"[{trace_id}] gRPC confidence call failed: {grpc_error}")
                # Fallback to deflection for high availability
                return await self._create_fallback_response(query, tenant_id, session_id, trace_id)
            
            # Step 5: Execute gRPC-based tier routing
            if tier_number == 1:
                # TIER 1: Direct FAQ Response (via gRPC)
                response_text = await self._handle_tier1_direct_faq(faq_results, tenant_id, trace_id)
                
            elif tier_number == 2:
                # TIER 2: GPT-3.5 Synthesis (API call required)
                response_text = await self._handle_tier2_gpt_synthesis(query, faq_results, tenant_id, trace_id)
                
            elif tier_number == 3:
                # TIER 3: Deep Understanding (GPT-4 with context)
                response_text = await self._handle_tier3_deep_understanding(query, faq_results, tenant_id, trace_id)
                
            else:
                # TIER 4: Polite Deflection (via gRPC)
                response_text = await self._handle_tier4_polite_deflection(tenant_id, trace_id)
            
            # Step 6: Build final response
            processing_time = (time.time() - start_time) * 1000
            
            result = {
                "status": "success",
                "tenant_id": tenant_id,
                "business_name": tenant_id,
                "response": response_text,
                "session_id": session_id,
                "trace_id": trace_id,
                "intent": "customer_inquiry",
                "confidence": confidence,
                "tier": tier_number,
                "route": route,
                "cost": cost_per_query,
                "processing_time_ms": round(processing_time, 1),
                "architecture": "gRPC_distributed"
            }
            
            # Cache successful response
            self._cache_response(tenant_id, query, result)
            
            logger.info(f"[{trace_id}] gRPC SuperIntelligent processing complete: {processing_time:.1f}ms")
            return result
            
        except Exception as e:
            logger.error(f"[{trace_id}] gRPC orchestrator processing failed: {str(e)}")
            return {
                "status": "error",
                "tenant_id": tenant_id,
                "response": "Maaf, terjadi kesalahan sistem. Silakan coba lagi dalam beberapa saat.",
                "session_id": session_id,
                "trace_id": trace_id,
                "error": str(e),
                "architecture": "gRPC_distributed"
            }
    
    async def _retrieve_faq_knowledge(self, query: str, tenant_id: str, trace_id: str) -> List[Any]:
        """Retrieve FAQ knowledge via RAG CRUD service"""
        try:
            faq_response = await self.ragcrud.search_faq(query, tenant_id, limit=5)
            
            if faq_response and "results" in faq_response:
                faq_results = faq_response["results"]
                logger.info(f"[{trace_id}] Retrieved {len(faq_results)} FAQ results")
                return faq_results
            else:
                logger.info(f"[{trace_id}] No FAQ results found")
                return []
                
        except Exception as e:
            logger.warning(f"[{trace_id}] FAQ retrieval failed: {e}")
            return []
    
    async def _handle_tier1_direct_faq(self, faq_results: List[Any], tenant_id: str, trace_id: str) -> str:
        """TIER 1: Direct FAQ response via gRPC"""
        try:
            faq_response = await self.tenant_parser.extract_faq_answer(faq_results, tenant_id)
            logger.info(f"[{trace_id}] TIER 1: Direct FAQ response via gRPC")
            return faq_response.answer
        except Exception as e:
            logger.error(f"[{trace_id}] TIER 1 gRPC call failed: {e}")
            # Fallback to first FAQ result
            if faq_results:
                return getattr(faq_results[0], 'answer', 'Informasi tersedia dalam FAQ.')
            return "Informasi FAQ tidak tersedia saat ini."
    
    async def _handle_tier2_gpt_synthesis(self, query: str, faq_results: List[Any], tenant_id: str, trace_id: str) -> str:
        """TIER 2: GPT-3.5 synthesis with FAQ context"""
        try:
            context = self._build_faq_context(faq_results)
            llm_response = await self.ragllm.generate_answer(
                query=query,
                tenant_id=tenant_id,
                context=context,
                model="gpt-3.5-turbo",
                max_tokens=150
            )
            logger.info(f"[{trace_id}] TIER 2: GPT-3.5 synthesis complete")
            return llm_response.get("answer", "Maaf, tidak dapat memberikan jawaban saat ini.")
        except Exception as e:
            logger.error(f"[{trace_id}] TIER 2 synthesis failed: {e}")
            return "Maaf, layanan sedang tidak tersedia. Silakan hubungi customer service."
    
    async def _handle_tier3_deep_understanding(self, query: str, faq_results: List[Any], tenant_id: str, trace_id: str) -> str:
        """TIER 3: Deep understanding with comprehensive context"""
        try:
            context = self._build_comprehensive_context(faq_results)
            llm_response = await self.ragllm.generate_answer(
                query=query,
                tenant_id=tenant_id,
                context=context,
                model="gpt-4",
                max_tokens=200
            )
            logger.info(f"[{trace_id}] TIER 3: Deep understanding complete")
            return llm_response.get("answer", "Maaf, tidak dapat memberikan jawaban saat ini.")
        except Exception as e:
            logger.error(f"[{trace_id}] TIER 3 deep understanding failed: {e}")
            return "Maaf, layanan sedang tidak tersedia. Silakan hubungi customer service."
    
    async def _handle_tier4_polite_deflection(self, tenant_id: str, trace_id: str) -> str:
        """TIER 4: Polite deflection via gRPC"""
        try:
            deflection_response = await self.tenant_parser.get_polite_deflection(tenant_id)
            logger.info(f"[{trace_id}] TIER 4: Polite deflection via gRPC")
            return deflection_response.message
        except Exception as e:
            logger.error(f"[{trace_id}] TIER 4 gRPC call failed: {e}")
            # Fallback deflection
            return f"Maaf, pertanyaan tersebut di luar cakupan layanan {tenant_id}. Silakan tanyakan hal lain yang dapat saya bantu."
    
    async def _create_fallback_response(self, query: str, tenant_id: str, session_id: str, trace_id: str) -> Dict[str, Any]:
        """Create fallback response when gRPC calls fail"""
        logger.warning(f"[{trace_id}] Using fallback response due to gRPC failure")
        
        return {
            "status": "success",
            "tenant_id": tenant_id,
            "business_name": tenant_id,
            "response": f"Maaf, sistem sedang dalam maintenance. Silakan coba lagi atau hubungi customer service {tenant_id}.",
            "session_id": session_id,
            "trace_id": trace_id,
            "intent": "system_fallback",
            "architecture": "gRPC_distributed_fallback"
        }
    
    def _build_faq_context(self, faq_results: List[Any]) -> str:
        """Build FAQ context for LLM"""
        if not faq_results:
            return ""
        
        context_parts = []
        for faq in faq_results[:2]:  # Top 2 for TIER 2
            question = getattr(faq, 'question', '')
            answer = getattr(faq, 'answer', '')
            if question and answer:
                context_parts.append(f"Q: {question}\nA: {answer}")
        
        return "\n\n".join(context_parts)
    
    def _build_comprehensive_context(self, faq_results: List[Any]) -> str:
        """Build comprehensive context for deep understanding"""
        if not faq_results:
            return ""
        
        context_parts = []
        for faq in faq_results[:4]:  # Top 4 for TIER 3
            question = getattr(faq, 'question', '')
            answer = getattr(faq, 'answer', '')
            content = getattr(faq, 'content', '')
            if question and answer:
                context_parts.append(f"Q: {question}\nA: {answer}\nContext: {content}")
        
        return "\n\n".join(context_parts)
    
    def _get_cached_response(self, tenant_id: str, query: str) -> Optional[Dict]:
        """Get cached response if available"""
        if not self.redis_client:
            return None
        
        try:
            cache_key = f"response:{tenant_id}:{hash(query.lower())}"
            cached_data = self.redis_client.get(cache_key)
            if cached_data:
                import json
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"Cache retrieval failed: {e}")
        
        return None
    
    def _cache_response(self, tenant_id: str, query: str, response_data: Dict):
        """Cache response for future use"""
        if not self.redis_client:
            return
        
        try:
            cache_key = f"response:{tenant_id}:{hash(query.lower())}"
            import json
            self.redis_client.setex(cache_key, 3600, json.dumps(response_data))  # 1 hour TTL
        except Exception as e:
            logger.warning(f"Response caching failed: {e}")
    
    def _track_query_cost(self, tenant_id: str, cost: float):
        """Track query cost for budget management"""
        if cost == 0.0:
            return
        
        if self.redis_client:
            try:
                today = time.strftime("%Y-%m-%d")
                cost_key = f"cost:{tenant_id}:{today}"
                self.redis_client.incrbyfloat(cost_key, cost)
                self.redis_client.expire(cost_key, 86400)  # 24 hours
            except Exception as e:
                logger.warning(f"Cost tracking failed: {e}")
    
    def _is_budget_exceeded(self, tenant_id: str) -> bool:
        """Check if daily budget is exceeded"""
        if not self.redis_client:
            return False
        
        try:
            today = time.strftime("%Y-%m-%d")
            cost_key = f"cost:{tenant_id}:{today}"
            daily_cost = float(self.redis_client.get(cost_key) or 0.0)
            return daily_cost >= self.daily_budget_limit
        except Exception:
            return False
    
    def _create_budget_exceeded_response(self, tenant_id: str, session_id: str) -> Dict[str, Any]:
        """Create response when budget is exceeded"""
        return {
            "status": "success",
            "tenant_id": tenant_id,
            "business_name": tenant_id,
            "response": f"Layanan {tenant_id} sedang mencapai batas harian. Silakan coba kembali besok atau hubungi customer service.",
            "session_id": session_id,
            "intent": "budget_exceeded",
            "architecture": "gRPC_distributed"
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """gRPC-integrated health check"""
        try:
            health_results = {}
            
            # Check gRPC tenant_parser service
            try:
                is_healthy = await self.tenant_parser.health_check()
                health_results["tenant_parser_grpc"] = "healthy" if is_healthy else "unhealthy"
            except Exception as e:
                health_results["tenant_parser_grpc"] = f"unhealthy: {e}"
            
            # Check Redis
            if self.redis_client:
                try:
                    self.redis_client.ping()
                    health_results["redis"] = "healthy"
                except Exception as e:
                    health_results["redis"] = f"unhealthy: {e}"
            else:
                health_results["redis"] = "not_configured"
            
            # Check RAG services
            try:
                ragcrud_health = await self.ragcrud.health_check()
                health_results["ragcrud"] = "healthy" if ragcrud_health else "unhealthy"
            except Exception as e:
                health_results["ragcrud"] = f"unhealthy: {e}"
            
            try:
                ragllm_health = await self.ragllm.health_check()
                health_results["ragllm"] = "healthy" if ragllm_health else "unhealthy"
            except Exception as e:
                health_results["ragllm"] = f"unhealthy: {e}"
            
            overall_healthy = all("healthy" in status or status == "not_configured" 
                                for status in health_results.values())
            
            return {
                "status": "healthy" if overall_healthy else "degraded",
                "architecture": "gRPC_distributed",
                "components": health_results
            }
            
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "architecture": "gRPC_distributed"
            }
