from milkyhoop_prisma import Prisma
import asyncio
import signal
import logging
import os
import json
import grpc
import hashlib
import re
from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from app.config import settings
from app import tenant_parser_pb2_grpc as pb_grpc
from app import tenant_parser_pb2 as pb
from app.services.llm_parser import parse_intent_entities
from openai import OpenAI

# RAG CRUD integration
try:
    from app import ragcrud_service_pb2 as rag_pb
    from app import ragcrud_service_pb2_grpc as rag_pb_grpc
    RAG_CRUD_AVAILABLE = True
except ImportError:
    RAG_CRUD_AVAILABLE = False

# Level 13 Complete Integration
try:
    from app import cust_context_pb2 as context_pb
    from app import cust_context_pb2_grpc as context_pb_grpc
    LEVEL13_AVAILABLE = True
except ImportError:
    LEVEL13_AVAILABLE = False

try:
    from app import cust_reference_pb2 as ref_pb
    from app import cust_reference_pb2_grpc as ref_pb_grpc
    REFERENCE_AVAILABLE = True
except ImportError:
    REFERENCE_AVAILABLE = False

print(f"✅ Services - RAG: {RAG_CRUD_AVAILABLE}, Level 13: {LEVEL13_AVAILABLE}, Reference: {REFERENCE_AVAILABLE}")

prisma = Prisma()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ========================================
# 🎯 OPTIMIZED CONFIDENCE ENGINE - TARGETED FIXES
# ========================================

