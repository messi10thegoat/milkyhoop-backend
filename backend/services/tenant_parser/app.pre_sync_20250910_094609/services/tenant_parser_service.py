"""
Optimized Tenant Parser Service with Targeted Fixes
- Enhanced semantic consistency for WNA queries
- Better personalization detection
- Universal business patterns
- Improved confidence thresholds
- Preserved working architecture
"""

import asyncio
import logging
import os
import re
from typing import Dict, Any, Optional, List
import grpc
import json
import structlog
from opentelemetry import trace
from google.protobuf import empty_pb2
from openai import OpenAI

# Import the generated protobuf classes
from app import tenant_parser_pb2, tenant_parser_pb2_grpc

# RAG CRUD integration
try:
    from app import ragcrud_service_pb2 as rag_pb
    from app import ragcrud_service_pb2_grpc as rag_pb_grpc
    RAG_CRUD_AVAILABLE = True
except ImportError:
    RAG_CRUD_AVAILABLE = False

# Configure logger
logger = structlog.get_logger(__name__)

class UniversalConfidenceEngine:
    """
    OPTIMIZED: Universal Confidence Engine with targeted fixes
    - Better personalization detection
    - Improved semantic consistency for WNA queries  
    - Universal business patterns
    - Strict FAQ-only synthesis
    """
    
    def __init__(self):
        self.daily_cost_tracker = {}
        self.cache = {}
        
        # Initialize OpenAI client
        try:
            self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            logger.info("✅ OpenAI client initialized")
        except Exception as e:
            self.openai_client = None
            logger.warning(f"⚠️ OpenAI client not available: {e}")
        
        # OPTIMIZATION 1: Universal business patterns (not banking-specific)
        self.universal_business_patterns = {
            "pricing_queries": [
                "berapa", "harga", "biaya", "tarif", "mahal", "murah", 
                "bayar", "cost", "fee", "charge", "gratis", "free"
            ],
            "product_queries": [
                "jenis", "macam", "produk", "layanan", "service", "menu",
                "paket", "program", "treatment", "konsultasi", "terapi"  # Universal terms
            ],
            "process_queries": [
                "cara", "bagaimana", "gimana", "langkah", "proses",
                "daftar", "buka", "tutup", "aktivasi", "registrasi"
            ],
            "location_queries": [
                "dimana", "lokasi", "alamat", "cabang", "kantor",
                "tempat", "branch", "outlet", "jam", "buka", "tutup"
            ],
            "comparison_queries": [
                "vs", "atau", "mana", "bandingkan", "perbandingan", 
                "beda", "lebih", "pilih", "bagus", "cocok", "rekomendasi"
            ],
            "requirement_queries": [
                "syarat", "persyaratan", "dokumen", "butuh", "perlu",
                "eligible", "boleh", "bisa", "izin", "kondisi"  # For WNA consistency
            ]
        }
        
        # OPTIMIZATION 2: Enhanced personalization indicators
        self.personalization_indicators = [
            'cocok', 'sesuai', 'bagus', 'terbaik', 'rekomendasi', 'saran',
            'pilih', 'mana', 'sebaiknya', 'istri', 'suami', 'anak', 'keluarga',
            'punya uang', 'dana besar', 'budget', 'kaya', 'mampu'
        ]
    
    def calculate_universal_confidence(self, query: str, faq_results: List = None) -> float:
        """
        OPTIMIZED: Enhanced confidence calculation with personalization awareness
        """
        logger.info(f"🔍 Calculating optimized confidence for: {query[:50]}...")
        
        # Scope filter for non-business queries
        query_lower = query.lower().strip()
        non_business_keywords = [
            "cuaca", "weather", "politik", "political", "resep", "recipe", 
            "olahraga", "sport", "berita", "news", "film", "movie", 
            "musik", "music", "game", "pizza", "makan", "masak"
        ]
        
        if any(word in query_lower for word in non_business_keywords):
            logger.info(f"🚫 SCOPE FILTER: Non-business query detected")
            return 0.0
        
        confidence = 0.0
        
        # OPTIMIZATION 3: Detect personalization needs (CRITICAL FIX)
        has_personalization = any(indicator in query_lower for indicator in self.personalization_indicators)
        
        if has_personalization:
            logger.info(f"🎯 Personalization detected: capping confidence for synthesis routing")
            confidence_cap = 0.55  # Force synthesis routing for personalized queries
        else:
            confidence_cap = 1.0
        
        # Base scoring from FAQ similarity
        if faq_results and len(faq_results) > 0:
            best_faq = faq_results[0]
            
            # OPTIMIZATION 4: Improved FAQ similarity calculation
            from fuzzywuzzy import fuzz
            
            faq_content = getattr(best_faq, 'content', '')
            faq_question = faq_content.split('\n')[0] if faq_content else ''
            
            # Calculate multiple similarity metrics
            partial_ratio = fuzz.partial_ratio(query_lower, faq_question.lower()) / 100.0
            token_sort_ratio = fuzz.token_sort_ratio(query_lower, faq_question.lower()) / 100.0
            similarity = max(partial_ratio, token_sort_ratio)
            
            # OPTIMIZATION 5: Enhanced semantic matching for WNA consistency
            if self.is_wna_related(query_lower) and self.is_wna_related(faq_question.lower()):
                similarity += 0.2  # Boost for WNA semantic consistency
                logger.info(f"🌍 WNA consistency boost applied")
            
            if similarity > 0.6:
                base_confidence = similarity * 0.4  # Reduced from 0.5
                confidence += base_confidence
                logger.info(f"📊 FAQ similarity: {similarity:.3f}, confidence boost: +{base_confidence:.3f}")
        
        # OPTIMIZATION 6: Universal business pattern recognition
        pattern_boost = 0.0
        for pattern_type, keywords in self.universal_business_patterns.items():
            if any(keyword in query_lower for keyword in keywords):
                if pattern_type == "comparison_queries":
                    pattern_boost = 0.20  # Reduced from 0.25
                elif pattern_type == "pricing_queries":
                    pattern_boost = 0.15  # Reduced from 0.2
                elif pattern_type == "requirement_queries":  # For WNA consistency
                    pattern_boost = 0.15
                else:
                    pattern_boost = 0.10  # Reduced from 0.15
                
                logger.info(f"🏢 Business pattern '{pattern_type}' boost: +{pattern_boost:.3f}")
                break
        
        confidence += pattern_boost
        
        # Universal question patterns (reduced impact)
        question_patterns = ['apa', 'berapa', 'bagaimana', 'gimana', 'kapan', 'dimana', 'siapa']
        if any(pattern in query_lower for pattern in question_patterns):
            confidence += 0.08  # Reduced from 0.1
        
        # Action-oriented queries (reduced impact) 
        action_patterns = ['bisa', 'boleh', 'mau', 'ingin', 'butuh', 'perlu']
        if any(pattern in query_lower for pattern in action_patterns):
            confidence += 0.08  # Reduced from 0.1
        
        # OPTIMIZATION 7: Apply personalization cap
        final_confidence = min(confidence, confidence_cap)
        
        logger.info(f"🎯 Optimized confidence: raw={confidence:.3f}, cap={confidence_cap:.3f}, final={final_confidence:.3f}")
        
        return final_confidence
    
    def is_wna_related(self, text: str) -> bool:
        """
        OPTIMIZATION 8: Enhanced WNA detection for semantic consistency
        """
        wna_indicators = [
            'wna', 'warga negara asing', 'foreigner', 'asing',
            'paspor', 'passport', 'kitas', 'kitap', 
            'visa', 'izin tinggal', 'expatriate', 'expat'
        ]
        return any(indicator in text for indicator in wna_indicators)
    
    def enhanced_decision_engine(self, confidence: float) -> dict:
        """
        OPTIMIZED: Adjusted thresholds for better synthesis routing
        """
        
        # OPTIMIZATION 9: Better routing thresholds
        if confidence >= 0.65:  # Raised from 0.60 to reduce over-direct-routing
            return {
                "route": "direct_faq_only",
                "model": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_per_query": 0.0,
                "faq_count": 1,
                "context_level": "none"
            }
        elif confidence >= 0.30:  # Lowered from 0.35 for more synthesis opportunities
            return {
                "route": "gpt35_synthesis", 
                "model": "gpt-3.5-turbo",
                "tokens_input": 300,
                "tokens_output": 150,
                "cost_per_query": 7.0,
                "faq_count": 2,
                "context_level": "medium"
            }
        elif confidence >= 0.15:  # Lowered from 0.20
            return {
                "route": "gpt35_deep_analysis",
                "model": "gpt-3.5-turbo",
                "tokens_input": 600,
                "tokens_output": 200,
                "cost_per_query": 15.0,
                "faq_count": 3,
                "context_level": "full"
            }
        else:
            return {
                "route": "no_match_deflection",
                "model": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_per_query": 0.0,
                "faq_count": 0,
                "context_level": "none"
            }
    
    async def call_gpt_35(self, prompt: str, max_tokens: int = 150) -> str:
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
            logger.error(f"❌ GPT-3.5 API call failed: {e}")
            raise e
    
    def build_universal_synthesis_prompt(self, query: str, faq_results: List, context_level: str) -> str:
        """
        OPTIMIZATION 10: Universal synthesis prompt with strict FAQ constraints
        """
        
        context_parts = []
        faq_limit = 2 if context_level == "medium" else 3
        
        for i, faq in enumerate(faq_results[:faq_limit], 1):
            context_parts.append(f"FAQ {i}: {faq.content}")
        
        context = "\n\n".join(context_parts)
        
        if context_level == "medium":
            prompt = f"""Anda adalah customer service assistant universal. Berikan rekomendasi berdasarkan informasi FAQ yang tersedia.

KONTEKS FAQ:
{context}

PERTANYAAN CUSTOMER: {query}

ATURAN KETAT UNIVERSAL:
- HANYA gunakan informasi yang EKSPLISIT tertulis di FAQ di atas
- JANGAN tambahkan informasi dari pengetahuan umum AI
- Berikan rekomendasi yang sesuai dengan kebutuhan customer
- Jika perlu bandingkan opsi, gunakan HANYA data dari FAQ
- Maksimal 3 kalimat, natural dan conversational
- Jika informasi tidak ada di FAQ, katakan "Informasi tersebut tidak tersedia"
- Fokus pada memberikan nilai tambah dari analisis FAQ

REKOMENDASI BERDASARKAN FAQ:"""

        else:  # full context
            prompt = f"""Anda adalah senior customer service universal. Berikan analisis comprehensive berdasarkan FAQ yang tersedia.

KONTEKS FAQ LENGKAP:
{context}

PERTANYAAN CUSTOMER: {query}

ATURAN KETAT COMPREHENSIVE:
- HANYA gunakan informasi yang TERSEDIA di FAQ di atas
- JANGAN tambahkan informasi dari pengetahuan umum AI
- Analisis kebutuhan customer dan berikan rekomendasi terbaik
- Bandingkan beberapa opsi jika relevan berdasarkan FAQ
- Berikan insight yang membantu pengambilan keputusan
- Maksimal 4 kalimat, informatif dan well-structured
- Prioritaskan akurasi informasi dari FAQ di atas kelengkapan
- Jika informasi tidak lengkap di FAQ, jujur sampaikan keterbatasan

ANALISIS COMPREHENSIVE BERDASARKAN FAQ:"""

        return prompt

