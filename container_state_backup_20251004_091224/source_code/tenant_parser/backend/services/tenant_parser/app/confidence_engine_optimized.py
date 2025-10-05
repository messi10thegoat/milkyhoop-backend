"""
SURGICAL FIX: Optimized Confidence Engine
Targeted fixes without breaking existing architecture:
1. Stricter confidence thresholds
2. Enhanced semantic filtering  
3. Universal prompts (no business-specific terms)
4. Improved topic mismatch detection
"""

import logging
import os
import re
import asyncio
from typing import Dict, Any, Optional, List
from fuzzywuzzy import fuzz
from openai import OpenAI

logger = logging.getLogger(__name__)

class OptimizedConfidenceEngine:
    """
    SURGICAL OPTIMIZATION: Enhanced confidence engine with stricter filtering
    """
    
    def __init__(self):
        self.daily_cost_tracker = {}
        self.cache = {}
        
        try:
            self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        except:
            self.openai_client = None
            logger.warning("OpenAI client not available")
        
        # OPTIMIZED: Truly universal patterns (no industry bias)
        self.universal_patterns = {
            'pricing': ['berapa', 'harga', 'biaya', 'tarif', 'mahal', 'murah', 'bayar', 'cost'],
            'products': ['jenis', 'macam', 'produk', 'layanan', 'service', 'tersedia'],
            'process': ['cara', 'bagaimana', 'gimana', 'langkah', 'proses', 'daftar'],
            'location': ['dimana', 'lokasi', 'alamat', 'kantor', 'tempat', 'outlet'],
            'requirements': ['syarat', 'persyaratan', 'dokumen', 'butuh', 'perlu'],
            'comparison': ['vs', 'atau', 'mana', 'bandingkan', 'beda', 'lebih', 'pilih']
        }
        
        # Enhanced non-business scope filter
        self.non_business_keywords = [
            'cuaca', 'weather', 'politik', 'political', 'resep', 'recipe', 
            'olahraga', 'sport', 'berita', 'news', 'film', 'movie', 
            'musik', 'music', 'game', 'pizza', 'makan', 'masak', 'memasak',
            'cooking', 'travel', 'vacation', 'liburan'
        ]
    
    def enhanced_semantic_filtering(self, query: str, faq_results: List) -> float:
        """
        SURGICAL FIX: Enhanced semantic filtering with stricter topic matching
        """
        if not faq_results:
            return 0.0
        
        query_lower = query.lower()
        best_faq = faq_results[0]
        faq_content = getattr(best_faq, 'content', '').lower()
        
        # Extract semantic keywords
        query_keywords = self.extract_semantic_keywords(query_lower)
        faq_keywords = self.extract_semantic_keywords(faq_content)
        
        logger.info(f"Query keywords: {query_keywords}")
        logger.info(f"FAQ keywords: {faq_keywords}")
        
        # Calculate semantic overlap
        if not query_keywords or not faq_keywords:
            return 0.0
        
        overlap = len(set(query_keywords) & set(faq_keywords))
        total_unique = len(set(query_keywords) | set(faq_keywords))
        
        semantic_score = overlap / total_unique if total_unique > 0 else 0.0
        
        # SURGICAL FIX: Stricter semantic threshold
        if semantic_score < 0.4:  # Raised from 0.3
            logger.info(f"SEMANTIC REJECTION: Score {semantic_score:.3f} below threshold 0.4")
            return 0.0
        
        logger.info(f"Semantic score: {semantic_score:.3f} - PASSED")
        return semantic_score
    
    def extract_semantic_keywords(self, text: str) -> List[str]:
        """Extract meaningful semantic keywords for topic matching"""
        # Remove common words
        stop_words = {
            'yang', 'adalah', 'dan', 'atau', 'untuk', 'dengan', 'dari', 'ke', 'di', 
            'pada', 'ini', 'itu', 'ada', 'tidak', 'ya', 'saya', 'anda', 'kamu'
        }
        
        # Extract meaningful words (3+ characters, not stopwords)
        words = re.findall(r'\b\w{3,}\b', text.lower())
        keywords = [word for word in words if word not in stop_words]
        
        # Extract business entities (capitalized terms in original)
        entities = re.findall(r'\b[A-Z][a-z]+\b', text)
        keywords.extend([entity.lower() for entity in entities])
        
        return list(set(keywords))  # Remove duplicates
    
    def calculate_universal_confidence(self, query: str, faq_results: List = None, tenant_id: str = "default") -> float:
        """
        SURGICAL OPTIMIZATION: Enhanced confidence with stricter filtering
        """
        query_lower = query.lower().strip()
        
        # Universal scope filter
        if any(word in query_lower for word in self.non_business_keywords):
            logger.info(f"[{tenant_id}] SCOPE FILTER: Non-business detected")
            return 0.0
        
        confidence = 0.0
        
        # SURGICAL FIX: Enhanced semantic filtering first
        if faq_results:
            semantic_score = self.enhanced_semantic_filtering(query, faq_results)
            if semantic_score == 0.0:
                logger.info(f"[{tenant_id}] SEMANTIC FILTER: Topic mismatch - confidence set to 0")
                return 0.0
            
            # Base confidence from FAQ similarity
            best_faq = faq_results[0]
            faq_content = getattr(best_faq, 'content', '')
            
            # String similarity
            partial_ratio = fuzz.partial_ratio(query_lower, faq_content.lower()) / 100.0
            token_sort_ratio = fuzz.token_sort_ratio(query_lower, faq_content.lower()) / 100.0
            string_similarity = max(partial_ratio, token_sort_ratio)
            
            # Combined scoring with semantic weight
            if string_similarity > 0.6:
                confidence = string_similarity * 0.6 + semantic_score * 0.4
            else:
                confidence = semantic_score * 0.5
        
        # Universal pattern recognition (reduced boosts)
        pattern_boost = 0.0
        for pattern_type, keywords in self.universal_patterns.items():
            if any(keyword in query_lower for keyword in keywords):
                if pattern_type == 'comparison':
                    pattern_boost = 0.20  # Reduced from 0.25
                elif pattern_type == 'pricing':
                    pattern_boost = 0.15  # Reduced from 0.20
                else:
                    pattern_boost = 0.10  # Reduced from 0.15
                break
        
        confidence += pattern_boost
        
        final_confidence = min(confidence, 1.0)
        logger.info(f"[{tenant_id}] OPTIMIZED confidence: {final_confidence:.3f}")
        
        return final_confidence
    
    def enhanced_decision_engine(self, confidence: float) -> Dict[str, Any]:
        """
        SURGICAL FIX: Stricter thresholds for better deflection
        """
        if confidence >= 0.75:  # High confidence - direct FAQ
            return {
                "route": "direct_faq_only",
                "model": None,
                "cost_per_query": 0.0,
                "tokens_input": 0,
                "tokens_output": 0
            }
        elif confidence >= 0.50:  # RAISED from 0.30 - medium synthesis
            return {
                "route": "gpt35_synthesis", 
                "model": "gpt-3.5-turbo",
                "cost_per_query": 7.0,
                "tokens_input": 300,
                "tokens_output": 150
            }
        elif confidence >= 0.35:  # RAISED from 0.15 - deep analysis
            return {
                "route": "gpt35_deep_analysis",
                "model": "gpt-3.5-turbo",
                "cost_per_query": 15.0,
                "tokens_input": 600,
                "tokens_output": 200
            }
        else:
            return {
                "route": "polite_deflection",
                "model": None,
                "cost_per_query": 0.0,
                "tokens_input": 0,
                "tokens_output": 0
            }
    
    async def call_gpt_35_synthesis(self, query: str, faq_results: List, context_level: str, tenant_id: str) -> str:
        """
        SURGICAL FIX: Universal prompts with strict FAQ constraints
        """
        if not self.openai_client:
            raise Exception("OpenAI client not available")
        
        # Build FAQ context
        context_parts = []
        faq_limit = 2 if context_level == "medium" else 3
        
        for i, faq in enumerate(faq_results[:faq_limit], 1):
            context_parts.append(f"Context {i}: {faq.content}")
        
        context = "\n\n".join(context_parts)
        
        # SURGICAL FIX: Universal prompts (no business-specific bias)
        if context_level == "medium":
            prompt = f"""You are a helpful assistant for {tenant_id}. Answer based ONLY on the provided context.

AVAILABLE CONTEXT:
{context}

USER QUESTION: {query}

STRICT RULES:
- Use ONLY information explicitly available in the context above
- Do NOT add information from general knowledge
- If information is not available in context, say "That information is not available"
- Maximum 3 sentences, natural and helpful
- Be conversational and professional

RESPONSE:"""

        else:  # full context
            prompt = f"""You are a knowledgeable assistant for {tenant_id}. Provide comprehensive guidance based on available context.

COMPLETE CONTEXT:
{context}

USER QUESTION: {query}

STRICT RULES:
- Use ONLY information explicitly stated in the context above
- Analyze user needs and recommend best options from available information
- Compare multiple options if relevant, using ONLY context data
- If information is not in context, clearly state "That information is not available"
- Maximum 4 sentences, structured and informative
- Prioritize accuracy over completeness

COMPREHENSIVE RESPONSE:"""

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200 if context_level == "full" else 150,
                    temperature=0.7
                )
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"[{tenant_id}] GPT synthesis failed: {e}")
            raise e
    
    def extract_answer_from_faq(self, faq_content: str) -> str:
        """Extract clean answer from FAQ content"""
        try:
            if faq_content.startswith("Q:") and "\nA:" in faq_content:
                return faq_content.split("\nA:", 1)[1].strip()
            return faq_content.strip()
        except:
            return faq_content
    
    def get_cache_key(self, tenant_id: str, query: str) -> str:
        """Generate cache key"""
        import hashlib
        return hashlib.md5(f"{tenant_id}:{query.lower()}".encode()).hexdigest()
    
    def track_cost(self, tenant_id: str, cost: float):
        """Track daily costs"""
        if tenant_id not in self.daily_cost_tracker:
            self.daily_cost_tracker[tenant_id] = 0.0
        self.daily_cost_tracker[tenant_id] += cost

# Factory function for easy integration
def create_optimized_confidence_engine():
    """Create optimized confidence engine instance"""
    return OptimizedConfidenceEngine()