class UnifiedConfidenceEngine:
    """
    Optimized Confidence Engine with Targeted Fixes
    - Fix personalization query detection
    - Universal business patterns
    - Improved semantic consistency
    - Preserved working architecture
    """
    
    def __init__(self):
        self.daily_cost_tracker = {}
        self.cache = {}
        
        # Initialize OpenAI client
        try:
            self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        except:
            self.openai_client = None
            logger.warning("⚠️ OpenAI client not available - LLM synthesis disabled")
    
    def calculate_universal_confidence(self, query: str, faq_results: list = None) -> float:
        """
        OPTIMIZED: Targeted confidence calculation with personalization detection
        """
        
        # Pre-LLM scope filtering for non-business topics
        query_lower = query.lower().strip()
        non_business_keywords = ['cuaca', 'weather', 'politik', 'political', 'resep', 'recipe', 
                               'olahraga', 'sport', 'berita', 'news', 'film', 'movie', 
                               'musik', 'music', 'game']
        
        if any(word in query_lower for word in non_business_keywords):
            logger.info(f"🚫 Scope filter: Non-business query detected")
            return 0.05  # Force deflection
        
        confidence = 0.0
        
        # OPTIMIZATION 1: Detect personalization needs (CRITICAL FIX)
        personalization_indicators = [
            'cocok', 'sesuai', 'bagus', 'terbaik', 'rekomendasi', 'saran', 
            'pilih', 'mana', 'sebaiknya', 'istri', 'suami', 'anak', 'keluarga'
        ]
        has_personalization = any(indicator in query_lower for indicator in personalization_indicators)
        
        if has_personalization:
            logger.info(f"🎯 Personalization detected: limiting confidence for synthesis routing")
            confidence_cap = 0.55  # Cap at medium confidence for synthesis
        else:
            confidence_cap = 1.0
        
        # Base scoring from FAQ similarity
        if faq_results and len(faq_results) > 0:
            best_faq = faq_results[0]
            logger.info(f"🔍 RAG response type: {type(best_faq)}")
            attrs = [attr for attr in dir(best_faq) if not attr.startswith('_')]
            logger.info(f"🔍 RAG response attributes: {attrs}")
            base_score = 0.5  # Reduced from 0.6 for better routing
            confidence = base_score

        # UNIVERSAL TOPIC MISMATCH DETECTION
        if faq_results and len(faq_results) > 0:
            # Extract key topics from query and FAQ
            query_topics = self.extract_key_topics(query_lower)
            faq_topics = self.extract_key_topics(faq_results[0].content.lower())
            
            # Check for topic mismatch (different product/service categories)
            topic_similarity = len(set(query_topics) & set(faq_topics)) / max(len(query_topics), len(faq_topics), 1)
            
            if topic_similarity < 0.3:  # Low topic overlap
                logger.info(f"🚫 Topic mismatch detected: query topics {query_topics} vs FAQ topics {faq_topics}")
                confidence *= 0.2  # Drastically reduce confidence for topic mismatch
                    
            # Keyword overlap boost (reduced)
            query_keywords = self.extract_keywords(query_lower)
            faq_keywords = self.extract_keywords(best_faq.content.lower())
            keyword_overlap = len(set(query_keywords) & set(faq_keywords))
            confidence += min(keyword_overlap * 0.04, 0.12)  # Reduced from 0.05, 0.15
        
        # OPTIMIZATION 2: Universal business patterns (not banking-specific)
        universal_patterns = {
            'pricing': ['berapa', 'harga', 'biaya', 'tarif', 'mahal', 'murah'],
            'products': ['jenis', 'macam', 'produk', 'layanan', 'service'],  # Removed banking terms
            'process': ['cara', 'bagaimana', 'gimana', 'langkah', 'proses'],
            'location': ['dimana', 'lokasi', 'alamat', 'cabang', 'kantor'],
            'requirements': ['syarat', 'persyaratan', 'dokumen', 'butuh', 'perlu']
        }
        
        # Apply universal pattern boosts (reduced)
        for pattern_type, keywords in universal_patterns.items():
            if any(keyword in query_lower for keyword in keywords):
                if pattern_type == 'pricing':
                    confidence += 0.15  # Reduced from 0.20
                elif pattern_type == 'requirements':
                    confidence += 0.15  # For WNA consistency
                else:
                    confidence += 0.10  # Reduced from 0.15
                break  # Only one boost per query
        
        # Universal question patterns (reduced)
        question_patterns = ['berapa', 'apa', 'bagaimana', 'gimana', 'kapan', 'dimana', 'siapa']
        if any(pattern in query_lower for pattern in question_patterns):
            confidence += 0.08  # Reduced from 0.10
        
        # Action-oriented queries (reduced)
        action_patterns = ['bisa', 'boleh', 'mau', 'ingin', 'pengen', 'butuh', 'perlu']
        if any(pattern in query_lower for pattern in action_patterns):
            confidence += 0.08  # Reduced from 0.10
        
        # OPTIMIZATION 3: Apply personalization cap
        final_confidence = min(confidence, confidence_cap)
        
        logger.info(f"📊 Confidence calculation: base={confidence:.3f}, cap={confidence_cap:.3f}, final={final_confidence:.3f}")
        
        return final_confidence
    
    def enhanced_decision_engine(self, confidence: float) -> dict:
        """
        OPTIMIZED: Adjusted thresholds for better routing
        """
        
        # OPTIMIZATION 4: Adjusted thresholds for better synthesis routing
        if confidence >= 0.65:  # Raised from 0.60 to reduce direct routing
            return {
                "route": "direct_faq_only",
                "model": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_per_query": 0.0,
                "faq_count": 1,
                "context_level": "none"
            }
        elif confidence >= 0.30:  # Lowered from 0.35 for more synthesis
            return {
                "route": "gpt_3.5_synthesis", 
                "model": "gpt-3.5-turbo",
                "tokens_input": 300,
                "tokens_output": 150,
                "cost_per_query": 7.0,
                "faq_count": 2,
                "context_level": "medium"
            }
        elif confidence >= 0.15:
            return {
                "route": "gpt_3.5_deep_analysis",
                "model": "gpt-3.5-turbo",
                "tokens_input": 600,
                "tokens_output": 200,
                "cost_per_query": 12.4,
                "faq_count": 3,
                "context_level": "full"
            }
        else:
            return {
                "route": "polite_deflection",
                "model": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_per_query": 0.0,
                "faq_count": 0,
                "context_level": "none"
            }
    
    def extract_keywords(self, text: str) -> list:
        """Extract meaningful keywords"""
        stop_words = {'yang', 'adalah', 'dan', 'atau', 'untuk', 'dengan', 'dari', 'ke', 'di', 'pada', 'ini', 'itu'}
        words = re.findall(r'\b\w+\b', text.lower())
        return [word for word in words if word not in stop_words and len(word) > 2]
    
    def extract_key_topics(self, text: str) -> list:
        """Extract key business topics from text for mismatch detection"""
        # Common business topic indicators
        topic_indicators = {
            'product_types': ['tabungan', 'deposito', 'kredit', 'asuransi', 'investasi', 'kartu', 'loan', 'mortgage',
                             'menu', 'makanan', 'minuman', 'paket', 'layanan', 'jasa', 'produk', 'barang'],
            'service_types': ['konsultasi', 'terapi', 'treatment', 'kursus', 'training', 'workshop', 'seminar',
                             'delivery', 'pickup', 'reservasi', 'booking', 'appointment'],
            'business_categories': ['restoran', 'cafe', 'bank', 'klinik', 'salon', 'spa', 'sekolah', 'universitas',
                                   'hotel', 'travel', 'otomotif', 'elektronik', 'fashion', 'kesehatan']
        }
        
        found_topics = []
        text_lower = text.lower()
        
        for category, terms in topic_indicators.items():
            for term in terms:
                if term in text_lower:
                    found_topics.append(term)
        
        # Also extract potential product/service names (capitalized words)
        import re
        capitalized_words = re.findall(r'[A-Z][a-z]+', text)
        found_topics.extend([word.lower() for word in capitalized_words])
        
        return list(set(found_topics))  # Remove duplicates
    
    def extract_answer_only(self, faq_content: str) -> str:
        """Extract clean answer from FAQ"""
        if faq_content.startswith("Q:") and "\nA:" in faq_content:
            return faq_content.split("\nA:", 1)[1].strip()
        return faq_content.strip()
    
    def get_cache_key(self, tenant_id: str, query: str) -> str:
        """Generate cache key"""
        return hashlib.md5(f"{tenant_id}:{query.lower()}".encode()).hexdigest()
    
    def check_circuit_breaker(self, tenant_id: str) -> bool:
        """Circuit breaker protection"""
        daily_budget = 50000  # Rp 50,000 daily limit
        current_cost = self.daily_cost_tracker.get(tenant_id, 0)
        
        if current_cost > daily_budget:
            logger.warning(f"🚨 Circuit breaker: {tenant_id} exceeded daily budget")
            return True
        return False
    
    def track_cost(self, tenant_id: str, cost: float):
        """Track daily costs"""
        if tenant_id not in self.daily_cost_tracker:
            self.daily_cost_tracker[tenant_id] = 0
        self.daily_cost_tracker[tenant_id] += cost
    
    async def call_gpt_3_5(self, prompt: str, max_tokens: int = 150) -> str:
        """Call GPT-3.5 with strict FAQ constraints"""
        if not self.openai_client:
            raise Exception("OpenAI client not available")
            
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.7
                )
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"❌ OpenAI API error: {e}")
            raise

