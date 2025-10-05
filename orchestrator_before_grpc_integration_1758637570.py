"""
SuperIntelligent Customer Service Orchestrator - Production Implementation
Integrates 4-Tier SuperIntelligent engine with complete orchestration pipeline
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

# Import SuperIntelligent Engine (same as tenant_parser)
import sys
sys.path.append('/app/backend/services/cust_orchestrator/app/services')
from enhanced_confidence_engine import create_enhanced_confidence_engine

logger = logging.getLogger(__name__)

class SuperIntelligentCustomerOrchestrator:
    """
    Production SuperIntelligent Customer Orchestrator
    
    Features:
    - 4-Tier SuperIntelligent confidence system
    - Cost-optimized API routing 
    - Redis-based caching and cost tracking
    - Production-ready error handling
    - Complete orchestration pipeline
    """
    
    def __init__(self):
        """Initialize SuperIntelligent orchestrator with all components"""
        try:
            # Initialize SuperIntelligent Engine (CRITICAL ADDITION)
            self.superintelligent_engine = create_enhanced_confidence_engine()
            logger.info("SuperIntelligent 4-Tier Engine: INITIALIZED")
            
            # Initialize service clients
            self.tenant_parser = TenantParserClient()
            self.ragcrud = RAGCRUDClient()
            self.ragllm = RAGLLMClient()
            
            # Initialize Redis for caching and cost tracking
            self.redis_client = self._initialize_redis()
            
            # Configuration (aligned with SuperIntelligent thresholds)
            self.tier_1_threshold = 0.85    # Direct FAQ (no API cost)
            self.tier_2_threshold = 0.60    # GPT Synthesis (medium cost)
            self.tier_3_threshold = 0.30    # Deep Understanding (high cost)
            # Below 0.30 = Tier 4 (Polite deflection, no cost)
            
            self.daily_budget_limit = 100000  # Rp 100k per tenant per day
            self.cache_ttl = 3600  # 1 hour cache
            self.max_processing_time = 15.0  # seconds
            
            logger.info("SuperIntelligentCustomerOrchestrator: FULLY OPERATIONAL")
            
        except Exception as e:
            logger.error(f"Failed to initialize SuperIntelligentCustomerOrchestrator: {str(e)}")
            raise
    
    def _initialize_redis(self):
        """Initialize Redis with production settings"""
        try:
            redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'redis'),
                port=int(os.getenv('REDIS_PORT', '6379')),
                password='MilkyRedis2025Secure',
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            redis_client.ping()
            logger.info("Redis connection: ESTABLISHED")
            return redis_client
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}. Using in-memory fallback.")
            return None
    
    async def process_customer_query(
        self, 
        query: str, 
        tenant_id: str, 
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        SuperIntelligent Customer Query Processing
        
        Flow:
        1. Cache check (if Redis available)
        2. FAQ retrieval via RAG CRUD
        3. SuperIntelligent confidence calculation
        4. 4-Tier routing decision
        5. Response generation based on tier
        6. Cost tracking and caching
        """
        start_time = time.time()
        trace_id = session_id or f"trace_{int(time.time())}"
        
        try:
            logger.info(f"[{trace_id}] SuperIntelligent processing: '{query[:50]}...' for {tenant_id}")
            
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
            
            # Step 4: SuperIntelligent Confidence Calculation
            confidence = self.superintelligent_engine.calculate_super_confidence(
                query, faq_results, tenant_id
            )
            
            # Step 5: 4-Tier Decision Engine
            decision = self.superintelligent_engine.super_decision_engine(confidence)
            
            logger.info(f"[{trace_id}] SuperIntelligent Decision: TIER {decision['tier']} - {decision['route']}")
            logger.info(f"[{trace_id}] Confidence: {confidence:.3f}, Cost: Rp {decision['cost_per_query']}")
            
            # Step 6: Execute tier-based routing
            response_data = await self._execute_tier_routing(
                decision, query, tenant_id, trace_id, faq_results, confidence
            )
            
            # Step 7: Track costs and cache response
            self._track_costs(tenant_id, decision['cost_per_query'])
            self._cache_response(tenant_id, query, response_data)
            
            # Step 8: Assemble final response
            processing_time = (time.time() - start_time) * 1000
            
            result = {
                "status": "success",
                "response": response_data["response"],
                "tenant_id": tenant_id,
                "session_id": session_id,
                "trace_id": trace_id,
                "intent": "customer_inquiry",
                "superintelligent_metadata": {
                    "confidence": confidence,
                    "tier": decision["tier"],
                    "route": decision["route"],
                    "api_call_made": decision["api_call"],
                    "cost_rp": decision["cost_per_query"],
                    "intelligence_level": decision["intelligence_level"],
                    "processing_time_ms": round(processing_time, 2),
                    "faq_count_used": decision["faq_count"],
                    "model_used": decision.get("model", "none")
                }
            }
            
            logger.info(f"[{trace_id}] SuperIntelligent processing complete: {processing_time:.1f}ms")
            return result
            
        except Exception as e:
            logger.error(f"[{trace_id}] SuperIntelligent processing error: {str(e)}")
            return self._create_error_response(str(e), tenant_id, session_id, time.time() - start_time)
    
    async def _retrieve_faq_knowledge(self, query: str, tenant_id: str, trace_id: str) -> List:
        """Retrieve FAQ knowledge with timeout and error handling"""
        try:
            faq_result = await asyncio.wait_for(
                self.ragcrud.search_faq(query, tenant_id, intent="customer_inquiry"),
                timeout=4.0
            )
            
            # Convert to format expected by SuperIntelligent engine
            if isinstance(faq_result, dict) and 'results' in faq_result:
                return faq_result['results']
            elif isinstance(faq_result, list):
                return faq_result
            else:
                return []
                
        except asyncio.TimeoutError:
            logger.warning(f"[{trace_id}] FAQ retrieval timeout")
            return []
        except Exception as e:
            logger.error(f"[{trace_id}] FAQ retrieval error: {e}")
            return []
    
    async def _execute_tier_routing(
        self, 
        decision: Dict, 
        query: str, 
        tenant_id: str, 
        trace_id: str, 
        faq_results: List,
        confidence: float
    ) -> Dict[str, Any]:
        """Execute routing based on SuperIntelligent tier decision"""
        
        if decision["route"] == "direct_faq_fallback":
            # TIER 1: Direct FAQ Response (No API cost)
            if faq_results and len(faq_results) > 0:
                response = self.superintelligent_engine.extract_faq_answer(faq_results[0].content)
                logger.info(f"[{trace_id}] TIER 1: Direct FAQ response (Cost: Rp 0)")
            else:
                response = f"Maaf, informasi untuk {tenant_id} sedang tidak tersedia saat ini."
            
            return {
                "response": response,
                "response_type": "direct_faq",
                "api_cost": 0.0
            }
        
        elif decision["route"] == "gpt_synthesis" or decision["route"] == "deep_understanding":
            # TIER 2 & 3: GPT-based responses with FAQ context
            try:
                intelligence_level = "synthesis" if decision["route"] == "gpt_synthesis" else "deep"
                
                response = await asyncio.wait_for(
                    self.ragllm.generate_response(
                        query, faq_results, intelligence_level, tenant_id
                    ),
                    timeout=8.0
                )
                
                tier_num = 2 if decision["route"] == "gpt_synthesis" else 3
                logger.info(f"[{trace_id}] TIER {tier_num}: GPT synthesis complete (Cost: Rp {decision['cost_per_query']})")
                
                return {
                    "response": response,
                    "response_type": f"gpt_{intelligence_level}",
                    "api_cost": decision['cost_per_query']
                }
                
            except asyncio.TimeoutError:
                logger.warning(f"[{trace_id}] GPT synthesis timeout, fallback to FAQ")
                if faq_results:
                    response = self.superintelligent_engine.extract_faq_answer(faq_results[0].content)
                else:
                    response = f"Maaf, saya membutuhkan waktu lebih lama untuk memproses pertanyaan Anda. Bisa coba lagi?"
                
                return {
                    "response": response,
                    "response_type": "timeout_fallback",
                    "api_cost": 0.0
                }
            
            except Exception as e:
                logger.error(f"[{trace_id}] GPT synthesis error: {e}")
                # Fallback to direct FAQ if available
                if faq_results:
                    response = self.superintelligent_engine.extract_faq_answer(faq_results[0].content)
                else:
                    response = f"Maaf, saya mengalami kendala teknis. Silakan coba lagi sebentar."
                
                return {
                    "response": response,
                    "response_type": "error_fallback", 
                    "api_cost": 0.0
                }
        
        else:
            # TIER 4: Polite Deflection (No API cost)
            response = self.superintelligent_engine.get_polite_deflection(tenant_id)
            logger.info(f"[{trace_id}] TIER 4: Polite deflection (Cost: Rp 0)")
            
            return {
                "response": response,
                "response_type": "polite_deflection",
                "api_cost": 0.0
            }
    
    def _get_cached_response(self, tenant_id: str, query: str) -> Optional[Dict]:
        """Get cached response if available"""
        if not self.redis_client:
            return None
        
        try:
            cache_key = self.superintelligent_engine.get_cache_key(tenant_id, query)
            cached = self.redis_client.get(cache_key)
            
            if cached:
                import json
                return json.loads(cached)
                
        except Exception as e:
            logger.warning(f"Cache retrieval error: {e}")
        
        return None
    
    def _cache_response(self, tenant_id: str, query: str, response_data: Dict):
        """Cache response for future use"""
        if not self.redis_client:
            return
        
        try:
            cache_key = self.superintelligent_engine.get_cache_key(tenant_id, query)
            import json
            self.redis_client.setex(cache_key, self.cache_ttl, json.dumps(response_data))
            
        except Exception as e:
            logger.warning(f"Cache storage error: {e}")
    
    def _track_costs(self, tenant_id: str, cost: float):
        """Track daily costs per tenant"""
        if cost == 0.0:
            return
        
        if self.redis_client:
            try:
                cost_key = f"daily_cost:{tenant_id}"
                self.redis_client.incrbyfloat(cost_key, cost)
                # Set expiry to end of day
                import datetime
                tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
                midnight = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
                expire_seconds = int((midnight - datetime.datetime.now()).total_seconds())
                self.redis_client.expire(cost_key, expire_seconds)
                
            except Exception as e:
                logger.warning(f"Cost tracking error: {e}")
        else:
            # In-memory tracking fallback
            self.superintelligent_engine.track_intelligence_cost(tenant_id, cost)
    
    def _is_budget_exceeded(self, tenant_id: str) -> bool:
        """Check if daily budget is exceeded"""
        if self.redis_client:
            try:
                cost_key = f"daily_cost:{tenant_id}"
                current_cost = float(self.redis_client.get(cost_key) or 0)
                return current_cost > self.daily_budget_limit
            except:
                return False
        else:
            return self.superintelligent_engine.check_daily_budget(tenant_id)
    
    def _create_budget_exceeded_response(self, tenant_id: str, session_id: str) -> Dict[str, Any]:
        """Create response when daily budget is exceeded"""
        return {
            "status": "success",
            "response": f"Maaf, layanan {tenant_id} telah mencapai batas penggunaan harian. Silakan coba lagi besok.",
            "tenant_id": tenant_id,
            "session_id": session_id,
            "superintelligent_metadata": {
                "tier": 4,
                "route": "budget_exceeded",
                "cost_rp": 0.0,
                "intelligence_level": "budget_protection"
            }
        }
    
    def _create_error_response(
        self, 
        error_msg: str, 
        tenant_id: str, 
        session_id: str, 
        processing_time: float
    ) -> Dict[str, Any]:
        """Create standardized error response"""
        return {
            "status": "error",
            "response": "Maaf, saya mengalami kendala teknis. Silakan coba lagi dalam beberapa saat.",
            "tenant_id": tenant_id,
            "session_id": session_id,
            "superintelligent_metadata": {
                "tier": 4,
                "route": "system_error",
                "cost_rp": 0.0,
                "processing_time_ms": round(processing_time * 1000, 2),
                "error_details": error_msg
            }
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Comprehensive SuperIntelligent orchestrator health check"""
        try:
            health_results = {}
            
            # Check SuperIntelligent Engine
            try:
                test_confidence = self.superintelligent_engine.calculate_super_confidence(
                    "test query", [], "test_tenant"
                )
                health_results["superintelligent_engine"] = "healthy"
            except Exception as e:
                health_results["superintelligent_engine"] = f"unhealthy: {e}"
            
            # Check Redis
            if self.redis_client:
                try:
                    self.redis_client.ping()
                    health_results["redis_cache"] = "healthy"
                except:
                    health_results["redis_cache"] = "unhealthy"
            else:
                health_results["redis_cache"] = "unavailable (using fallback)"
            
            # Check service clients
            try:
                # Quick timeout test for each service
                await asyncio.wait_for(
                    self.tenant_parser.classify_intent("test", "test"), 
                    timeout=1.0
                )
                health_results["tenant_parser"] = "healthy"
            except:
                health_results["tenant_parser"] = "unhealthy"
            
            try:
                await asyncio.wait_for(
                    self.ragcrud.search_faq("test", "test"), 
                    timeout=1.0
                )
                health_results["ragcrud"] = "healthy"
            except:
                health_results["ragcrud"] = "unhealthy"
            
            try:
                await asyncio.wait_for(
                    self.ragllm.generate_response("test", "test", "test"), 
                    timeout=1.0
                )
                health_results["ragllm"] = "healthy"
            except:
                health_results["ragllm"] = "unhealthy"
            
            # Overall status
            critical_services = ["superintelligent_engine", "ragcrud"]
            critical_healthy = all(
                "healthy" in health_results.get(service, "") 
                for service in critical_services
            )
            
            return {
                "status": "healthy" if critical_healthy else "degraded",
                "services": health_results,
                "superintelligent_features": [
                    "4_tier_confidence_system",
                    "cost_optimization",
                    "redis_caching",
                    "budget_tracking", 
                    "intelligent_routing"
                ],
                "version": "4.0.0-superintelligent",
                "orchestrator": "SuperIntelligentCustomerOrchestrator"
            }
            
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "orchestrator": "SuperIntelligentCustomerOrchestrator"
            }