class TenantParserService(tenant_parser_pb2_grpc.IntentParserServiceServicer):
    def __init__(self):
        self.rag_crud_target = "ragcrud_service:5001"
        
        # Initialize Optimized Confidence Engine
        self.confidence_engine = UniversalConfidenceEngine()
        
        # gRPC channel setup
        self._rag_crud_channel = None
        self._rag_crud_stub = None
        self.setup_rag_crud_channel()
    
    def setup_rag_crud_channel(self):
        """Setup RAG CRUD gRPC channel"""
        if RAG_CRUD_AVAILABLE:
            try:
                self._rag_crud_channel = grpc.aio.insecure_channel(self.rag_crud_target)
                self._rag_crud_stub = rag_pb_grpc.RagCrudServiceStub(self._rag_crud_channel)
                logger.info("✅ RAG CRUD channel initialized")
            except Exception as e:
                logger.error(f"❌ RAG CRUD channel setup failed: {e}")
                self._rag_crud_stub = None
    
    def extract_tenant_from_context(self, context) -> str:
        """Extract tenant_id from gRPC context"""
        try:
            metadata = dict(context.invocation_metadata())
            return metadata.get('tenant-id', 'unknown')
        except Exception as e:
            logger.error(f"❌ Failed to extract tenant: {e}")
            return 'unknown'
    
    async def DoSomething(self, request, context):
        """
        OPTIMIZED: Enhanced Confidence Engine with targeted fixes
        """
        session_id = request.user_id
        message = request.input
        tenant_id = self.extract_tenant_from_context(context)
        
        logger.info(f"🎯 [{tenant_id}] Optimized pipeline start: {message[:50]}...")
        
        try:
            # Step 1: Fetch FAQ documents with enhanced retrieval
            faq_results = []
            if RAG_CRUD_AVAILABLE and self._rag_crud_stub:
                try:
                    faq_request = rag_pb.FuzzySearchRequest()
                    faq_request.tenant_id = tenant_id
                    faq_request.search_content = message
                    faq_request.similarity_threshold = 0.6  # Optimized threshold
                    
                    faq_response = await self._rag_crud_stub.FuzzySearchDocuments(
                        faq_request, timeout=10.0
                    )
                    
                    if faq_response.documents:
                        faq_results = list(faq_response.documents)
                        logger.info(f"✅ [{tenant_id}] Found {len(faq_results)} FAQ matches")
                    else:
                        logger.info(f"ℹ️ [{tenant_id}] No FAQ matches found")
                        
                except Exception as e:
                    logger.error(f"❌ [{tenant_id}] FAQ fetch error: {e}")
            
            # Step 2: OPTIMIZED confidence calculation
            confidence = self.confidence_engine.calculate_universal_confidence(message, faq_results)
            
            # Step 3: Enhanced routing decision
            decision = self.confidence_engine.enhanced_decision_engine(confidence)
            
            # Step 4: Execute routing with strict FAQ boundaries
            if decision["route"] == "direct_faq_only" and faq_results:
                # High confidence - direct FAQ usage
                best_faq = faq_results[0]
                response_text = self.extract_answer_from_faq(best_faq.content)
                
            elif decision["route"] == "no_match_deflection":
                # No confidence - polite deflection
                response_text = f"Maaf, pertanyaan Anda di luar cakupan layanan yang tersedia. Silakan hubungi tim customer service untuk bantuan lebih lanjut."
                
            elif decision["route"] in ["gpt35_synthesis", "gpt35_deep_analysis"]:
                # Medium/low confidence - GPT-3.5 SYNTHESIS with strict constraints
                try:
                    prompt = self.confidence_engine.build_universal_synthesis_prompt(
                        message, faq_results, decision["context_level"]
                    )
                    
                    response_text = await self.confidence_engine.call_gpt_35(
                        prompt, 
                        max_tokens=decision["tokens_output"]
                    )
                    
                    logger.info(f"✅ [{tenant_id}] GPT-3.5 synthesis successful - Route: {decision['route']}")
                    
                except Exception as e:
                    logger.error(f"❌ [{tenant_id}] GPT-3.5 synthesis failed: {e}")
                    # Fallback to direct FAQ
                    if faq_results:
                        response_text = self.extract_answer_from_faq(faq_results[0].content)
                    else:
                        response_text = f"Maaf ada kendala teknis, silakan coba lagi."
            else:
                # Fallback
                if faq_results:
                    best_faq = faq_results[0]
                    response_text = self.extract_answer_from_faq(best_faq.content)
                else:
                    response_text = f"Terima kasih atas pertanyaan Anda. Tim kami akan segera membantu."

            # Final response with optimized confidence metadata
            result = {
                "tenant_id": tenant_id,
                "intent": "general_inquiry",
                "entities": {},
                "response": response_text,
                "confidence_metadata": {
                    "confidence_score": confidence,
                    "route_taken": decision["route"],
                    "cost_estimate": decision["cost_per_query"],
                    "tokens_used": decision["tokens_input"] + decision["tokens_output"],
                    "optimization_active": True,
                    "faq_matches_found": len(faq_results),
                    "synthesis_enabled": self.confidence_engine.openai_client is not None,
                    "personalization_aware": True,
                    "universal_patterns": True,
                    "wna_consistency": True
                }
            }
            
            logger.info(f"🚀 [{tenant_id}] Optimized confidence complete - Route: {decision['route']}, Confidence: {confidence:.3f}")
            
            return tenant_parser_pb2.IntentParserResponse(
                status="success",
                result=json.dumps(result, ensure_ascii=False)
            )
            
        except Exception as e:
            logger.error(f"🔥 [{tenant_id}] Optimized confidence error: {e}", exc_info=True)
            return tenant_parser_pb2.IntentParserResponse(
                status="error",
                result=json.dumps({
                    "tenant_id": tenant_id,
                    "intent": "general_inquiry",
                    "entities": {},
                    "response": f"Maaf ada kendala teknis, silakan coba lagi.",
                    "confidence_metadata": {"error": str(e)}
                }, ensure_ascii=False)
            )
    
    def extract_answer_from_faq(self, faq_content: str) -> str:
        """Extract answer part from Q&A format FAQ"""
        try:
            if faq_content.startswith("Q:") and "\nA:" in faq_content:
                # Extract answer part after "A: "
                answer_part = faq_content.split("\nA:", 1)[1].strip()
                return answer_part
            else:
                # Return content as-is if not in Q&A format
                return faq_content.strip()
        except Exception as e:
            logger.error(f"❌ FAQ extraction error: {e}")
            return faq_content

async def serve():
    server = grpc.aio.server()
    tenant_parser_pb2_grpc.add_IntentParserServiceServicer_to_server(
        TenantParserService(), server
    )
    listen_addr = '[::]:5012'
    server.add_insecure_port(listen_addr)
    
    logger.info(f"🎯 Optimized Tenant Parser with targeted fixes starting on {listen_addr}")
    logger.info(f"✅ Key optimizations: Personalization detection, WNA consistency, universal patterns")
    logger.info(f"🛡️ Strict FAQ-only synthesis with improved confidence thresholds")
    logger.info(f"🌐 Universal: Works for ANY business type")
    
    await server.start()
    await server.wait_for_termination()

if __name__ == '__main__':
    asyncio.run(serve())