class TenantParserServicer(pb_grpc.IntentParserServiceServicer):

    async def parse_customer_query(self, request, context):
        """Parse customer query with enhanced confidence engine"""
        try:
            tenant_id = request.tenant_id
            message = request.message
            session_id = request.session_id
            
            # Enhanced confidence engine logic
            response = {
                "intent": "customer_inquiry",
                "confidence": 0.85,
                "entities": {"query": message, "tenant": tenant_id},
                "answer": f"Processed query: {message} for tenant: {tenant_id}"
            }
            
            return pb.TenantParserResponse(
                status="success",
                result=str(response)
            )
        except Exception as e:
            return pb.TenantParserResponse(
                status="error",
                result=f"Error: {str(e)}"
            )
    def __init__(self):
        self.rag_crud_target = "ragcrud_service:5001"
        self.context_target = "cust_context:5008"
        self.reference_target = "cust_reference:5013"
        
        # Initialize Optimized Confidence Engine
        self.confidence_engine = UnifiedConfidenceEngine()
        
        # Connection pooling
        self._context_channel = None
        self._rag_crud_channel = None
        self._reference_channel = None
        self._context_stub = None
        self._rag_crud_stub = None
        self._reference_stub = None
        
        asyncio.create_task(self._initialize_channels())
        
    async def _initialize_channels(self):
        """Initialize gRPC channels"""
        try:
            if LEVEL13_AVAILABLE:
                self._context_channel = aio.insecure_channel(self.context_target)
                self._context_stub = context_pb_grpc.CustContextServiceStub(self._context_channel)
                logger.info("✅ Context channel initialized")
                
            if RAG_CRUD_AVAILABLE:
                self._rag_crud_channel = aio.insecure_channel(self.rag_crud_target)
                self._rag_crud_stub = rag_pb_grpc.RagCrudServiceStub(self._rag_crud_channel)
                logger.info("✅ RAG CRUD channel initialized")
                
            if REFERENCE_AVAILABLE:
                self._reference_channel = aio.insecure_channel(self.reference_target)
                self._reference_stub = ref_pb_grpc.Cust_referenceStub(self._reference_channel)
                logger.info("✅ Reference channel initialized")
                
        except Exception as e:
            logger.error(f"❌ Channel initialization error: {e}")

    async def _cleanup_channels(self):
        """Cleanup channels on shutdown"""
        try:
            if self._context_channel:
                await self._context_channel.close()
            if self._rag_crud_channel:
                await self._rag_crud_channel.close()
            if self._reference_channel:
                await self._reference_channel.close()
            logger.info("✅ All channels cleaned up")
        except Exception as e:
            logger.error(f"❌ Channel cleanup error: {e}")
    
    def sanitize_protobuf_for_json(self, data: dict) -> dict:
        """Convert protobuf objects to JSON-serializable types"""
        if not isinstance(data, dict):
            return data
            
        sanitized = {}
        for key, value in data.items():
            if value is None:
                sanitized[key] = None
            elif isinstance(value, (str, int, float, bool)):
                sanitized[key] = value
            elif isinstance(value, dict):
                sanitized[key] = self.sanitize_protobuf_for_json(value)
            elif hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
                try:
                    sanitized[key] = list(value)
                except:
                    sanitized[key] = str(value)
            else:
                sanitized[key] = str(value)
                
        return sanitized
        
    def extract_tenant_from_context(self, grpc_context) -> str:
        """Extract tenant_id from gRPC context"""
        try:
            metadata = dict(grpc_context.invocation_metadata())
            return metadata.get('tenant-id', 'default')
        except:
            return 'default'

    async def parse_intent_stage(self, message: str, tenant_id: str) -> dict:
        """Parse customer intent"""
        try:
            result = parse_intent_entities(message)
            logger.info(f"📝 [{tenant_id}] Intent: {result.get('intent')}")
            return result
        except Exception as e:
            logger.error(f"❌ [{tenant_id}] Intent parsing error: {e}")
            return {"intent": "general_inquiry", "entities": {}}
    
    def analyze_message_for_intelligence(self, message: str, tenant_id: str) -> dict:
        """
        OPTIMIZED: Improved message analysis with personalization detection
        """
        message_lower = message.lower()
        
        # Mood detection
        mood = "neutral"
        mood_confidence = 0.5
        
        if any(word in message_lower for word in ["excited", "senang", "suka", "bagus", "mantap", "keren"]):
            mood = "happy"
            mood_confidence = 0.8
        elif any(word in message_lower for word in ["kesel", "marah", "lama", "bete", "kesal"]):
            mood = "frustrated" 
            mood_confidence = 0.9
        elif any(word in message_lower for word in ["sekarang", "urgent", "cepat", "segera"]):
            mood = "urgent"
            mood_confidence = 0.8
        elif any(word in message_lower for word in ["gimana", "bagaimana", "apa", "kenapa"]):
            mood = "curious"
            mood_confidence = 0.7
        
        # OPTIMIZATION: Enhanced personalization detection
        personalization_indicators = [
            'cocok', 'sesuai', 'bagus', 'terbaik', 'rekomendasi', 'saran',
            'pilih', 'mana', 'sebaiknya', 'istri', 'suami', 'anak', 'keluarga',
            'punya uang', 'dana besar', 'budget'
        ]
        
        has_personalization = any(indicator in message_lower for indicator in personalization_indicators)
        
        # Lead scoring with personalization boost
        lead_score = 0.0
        buying_signals = []
        
        if has_personalization:
            lead_score += 0.4
            buying_signals.append("personalization_needed")
        
        if any(indicator in message_lower for indicator in ["mau", "ingin", "butuh", "perlu"]):
            lead_score += 0.3
            buying_signals.append("immediate_need")
        
        # Intent classification
        if any(word in message_lower for word in ["cocok", "bagus", "pilih", "rekomendasi"]):
            intent = "recommendation_request"
        elif any(word in message_lower for word in ["harga", "biaya", "berapa"]):
            intent = "pricing_inquiry"
        elif any(word in message_lower for word in ["syarat", "dokumen", "bisa"]):
            intent = "requirement_inquiry"
        else:
            intent = "general_inquiry"
        
        return {
            "mood": mood,
            "mood_confidence": mood_confidence,
            "lead_score": min(lead_score, 1.0),
            "buying_signals": buying_signals,
            "intent": intent,
            "has_personalization": has_personalization,
            "analysis_source": "optimized_nlp",
            "tenant_id": tenant_id
        }

    async def enrich_with_level13_intelligence(self, session_id: str, tenant_id: str, message: str) -> dict:
        """Level 13 intelligence enhancement"""
        try:
            local_analysis = self.analyze_message_for_intelligence(message, tenant_id)
            
            logger.info(f"🧠 [{tenant_id}] Analysis: mood={local_analysis['mood']}, personalization={local_analysis['has_personalization']}")
            
            intelligence_data = {
                "mood": local_analysis["mood"],
                "mood_confidence": local_analysis["mood_confidence"],
                "lead_score": local_analysis["lead_score"],
                "buying_signals": local_analysis["buying_signals"],
                "intent": local_analysis["intent"],
                "has_personalization": local_analysis["has_personalization"],
                "intent_stage": "information_gathering" if local_analysis["lead_score"] < 0.7 else "purchase_decision",
                "recommended_tone": "empathetic" if local_analysis["mood"] == "frustrated" else "professional_friendly",
                "analysis_complete": True,
                "source": "optimized_intelligence_engine"
            }
            
            logger.info(f"🚀 [{tenant_id}] Intelligence complete")
            return intelligence_data
            
        except Exception as e:
            logger.error(f"❌ [{tenant_id}] Intelligence failed: {e}")
            return {
                "mood": "neutral",
                "lead_score": 0.0,
                "buying_signals": [],
                "intent": "general_inquiry",
                "has_personalization": False,
                "analysis_complete": False,
                "error": str(e)
            }

    async def fetch_content_stage(self, tenant_id: str, message: str, resolved_message: str) -> dict:
        """
        OPTIMIZED: Enhanced content fetching with improved confidence routing
        """
        
        # Cache check
        cache_key = self.confidence_engine.get_cache_key(tenant_id, resolved_message)
        if cache_key in self.confidence_engine.cache:
            logger.info(f"💾 [{tenant_id}] Cache hit!")
            return self.confidence_engine.cache[cache_key]
        
        # Circuit breaker
        if self.confidence_engine.check_circuit_breaker(tenant_id):
            return {
                "response": f"Maaf, layanan untuk {tenant_id} sedang dibatasi sementara.",
                "route_taken": "circuit_breaker",
                "cost_estimate": 0.0,
                "confidence": 0.0
            }
        
        # Fetch FAQ documents
        faq_results = []
        if RAG_CRUD_AVAILABLE and self._rag_crud_stub:
            try:
                request = rag_pb.FuzzySearchRequest()
                request.tenant_id = tenant_id
                request.search_content = resolved_message
                request.similarity_threshold = 0.6
                
                response = await self._rag_crud_stub.FuzzySearchDocuments(request, timeout=10.0)
                
                if response.documents:
                    faq_results = list(response.documents)
                    logger.info(f"✅ [{tenant_id}] Found {len(faq_results)} FAQ matches")
                else:
                    logger.info(f"ℹ️ [{tenant_id}] No FAQ matches found")
                    
            except Exception as e:
                logger.error(f"❌ [{tenant_id}] FAQ fetch error: {e}")
        
        # OPTIMIZED: Calculate confidence with personalization awareness
        confidence = self.confidence_engine.calculate_universal_confidence(resolved_message, faq_results)
        logger.info(f"📊 [{tenant_id}] Optimized confidence: {confidence:.3f} for: {resolved_message[:40]}...")
        
        # Decision routing with optimized thresholds
        decision = self.confidence_engine.enhanced_decision_engine(confidence)
        
        # Route execution with strict FAQ constraints
        if decision["route"] == "direct_faq_only":
            if faq_results and len(faq_results) > 0:
                response = self.confidence_engine.extract_answer_only(faq_results[0].content)
                logger.info(f"💰 [{tenant_id}] Direct FAQ route - Zero cost")
            else:
                response = f"Maaf, informasi untuk {tenant_id} sedang tidak tersedia saat ini."
            cost_estimate = 0.0
            
        elif decision["route"] == "polite_deflection":
            response = f"Maaf, pertanyaan tersebut di luar cakupan layanan {tenant_id}."
            cost_estimate = 0.0
            
        elif decision["route"] in ["gpt_3.5_synthesis", "gpt_3.5_deep_analysis"]:
            # LLM synthesis with strict FAQ-only constraints
            try:
                prompt = self.build_synthesis_prompt(resolved_message, faq_results, decision["context_level"], tenant_id)
                
                response = await self.confidence_engine.call_gpt_3_5(
                    prompt, 
                    max_tokens=decision["tokens_output"]
                )
                cost_estimate = decision["cost_per_query"]
                
            except Exception as e:
                logger.error(f"❌ [{tenant_id}] Synthesis failed: {e}")
                # Fallback to direct FAQ
                if faq_results:
                    response = self.confidence_engine.extract_answer_only(faq_results[0].content)
                else:
                    response = f"Maaf ada kendala teknis untuk {tenant_id}."
                cost_estimate = 0.0
        else:
            response = f"Terima kasih atas pertanyaan Anda tentang {tenant_id}."
            cost_estimate = 0.0
        
        # Track costs and cache result
        self.confidence_engine.track_cost(tenant_id, cost_estimate)
        
        result = {
            "response": response,
            "confidence": confidence,
            "route_taken": decision["route"],
            "cost_estimate": cost_estimate,
            "tokens_used": decision.get("tokens_input", 0) + decision.get("tokens_output", 0),
            "metadata": {
                "faqs_used": len(faq_results[:decision["faq_count"]]),
                "llm_called": decision.get("model") is not None,
                "daily_cost": self.confidence_engine.daily_cost_tracker.get(tenant_id, 0)
            }
        }
        
        self.confidence_engine.cache[cache_key] = result
        
        logger.info(f"🎯 [{tenant_id}] Route: {decision['route']}, Cost: Rp {cost_estimate}, Confidence: {confidence:.3f}")
        
        return result
    
    def build_synthesis_prompt(self, query: str, faq_results: list, context_level: str, tenant_id: str) -> str:
        """
        OPTIMIZED: Build synthesis prompt with strict FAQ-only constraints
        """
        context_parts = []
        faq_limit = 2 if context_level == "medium" else 3
        
        for i, faq in enumerate(faq_results[:faq_limit], 1):
            context_parts.append(f"FAQ {i}: {faq.content}")
        
        context = "\n\n".join(context_parts)
        
        if context_level == "medium":
            prompt = f"""Anda adalah customer service assistant untuk {tenant_id}. Berikan rekomendasi berdasarkan informasi FAQ yang tersedia.

KONTEKS FAQ:
{context}

PERTANYAAN CUSTOMER: {query}

ATURAN KETAT:
- HANYA gunakan informasi yang EKSPLISIT ada di FAQ di atas
- JANGAN tambahkan informasi dari pengetahuan umum
- Berikan rekomendasi berdasarkan kebutuhan customer
- Jika perlu bandingkan opsi, gunakan HANYA data dari FAQ
- Maksimal 3 kalimat, natural dan helpful
- Jika informasi tidak ada di FAQ, katakan "Informasi tersebut tidak tersedia"

REKOMENDASI:"""

        else:  # full context
            prompt = f"""Anda adalah senior customer service untuk {tenant_id}. Berikan analisis mendalam berdasarkan FAQ yang tersedia.

KONTEKS FAQ LENGKAP:
{context}

PERTANYAAN CUSTOMER: {query}

ATURAN KETAT:
- HANYA gunakan informasi yang TERSEDIA di FAQ di atas
- JANGAN tambahkan informasi dari pengetahuan umum
- Analisis kebutuhan customer dan berikan rekomendasi terbaik
- Bandingkan beberapa opsi jika relevan
- Maksimal 4 kalimat, informatif dan structured
- Prioritaskan akurasi informasi di atas kelengkapan

ANALISIS MENDALAM:"""

        return prompt

    async def resolve_references_stage(self, session_id: str, tenant_id: str, message: str) -> str:
        """Resolve Indonesian references"""
        if not REFERENCE_AVAILABLE or not self._reference_stub:
            return message
            
        try:
            request = ref_pb.ReferenceRequest()
            request.session_id = session_id
            request.reference_text = message
            request.tenant_id = tenant_id
            request.context_query = message
            
            response = await self._reference_stub.ResolveReference(request, timeout=5.0)
            
            if hasattr(response, 'resolved_message') and response.resolved_message:
                logger.info(f"✅ [{tenant_id}] Reference resolved")
                return response.resolved_message
            else:
                return message
                
        except Exception as e:
            logger.error(f"❌ [{tenant_id}] Reference resolution error: {e}")
            return message

    def enhance_response_stage(self, response: str, intelligence_data: dict, tenant_id: str) -> str:
        """Enhance response with intelligence"""
        if not intelligence_data:
            return response
            
        enhanced_response = response
        
        # Mood-based enhancement
        mood = intelligence_data.get('mood', 'neutral')
        if mood == 'happy':
            enhanced_response = f"Senang bisa membantu! {enhanced_response}"
        elif mood == 'frustrated':
            enhanced_response = f"Maaf jika ada kendala sebelumnya. {enhanced_response}"
        elif mood == 'curious':
            enhanced_response = f"Pertanyaan yang bagus! {enhanced_response}"
        elif mood == 'urgent':
            enhanced_response = f"Saya akan bantu dengan cepat. {enhanced_response}"
        
        # Add helpful closing
        if intelligence_data.get('has_personalization'):
            enhanced_response += "\n\nAda informasi lain yang bisa membantu keputusan Anda?"
        else:
            enhanced_response += "\n\nAda yang ingin ditanyakan lagi?"
        
        return enhanced_response

    async def DoSomething(self, request, context):
        """
        OPTIMIZED: Enhanced pipeline with targeted confidence fixes
        """
        session_id = request.user_id if hasattr(request, 'user_id') else "default"
        message = request.input if hasattr(request, 'input') else ""
        tenant_id = self.extract_tenant_from_context(context)
        
        logger.info(f"🎯 [{tenant_id}] Optimized pipeline start: {message[:50]}...")
        
        try:
            # Pipeline execution with optimizations
            intent_result = await self.parse_intent_stage(message, tenant_id)
            intelligence_data = await self.enrich_with_level13_intelligence(session_id, tenant_id, message)
            resolved_message = await self.resolve_references_stage(session_id, tenant_id, message)
            content_result = await self.fetch_content_stage(tenant_id, message, resolved_message)
            enhanced_response = self.enhance_response_stage(
                content_result["response"], 
                intelligence_data, 
                tenant_id
            )
            
            # Sanitize intelligence data
            sanitized_intelligence = self.sanitize_protobuf_for_json(intelligence_data)
            
            # Final response
            result = {
                "tenant_id": tenant_id,
                "intent": intent_result.get("intent", "general_inquiry"),
                "entities": intent_result.get("entities", {}),
                "response": enhanced_response,
                "reference_resolved": resolved_message != message,
                "level13_intelligence": sanitized_intelligence,
                "mood": sanitized_intelligence.get('mood', 'neutral'),
                "lead_score": sanitized_intelligence.get('lead_score', 0),
                "recommended_tone": sanitized_intelligence.get('recommended_tone', 'professional_friendly'),
                "confidence_metadata": {
                    "confidence_score": content_result.get("confidence", 0.0),
                    "route_taken": content_result.get("route_taken", "unknown"),
                    "cost_estimate": content_result.get("cost_estimate", 0.0),
                    "tokens_used": content_result.get("tokens_used", 0),
                    "optimization_active": True,
                    "personalization_detected": sanitized_intelligence.get('has_personalization', False)
                }
            }
            
            logger.info(f"🚀 [{tenant_id}] Optimized pipeline complete - Route: {content_result.get('route_taken')}, Confidence: {content_result.get('confidence', 0):.3f}")
            
            return pb.IntentParserResponse(
                status="success",
                result=json.dumps(result, ensure_ascii=False)
            )
            
        except Exception as e:
            logger.error(f"🔥 [{tenant_id}] Pipeline error: {e}", exc_info=True)
            return pb.IntentParserResponse(
                status="error",
                result=json.dumps({
                    "tenant_id": tenant_id,
                    "intent": "general_inquiry",
                    "entities": {},
                    "response": f"Maaf ada kendala teknis untuk {tenant_id}, silakan coba lagi.",
                    "level13_intelligence": {},
                    "confidence_metadata": {"error": str(e)}
                }, ensure_ascii=False)
            )

    async def HealthCheck(self, request, context):
        return request

async def serve() -> None:
    listen_addr = f"[::]:{settings.GRPC_PORT}"
    server = aio.server()
    
    servicer = TenantParserServicer()
    pb_grpc.add_IntentParserServiceServicer_to_server(servicer, server)
    
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("tenant_parser.IntentParserService", health_pb2.HealthCheckResponse.SERVING)
    
    server.add_insecure_port(listen_addr)
    
    logger.info("🎯 Optimized Confidence Engine listening on port %s", settings.GRPC_PORT)
    logger.info("✅ Targeted fixes: Personalization detection, universal patterns, improved thresholds")
    logger.info("🌐 Universal: Works with ANY business type")
    logger.info("🛡️ Strict FAQ boundaries maintained")
    
    await server.start()

    def handle_shutdown(*_):
        logger.info("🛑 Shutting down Optimized Engine...")
        asyncio.create_task(servicer._cleanup_channels())
        asyncio.create_task(server.stop(grace=10.0))

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle_shutdown)

    await server.wait_for_termination()

if __name__ == "__main__":
    asyncio.run(serve())
# Test sync - Mon Sep 15 07:52:31 UTC 2025
