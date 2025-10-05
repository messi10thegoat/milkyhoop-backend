"""
SUPER INTELLIGENT TENANTBOT - Enhanced Confidence Engine
4-Tier Intelligence System:
1. High Score (≥0.85) → Direct FAQ fallback (no API call)
2. Medium Score (0.60-0.84) → GPT synthesis with relevant FAQ (API call)
3. Low Score (0.30-0.59) → Deep understanding with multiple FAQ (API call)
4. Out of scope (<0.30) → Polite deflection (no API call)
"""

import asyncio
import logging
import os
import re
from typing import Dict, Any, Optional, List
from fuzzywuzzy import fuzz
from openai import OpenAI

logger = logging.getLogger(__name__)

class SuperIntelligentConfidenceEngine:
    """
    4-Tier Super Intelligence System for Tenantbot
    Implements precise confidence scoring with API optimization
    """
    
    def __init__(self):
        self.daily_cost_tracker = {}
        self.cache = {}
        
        # Initialize OpenAI client
        try:
            self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            logger.info("Super Intelligence Engine: OpenAI client ready")
        except Exception as e:
            self.openai_client = None
            logger.warning(f"OpenAI client unavailable: {e}")
        
        # Universal business patterns for all tenant types
        self.business_patterns = {
            "pricing": ["berapa", "harga", "biaya", "tarif", "setoran", "admin", "mahal", "murah", "cost"],
            "products": ["tabungan", "kredit", "kartu", "produk", "layanan", "jenis", "macam", "menu"],
            "process": ["cara", "bagaimana", "gimana", "langkah", "proses", "daftar", "buka"],
            "location": ["dimana", "lokasi", "alamat", "cabang", "kantor", "tempat"],
            "comparison": ["vs", "atau", "mana", "bandingkan", "beda", "lebih", "pilih", "cocok"],
            "requirements": ["syarat", "persyaratan", "dokumen", "butuh", "perlu", "wajib"]
        }
        
        # Out-of-scope keywords for deflection
        self.out_of_scope = [
            "cuaca", "weather", "politik", "political", "resep", "recipe", 
            "olahraga", "sport", "berita", "news", "film", "movie", 
            "musik", "music", "game", "pizza", "masak", "cooking"
        ]
    
    def calculate_super_confidence(self, query: str, faq_results: List = None, tenant_id: str = "default") -> float:
        """
        Super Intelligence Confidence Calculation
        Returns precise confidence score for 4-tier routing
        """
        logger.info(f"[{tenant_id}] Super Intelligence: analyzing '{query[:50]}...'")
        
        query_lower = query.lower().strip()
        
        # TIER 4: Immediate out-of-scope detection
        if any(word in query_lower for word in self.out_of_scope):
            logger.info(f"[{tenant_id}] TIER 4: Out-of-scope detected → polite deflection")
            return 0.0
        
        confidence = 0.0
        
        # FAQ similarity analysis
        if faq_results and len(faq_results) > 0:
            best_faq = faq_results[0]
            faq_content = f"{best_faq.question} {best_faq.answer}" if hasattr(best_faq, "question") else str(best_faq)


            # Semantic topic matching
            query_topics = self.extract_semantic_topics(query_lower)
            faq_topics = self.extract_semantic_topics(faq_content.lower())
            
            logger.info(f"[{tenant_id}] Query topics: {query_topics}")
            logger.info(f"[{tenant_id}] FAQ topics: {faq_topics}")
            
            # Topic similarity check
            if query_topics and faq_topics:
                overlap = len(set(query_topics) & set(faq_topics))
                total = len(set(query_topics) | set(faq_topics))
                topic_similarity = overlap / total if total > 0 else 0.0
                
                if topic_similarity < 0.3:
                    logger.info(f"[{tenant_id}] Topic mismatch: {topic_similarity:.3f} → deflection")
                    return 0.0
            
            # Use RAG service similarity scores directly
            rag_similarity = getattr(best_faq, 'similarity_score', 0.0)
            if rag_similarity == 0.0 and hasattr(best_faq, 'score'):
                rag_similarity = best_faq.score

            # Convert RAG score to confidence
            if rag_similarity > 0.7:
                confidence = 0.65 + (rag_similarity - 0.7) * 0.25  # 0.65-0.72 range
                logger.info(f"[{tenant_id}] High RAG similarity: {rag_similarity:.3f}")
            elif rag_similarity > 0.5:
                confidence = 0.40 + (rag_similarity - 0.5) * 0.40  # 0.40-0.65 range
                logger.info(f"[{tenant_id}] Medium RAG similarity: {rag_similarity:.3f}")
            else:
                confidence = rag_similarity * 0.6  # 0.0-0.30 range
                logger.info(f"[{tenant_id}] Low RAG similarity: {rag_similarity:.3f}")
        
        # Business pattern boosts
        pattern_boost = 0.0
        for pattern_type, keywords in self.business_patterns.items():
            if any(keyword in query_lower for keyword in keywords):
                if pattern_type == "comparison":
                    pattern_boost = 0.15
                elif pattern_type == "pricing":
                    pattern_boost = 0.12
                else:
                    pattern_boost = 0.08
                logger.info(f"[{tenant_id}] {pattern_type} pattern: +{pattern_boost}")
                break
        
        confidence += pattern_boost
        final_confidence = min(confidence, 1.0)
        
        # Determine tier
        if final_confidence >= 0.85:
            tier = "TIER 1: Direct FAQ"
        elif final_confidence >= 0.60:
            tier = "TIER 2: GPT Synthesis"
        elif final_confidence >= 0.30:
            tier = "TIER 3: Deep Understanding"
        else:
            tier = "TIER 4: Polite Deflection"
        
        logger.info(f"[{tenant_id}] Super confidence: {final_confidence:.3f} → {tier}")
        return final_confidence
    
    def super_decision_engine(self, confidence: float) -> Dict[str, Any]:
        """
        4-Tier Decision Engine with API call optimization
        """
        if confidence >= 0.85:
            # TIER 1: High confidence - Direct FAQ (NO API CALL)
            return {
                "route": "direct_faq_fallback",
                "tier": 1,
                "api_call": False,
                "model": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_per_query": 0.0,
                "faq_count": 1,
                "intelligence_level": "direct"
            }
        elif confidence >= 0.60:
            # TIER 2: Medium confidence - GPT Synthesis (API CALL)
            return {
                "route": "gpt_synthesis",
                "tier": 2,
                "api_call": True,
                "model": "gpt-3.5-turbo",
                "tokens_input": 400,
                "tokens_output": 200,
                "cost_per_query": 9.0,
                "faq_count": 2,
                "intelligence_level": "synthesis"
            }
        elif confidence >= 0.30:
            # TIER 3: Low confidence - Deep Understanding (API CALL)
            return {
                "route": "deep_understanding",
                "tier": 3,
                "api_call": True,
                "model": "gpt-3.5-turbo",
                "tokens_input": 600,
                "tokens_output": 300,
                "cost_per_query": 18.0,
                "faq_count": 4,
                "intelligence_level": "deep"
            }
        else:
            # TIER 4: Out of scope - Polite Deflection (NO API CALL)
            return {
                "route": "polite_deflection",
                "tier": 4,
                "api_call": False,
                "model": None,
                "tokens_input": 0,
                "tokens_output": 0,
                "cost_per_query": 0.0,
                "faq_count": 0,
                "intelligence_level": "deflection"
            }
    
    def extract_semantic_topics(self, text: str) -> List[str]:
        """Extract semantic topics for intelligent matching"""
        topic_categories = {
            'financial': ['tabungan', 'deposito', 'kredit', 'investasi', 'kartu', 'pinjaman', 'asuransi'],
            'food': ['menu', 'makanan', 'minuman', 'paket', 'hidangan', 'resto', 'cafe'],
            'health': ['konsultasi', 'terapi', 'treatment', 'perawatan', 'klinik', 'dokter'],
            'education': ['kursus', 'training', 'belajar', 'les', 'workshop', 'seminar'],
            'service': ['layanan', 'jasa', 'booking', 'reservasi', 'appointment']
        }
        
        found_topics = []
        text_lower = text.lower()
        
        for category, terms in topic_categories.items():
            for term in terms:
                if term in text_lower:
                    found_topics.append(term)
        
        # Extract proper nouns (capitalized words)
        proper_nouns = re.findall(r'\b[A-Z][a-zA-Z]+\b', text)
        found_topics.extend([word.lower() for word in proper_nouns])
        
        return list(set(found_topics))
    
    async def call_gpt_super_synthesis(self, query: str, faq_results: List, intelligence_level: str, tenant_id: str) -> str:
        """
        Super Intelligent GPT Synthesis - NO HALLUCINATION
        Strict FAQ-only responses with enhanced intelligence
        """
        if not self.openai_client:
            raise Exception("OpenAI client not available")
        
        # Build FAQ context
        context_parts = []
        if intelligence_level == "synthesis":
            for i, faq in enumerate(faq_results[:2], 1):
                context_parts.append(f"FAQ {i}: {faq.content}")
        else:  # deep understanding
            for i, faq in enumerate(faq_results[:4], 1):
                context_parts.append(f"FAQ {i}: {faq.content}")
        
        context = "\n\n".join(context_parts)
        
        if intelligence_level == "synthesis":
            prompt = f"""Anda adalah AI assistant super intelligent untuk {tenant_id}. Berikan respons berkualitas tinggi berdasarkan FAQ.

KONTEKS FAQ:
{context}

PERTANYAAN: {query}

ATURAN SUPER INTELLIGENT:
- Jawab secara natural dan conversational seperti customer service yang ramah
- Gunakan informasi dari FAQ yang relevan
- Jika perlu bandingkan produk, berikan perbandingan yang jelas
- Jika ada pertanyaan tentang biaya/admin, sebutkan angka spesifik dari FAQ
- Maksimal 4 kalimat, langsung to the point
- Gunakan bahasa Indonesia yang friendly
- JANGAN MEMBUAT JAWABAN YANG TIDAK ADA DI FAQ, walaupun mungkin ada di pengetahuan umum tentang tenant. Tetap ketat jawab berdasarkan faq yang disediakan, boleh parafrase. 

RESPONS CERDAS:"""

        else:  # deep understanding
            prompt = f"""Anda adalah senior AI consultant super intelligent untuk {tenant_id}. Berikan analisis mendalam berdasarkan FAQ.

KONTEKS FAQ LENGKAP:
{context}

PERTANYAAN KOMPLEKS: {query}

ATURAN DEEP UNDERSTANDING:
- Berikan analisis mendalam berdasarkan kebutuhan customer
- Jika customer menyebutkan budget/kondisi tertentu, berikan rekomendasi yang sesuai
- Bandingkan beberapa produk jika diperlukan
- Jelaskan keuntungan dan pertimbangan masing-masing opsi
- Gunakan data spesifik dari FAQ (biaya admin, minimal setoran, dll)
- Maksimal 4 kalimat, structured dan informatif
- Tone professional tapi tetap friendly
- JANGAN MEMBUAT JAWABAN YANG TIDAK ADA DI FAQ, walaupun mungkin ada di pengetahuan umum tentang tenant. Tetap ketat jawab berdasarkan faq yang disediakan, boleh parafrase.

ANALISIS MENDALAM:"""

        try:
            response = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300 if intelligence_level == "deep" else 200,
                temperature=0.7
            )

            result = response.choices[0].message.content.strip()
            logger.info(f"[{tenant_id}] ✅ GPT synthesis completed: {len(result)} chars")
            return result
            
        except Exception as e:
            logger.error(f"[{tenant_id}] Super synthesis failed: {e}")
            raise e
    
    def extract_faq_answer(self, faq_content: str) -> str:
        """Extract clean answer from FAQ content"""
        try:
            if faq_content.startswith("Q:") and "\nA:" in faq_content:
                return faq_content.split("\nA:", 1)[1].strip()
            return faq_content.strip()
        except:
            return faq_content
    
    def get_cache_key(self, tenant_id: str, query: str) -> str:
        """Generate cache key for performance"""
        import hashlib
        return hashlib.md5(f"{tenant_id}:{query.lower()}".encode()).hexdigest()
    
    def track_intelligence_cost(self, tenant_id: str, cost: float):
        """Track daily intelligence costs"""
        if tenant_id not in self.daily_cost_tracker:
            self.daily_cost_tracker[tenant_id] = 0.0
        self.daily_cost_tracker[tenant_id] += cost
    
    def check_daily_budget(self, tenant_id: str) -> bool:
        """Check if daily budget exceeded"""
        daily_limit = 100000  # Rp 100k daily limit
        current_cost = self.daily_cost_tracker.get(tenant_id, 0)
        return current_cost > daily_limit
    
    def get_polite_deflection(self, tenant_id: str) -> str:
        """Generate polite deflection message"""
        return f"Maaf, pertanyaan tersebut di luar cakupan layanan {tenant_id}. Silakan tanyakan hal lain yang dapat saya bantu."

# Factory function for integration
def create_super_intelligent_engine():
    """Create super intelligent confidence engine"""
    return SuperIntelligentConfidenceEngine()
# Factory function for backward compatibility
def create_enhanced_confidence_engine():
    """Create enhanced confidence engine instance"""
    return SuperIntelligentConfidenceEngine()
