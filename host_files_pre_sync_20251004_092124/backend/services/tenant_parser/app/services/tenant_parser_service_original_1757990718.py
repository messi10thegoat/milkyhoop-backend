# Main tenant parser service with GPT-3.5 synthesis - FILL MANUALLY
"""
Enhanced Tenant Parser Service with Real Confidence Engine & GPT-3.5 Synthesis
Implements dynamic confidence scoring, intelligent routing, and FAQ synthesis
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

class UnifiedConfidenceEngine:
    """
    Universal Confidence Engine for Cost Optimization
    Dynamic routing based on FAQ similarity and business patterns
    With GPT-3.5 synthesis implementation
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
            logger.warning(f"⚠️ OpenAI client not available - LLM synthesis disabled: {e}")
        
        # Business pattern definitions with comparison keywords
        self.business_patterns = {
            "pricing_queries": [
                "berapa", "harga", "biaya", "tarif", "setoran", "admin", 
                "mahal", "murah", "bayar", "cost", "fee", "charge"
            ],
            "product_queries": [
                "tabungan", "kredit", "kartu", "deposito", "investasi",
                "asuransi", "pinjaman", "kpr", "produk", "layanan"
            ],
            "process_queries": [
                "cara", "bagaimana", "gimana", "langkah", "proses",
                "daftar", "buka", "tutup", "aktivasi", "registrasi"
            ],
            "location_queries": [
                "dimana", "lokasi", "alamat", "cabang", "kantor",
                "atm", "tempat", "outlet", "branch"
            ],
            "comparison_queries": [
                "vs", "atau", "mana", "bandingkan", "perbandingan", 
                "beda", "lebih", "pilih", "bagus", "cocok"
            ]
        }
    
    def calculate_universal_confidence(self, query: str, faq_results: List = None) -> float:
        """Enhanced universal confidence calculation with FAQ similarity scoring"""
        logger.info(f"🔍 Calculating confidence for: {query[:50]}...")
        
        # Immediate scope filter
        query_lower = query.lower().strip()
        non_business_keywords = [
            "cuaca", "weather", "politik", "political", "resep", "recipe", 
            "olahraga", "sport", "berita", "news", "film", "movie", 
            "musik", "music", "game", "pizza", "makan", "masak"
        ]
        
        # Scope filter check
        if any(word in query_lower for word in non_business_keywords):
            logger.info(f"🚫 SCOPE FILTER: Non-business query detected")
            return 0.0
        
        confidence = 0.0
        
        # Base scoring from FAQ similarity if available
        if faq_results and len(faq_results) > 0:
            best_faq = faq_results[0]
            
            # Manual similarity calculation using fuzzy string matching
            from fuzzywuzzy import fuzz
            
            # Get FAQ content for comparison
            faq_content = getattr(best_faq, 'content', '')
            faq_question = faq_content.split('\n')[0] if faq_content else ''

            
            # Calculate similarity scores
            partial_ratio = fuzz.partial_ratio(query_lower, faq_question.lower()) / 100.0
            token_sort_ratio = fuzz.token_sort_ratio(query_lower, faq_question.lower()) / 100.0
            similarity = max(partial_ratio, token_sort_ratio)
            
            # Apply similarity boost if above threshold
            if similarity > 0.6:
                boost = min(similarity * 0.5, 0.4)
                confidence += boost
                logger.info(f"📊 FAQ similarity boost: +{boost:.3f} (similarity: {similarity:.3f})")
            else:
                logger.info(f"📊 FAQ similarity boost: +0.000 (similarity: {similarity:.3f} below threshold)")
        
        # Business pattern recognition boosts
        if any(pattern in query_lower for pattern in self.business_patterns["comparison_queries"]):
            confidence += 0.25
            logger.info(f"⚖️ Comparison pattern boost: +0.25")
        elif any(pattern in query_lower for pattern in self.business_patterns["pricing_queries"]):
            confidence += 0.2
            logger.info(f"💰 Pricing pattern boost: +0.2")
        elif any(pattern in query_lower for pattern in self.business_patterns["product_queries"]):
            confidence += 0.15
            logger.info(f"📦 Product pattern boost: +0.15")
        elif any(pattern in query_lower for pattern in self.business_patterns["process_queries"]):
            confidence += 0.15
            logger.info(f"⚙️ Process pattern boost: +0.15")
        elif any(pattern in query_lower for pattern in self.business_patterns["location_queries"]):
            confidence += 0.15
            logger.info(f"📍 Location pattern boost: +0.15")
        
        final_confidence = min(confidence, 1.0)
        logger.info(f"🎯 Final confidence: {final_confidence:.3f}")
        return final_confidence
    
    def enhanced_decision_engine(self, confidence: float) -> dict:
        """Cost-optimized decision tree with routing thresholds"""
        
        if confidence >= 0.75:
            return {
                "route": "direct_faq_only",
                "model": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_per_query": 0.0,
                "faq_count": 1,
                "context_level": "none"
            }
        elif confidence >= 0.40:
            return {
                "route": "gpt35_synthesis", 
                "model": "gpt-3.5-turbo",
                "tokens_input": 300,
                "tokens_output": 150,
                "cost_per_query": 7.0,
                "faq_count": 2,
                "context_level": "medium"
            }
        elif confidence >= 0.20:
            return {
                "route": "gpt35_deep_analysis",  # Changed from gpt4 to gpt35 for cost optimization
                "model": "gpt-3.5-turbo",
                "tokens_input": 600,
                "tokens_output": 200,
                "cost_per_query": 15.0,  # Reduced cost since using GPT-3.5
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
        """Call GPT-3.5 with token budget enforcement"""
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
    
    def build_medium_prompt(self, query: str, faq_results: List) -> str:
        """Build medium context prompt for GPT-3.5 synthesis"""
        
        context_parts = []
        for i, faq in enumerate(faq_results[:2], 1):
            context_parts.append(f"FAQ {i}: {faq.content}")
        
        context = "\n\n".join(context_parts)
        
        prompt = f"""Kamu adalah customer service assistant untuk BCA. Jawab pertanyaan customer berdasarkan informasi FAQ yang tersedia.

KONTEKS FAQ:
{context}

PERTANYAAN CUSTOMER: {query}

INSTRUKSI:
- Jawab secara natural dan conversational seperti customer service yang ramah
- Gunakan informasi dari FAQ yang relevan
- Jika perlu bandingkan produk, berikan perbandingan yang jelas
- Jika ada pertanyaan tentang biaya/admin, sebutkan angka spesifik dari FAQ
- Maksimal 3 kalimat, langsung to the point
- Gunakan bahasa Indonesia yang friendly

JAWABAN:"""

        return prompt
    
    def build_deep_prompt(self, query: str, faq_results: List) -> str:
        """Build deep context prompt for complex queries"""
        
        context_parts = []
        for i, faq in enumerate(faq_results[:3], 1):
            context_parts.append(f"FAQ {i}: {faq.content}")
        
        context = "\n\n".join(context_parts)
        
        prompt = f"""Kamu adalah senior customer service BCA yang ahli memberikan rekomendasi produk. Analisis pertanyaan customer dan berikan jawaban comprehensive.

KONTEKS FAQ LENGKAP:
{context}

PERTANYAAN CUSTOMER: {query}

INSTRUKSI:
- Berikan analisis mendalam berdasarkan kebutuhan customer
- Jika customer menyebutkan budget/kondisi tertentu, berikan rekomendasi yang sesuai
- Bandingkan beberapa produk jika diperlukan
- Jelaskan keuntungan dan pertimbangan masing-masing opsi
- Gunakan data spesifik dari FAQ (biaya admin, minimal setoran, dll)
- Maksimal 4 kalimat, structured dan informatif
- Tone professional tapi tetap friendly

REKOMENDASI:"""

        return prompt

class TenantParserService(tenant_parser_pb2_grpc.IntentParserServiceServicer):
    def __init__(self):
        self.rag_crud_target = "ragcrud_service:5001"
        
        # Initialize Unified Confidence Engine
        self.confidence_engine = UnifiedConfidenceEngine()
        
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
        """Enhanced Confidence Engine Implementation with GPT-3.5 Synthesis"""
        session_id = request.user_id
        message = request.input
        tenant_id = self.extract_tenant_from_context(context)
        
        logger.info(f"🧠 [{tenant_id}] Enhanced confidence pipeline start: {message[:50]}...")
        
        try:
            # Step 1: Fetch FAQ documents
            faq_results = []
            if RAG_CRUD_AVAILABLE and self._rag_crud_stub:
                try:
                    faq_request = rag_pb.FuzzySearchRequest()
                    faq_request.tenant_id = tenant_id
                    faq_request.search_content = message
                    faq_request.similarity_threshold = 0.7
                    
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
            
            # Step 2: Calculate real confidence
            confidence = self.confidence_engine.calculate_universal_confidence(message, faq_results)
            
            # Step 3: Get routing decision
            decision = self.confidence_engine.enhanced_decision_engine(confidence)
            
            # Step 4: Build response based on routing with SYNTHESIS
            if decision["route"] == "direct_faq_only" and faq_results:
                # High confidence - use direct FAQ
                best_faq = faq_results[0]
                response_text = self.extract_answer_from_faq(best_faq.content)
                
            elif decision["route"] == "no_match_deflection":
                # No confidence - polite deflection
                response_text = f"Maaf, pertanyaan Anda di luar cakupan layanan {tenant_id}. Silakan hubungi customer service untuk bantuan lebih lanjut."
                
            elif decision["route"] in ["gpt35_synthesis", "gpt35_deep_analysis"]:
                # Medium/low confidence - GPT-3.5 SYNTHESIS
                try:
                    if decision["context_level"] == "medium":
                        prompt = self.confidence_engine.build_medium_prompt(message, faq_results[:2])
                    else:  # full context
                        prompt = self.confidence_engine.build_deep_prompt(message, faq_results[:3])
                    
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
                        response_text = f"Maaf ada kendala teknis untuk {tenant_id}, silakan coba lagi."
            else:
                # Fallback
                if faq_results:
                    best_faq = faq_results[0]
                    response_text = self.extract_answer_from_faq(best_faq.content)
                else:
                    response_text = f"Terima kasih atas pertanyaan Anda. Tim {tenant_id} akan segera membantu Anda."

            # Final response with real confidence metadata
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
                    "synthesis_enabled": self.confidence_engine.openai_client is not None
                }
            }
            
            logger.info(f"🚀 [{tenant_id}] Enhanced confidence complete - Route: {decision['route']}, Confidence: {confidence:.3f}")
            
            return tenant_parser_pb2.IntentParserResponse(
                status="success",
                result=json.dumps(result, ensure_ascii=False)
            )
            
        except Exception as e:
            logger.error(f"🔥 [{tenant_id}] Enhanced confidence error: {e}", exc_info=True)
            return tenant_parser_pb2.IntentParserResponse(
                status="error",
                result=json.dumps({
                    "tenant_id": tenant_id,
                    "intent": "general_inquiry",
                    "entities": {},
                    "response": f"Maaf ada kendala teknis untuk {tenant_id}, silakan coba lagi.",
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
    
    logger.info(f"🚀 Enhanced Tenant Parser with GPT-3.5 Synthesis starting on {listen_addr}")
    logger.info(f"💰 Cost Optimization: GPT-3.5 only, NO GPT-4 usage")
    logger.info(f"⚖️ Synthesis Routes: Medium confidence (≥0.40), Low confidence (≥0.20)")
    await server.start()
    await server.wait_for_termination()

if __name__ == '__main__':
    asyncio.run(serve())