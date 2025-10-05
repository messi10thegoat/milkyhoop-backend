"""
SUPER INTELLIGENT TENANTBOT - Enhanced Confidence Engine (OPTIMIZED)
4-Tier Intelligence System:
1. High Score (≥0.85) → Direct FAQ fallback (no API call)
2. Medium Score (0.60-0.84) → GPT synthesis with relevant FAQ (API call)
3. Low Score (0.30-0.59) → Deep understanding with multiple FAQ (API call)
4. Out of scope (<0.30) → Polite deflection (no API call)

OPTIMIZATION: Simplified confidence calculation using RAG CRUD scores directly
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
    4-Tier Super Intelligence System for Tenantbot (OPTIMIZED)
    Implements precise confidence scoring with simplified, predictable logic
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
        
        # Out-of-scope keywords for deflection (simplified)
        self.out_of_scope = [
            "cuaca", "weather", "politik", "political", "resep", "recipe", 
            "olahraga", "sport", "berita", "news", "film", "movie", 
            "musik", "music", "game", "pizza", "masak", "cooking",
            "ayam goreng", "fried chicken", "makanan", "food"
        ]
    
    def calculate_super_confidence(self, query: str, faq_results: List = None, tenant_id: str = "default") -> float:
        """
        OPTIMIZED: Simple, predictable confidence calculation
        Uses RAG CRUD scores directly without artificial transformations
        """
        logger.info(f"[{tenant_id}] Confidence calculation: '{query[:50]}...'")
        
        query_lower = query.lower().strip()
        
        # TIER 4: Immediate out-of-scope detection
        if any(word in query_lower for word in self.out_of_scope):
            logger.info(f"[{tenant_id}] OUT-OF-SCOPE detected → TIER 4 deflection")
            return 0.0
        
        # Return 0 if no FAQ results
        if not faq_results or len(faq_results) == 0:
            logger.info(f"[{tenant_id}] No FAQ results → TIER 4 deflection")
            return 0.0
        
        # OPTIMIZED: Use RAG CRUD similarity score directly
        best_faq = faq_results[0]
        
        # Extract similarity score from multiple possible field names
        confidence = 0.0
        if hasattr(best_faq, 'similarity_score'):
            confidence = float(best_faq.similarity_score)
        elif hasattr(best_faq, 'score'):
            confidence = float(best_faq.score)
        elif isinstance(best_faq, dict) and 'score' in best_faq:
            confidence = float(best_faq['score'])
        elif isinstance(best_faq, dict) and 'similarity_score' in best_faq:
            confidence = float(best_faq['similarity_score'])
        
        # Ensure confidence is within valid range
        confidence = max(0.0, min(confidence, 1.0))
        
        # Determine tier based on optimized thresholds
        if confidence >= 0.85:
            tier = "TIER 1: Direct FAQ"
        elif confidence >= 0.60:
            tier = "TIER 2: GPT Synthesis"
        elif confidence >= 0.30:
            tier = "TIER 3: Deep Understanding"
        else:
            tier = "TIER 4: Polite Deflection"
        
        logger.info(f"[{tenant_id}] RAG score: {confidence:.3f} → {tier}")
        return confidence
    
    def super_decision_engine(self, confidence: float) -> Dict[str, Any]:
        """
        4-Tier Decision Engine with API call optimization (UNCHANGED)
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
    
    async def call_gpt_super_synthesis(self, query: str, faq_results: List, intelligence_level: str, tenant_id: str) -> str:
        """
        Super Intelligent GPT Synthesis - NO HALLUCINATION (UNCHANGED)
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
        """Extract clean answer from FAQ content (UNCHANGED)"""
        try:
            if faq_content.startswith("Q:") and "\nA:" in faq_content:
                return faq_content.split("\nA:", 1)[1].strip()
            return faq_content.strip()
        except:
            return faq_content
    
    def get_cache_key(self, tenant_id: str, query: str) -> str:
        """Generate cache key for performance (UNCHANGED)"""
        import hashlib
        return hashlib.md5(f"{tenant_id}:{query.lower()}".encode()).hexdigest()
    
    def track_intelligence_cost(self, tenant_id: str, cost: float):
        """Track daily intelligence costs (UNCHANGED)"""
        if tenant_id not in self.daily_cost_tracker:
            self.daily_cost_tracker[tenant_id] = 0.0
        self.daily_cost_tracker[tenant_id] += cost
    
    def check_daily_budget(self, tenant_id: str) -> bool:
        """Check if daily budget exceeded (UNCHANGED)"""
        daily_limit = 100000  # Rp 100k daily limit
        current_cost = self.daily_cost_tracker.get(tenant_id, 0)
        return current_cost > daily_limit
    
    def get_polite_deflection(self, tenant_id: str) -> str:
        """Generate polite deflection message (UNCHANGED)"""
        return f"Maaf, pertanyaan tersebut di luar cakupan layanan {tenant_id}. Silakan tanyakan hal lain yang dapat saya bantu."

    def get_polite_deflection_messages(self) -> Dict[str, str]:
        """Get generic polite deflection messages for any tenant (UNCHANGED)"""
        return {
            'default': 'Maaf, pertanyaan ini belum bisa saya jawab. Silakan hubungi customer service untuk bantuan lebih lanjut.',
            'out_of_scope': 'Maaf, pertanyaan tersebut di luar cakupan layanan yang dapat saya bantu saat ini.',
            'contact_support': 'Silakan hubungi customer service untuk bantuan lebih lanjut dengan pertanyaan ini.',
            'try_different': 'Silakan coba pertanyaan lain yang terkait dengan layanan kami, atau hubungi customer service.'
        }

    def generate_tenant_deflection(self, tenant_id: str) -> str:
        """Generate contextual deflection message for any tenant (UNCHANGED)"""
        messages = self.get_polite_deflection_messages()
        
        # Generic template that works for any business
        tenant_name = tenant_id.replace('_', ' ').title()
        
        return f"Maaf, pertanyaan tersebut di luar cakupan layanan {tenant_name} yang dapat saya bantu. {messages['contact_support']}"

# Factory functions (UNCHANGED)
def create_enhanced_confidence_engine():
    """Create enhanced confidence engine instance"""
    return SuperIntelligentConfidenceEngine()

def create_super_intelligent_engine():
    """Create super intelligent confidence engine"""
    return SuperIntelligentConfidenceEngine()