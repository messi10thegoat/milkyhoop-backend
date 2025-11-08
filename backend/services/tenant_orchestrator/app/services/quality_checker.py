"""
Quality Checker Service - OpenAI Powered Message Quality Assessment
Prevents garbage data and saves LLM costs by filtering junk/low-quality messages
"""

import logging
import os
import json
from typing import Dict
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class QualityChecker:
    """
    OpenAI-powered message quality assessment for setup conversations
    
    Features:
    - Strict quality scoring (0-10 per dimension)
    - Junk detection (random characters, test inputs)
    - Low-quality filtering (greetings without context)
    - Cost optimization (saves 30-40% on downstream LLM calls)
    - Fallback mechanism (defaults to medium on error)
    
    Technical:
    - Uses OpenAI AsyncOpenAI client (v1.x compatible)
    - GPT-3.5-turbo for cost efficiency (~$0.0001 per check)
    - Async/await compatible
    - Zero field mapping dependencies
    """
    
    def __init__(self):
        """Initialize OpenAI client with API key from environment"""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY not found in environment")
            raise ValueError("OPENAI_API_KEY is required for QualityChecker")
        
        self.client = AsyncOpenAI(api_key=api_key)
        logger.info("QualityChecker initialized with OpenAI client")
    
    async def assess_message_quality(self, message: str) -> Dict:
        """
        Assess message quality using GPT-3.5-turbo
        
        Args:
            message: User input message to assess
            
        Returns:
            Dict with quality assessment:
            {
                "clarity_score": 0-10,
                "relevance_score": 0-10,
                "completeness_score": 0-10,
                "actionability_score": 0-10,
                "overall_quality": "high|medium|low|junk",
                "should_extract": true|false,
                "suggested_action": "extract|clarify|greet|reject",
                "reasoning": "brief explanation"
            }
        
        Cost: ~$0.0001 per assessment
        Latency: ~200-300ms
        """
        try:
            # Build quality assessment prompt
            quality_prompt = self._build_quality_prompt(message)
            
            # Call OpenAI API
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict quality assessment system for business setup messages. Return ONLY valid JSON without markdown."
                    },
                    {
                        "role": "user",
                        "content": quality_prompt
                    }
                ],
                temperature=0.3,
                max_tokens=200
            )
            
            # Parse response
            content = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:].strip()
            
            # Parse JSON
            result = json.loads(content)
            
            # Validate required fields
            required_fields = [
                "clarity_score", "relevance_score", "completeness_score",
                "actionability_score", "overall_quality", "should_extract",
                "suggested_action", "reasoning"
            ]
            
            for field in required_fields:
                if field not in result:
                    logger.warning(f"Missing field in quality response: {field}")
                    return self._fallback_response(message, f"Missing field: {field}")
            
            # Log assessment
            logger.info(
                f"Quality assessment: {result['overall_quality']} "
                f"(clarity={result['clarity_score']}, "
                f"relevance={result['relevance_score']}) - "
                f"{result['reasoning']}"
            )
            
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse quality response JSON: {str(e)}")
            return self._fallback_response(message, f"JSON parse error: {str(e)}")
            
        except Exception as e:
            logger.error(f"Quality assessment error: {str(e)}")
            return self._fallback_response(message, f"Assessment error: {str(e)}")
    
    def _build_quality_prompt(self, message: str) -> str:
        """
        Build prompt for quality assessment
        
        Args:
            message: User message to assess
            
        Returns:
            Formatted prompt for OpenAI
        """
        return f"""Analyze this business setup message quality with STRICT scoring:

Message: "{message}"

Score 0-10 for each dimension (be VERY strict!):

1. CLARITY (Is intent clear?)
   - 0-2: Gibberish, random characters (e.g., "asdfgh", "test123")
   - 3-4: Unclear, ambiguous
   - 5-6: Somewhat clear but needs interpretation
   - 7-8: Clear intent
   - 9-10: Crystal clear, explicit

2. RELEVANCE (Related to business setup?)
   - 0-2: Random chat, off-topic, greetings only
   - 3-4: Vaguely business-related
   - 5-6: Business context present
   - 7-8: Clear business information
   - 9-10: Specific business details

3. COMPLETENESS (Contains useful info?)
   - 0-2: Just greeting "halo", "hi", no content
   - 3-4: Minimal information
   - 5-6: Some details provided
   - 7-8: Good amount of information
   - 9-10: Comprehensive details

4. ACTIONABILITY (Can we extract data?)
   - 0-2: Nothing to extract
   - 3-4: Very little extractable
   - 5-6: Some extractable data
   - 7-8: Clear extractable information
   - 9-10: Multiple clear data points

CLASSIFICATION RULES (follow strictly):
- JUNK: clarity < 3 OR message is random characters/test input
- LOW: relevance < 4 OR just greeting without business context
- MEDIUM: All scores 4-6, has business intent but needs more info
- HIGH: All scores >= 7, clear actionable business information

Return ONLY valid JSON (no markdown, no code blocks):
{{
    "clarity_score": X,
    "relevance_score": Y,
    "completeness_score": Z,
    "actionability_score": W,
    "overall_quality": "high|medium|low|junk",
    "should_extract": true|false,
    "suggested_action": "extract|clarify|greet|reject",
    "reasoning": "one-sentence explanation"
}}

Examples for calibration:
- "asdfgh" -> JUNK (clarity=0, random characters)
- "halo aja" -> LOW (relevance=2, just greeting)
- "saya punya cafe" -> MEDIUM (some info but incomplete)
- "cafe saya bernama Kopi Santai, buka 8 pagi - 10 malam" -> HIGH (clear details)"""
    
    def _fallback_response(self, message: str, error_reason: str) -> Dict:
        """
        Fallback response when quality assessment fails
        
        Default to MEDIUM quality to not block legitimate users
        
        Args:
            message: Original message
            error_reason: Why assessment failed
            
        Returns:
            Safe fallback assessment
        """
        logger.warning(f"Using fallback response: {error_reason}")
        
        # Simple heuristic checks for obvious junk
        message_clean = message.strip().lower()
        
        # Check for obvious junk patterns
        if len(message_clean) < 3:
            return {
                "clarity_score": 1,
                "relevance_score": 1,
                "completeness_score": 1,
                "actionability_score": 1,
                "overall_quality": "junk",
                "should_extract": False,
                "suggested_action": "reject",
                "reasoning": "Message too short (fallback detection)"
            }
        
        # Check for random characters (high ratio of non-alphabetic)
        alphabetic_ratio = sum(c.isalpha() for c in message_clean) / len(message_clean)
        if alphabetic_ratio < 0.5:
            return {
                "clarity_score": 2,
                "relevance_score": 2,
                "completeness_score": 2,
                "actionability_score": 2,
                "overall_quality": "junk",
                "should_extract": False,
                "suggested_action": "reject",
                "reasoning": "High non-alphabetic ratio (fallback detection)"
            }
        
        # Default to medium quality (safe fallback)
        return {
            "clarity_score": 5,
            "relevance_score": 5,
            "completeness_score": 5,
            "actionability_score": 5,
            "overall_quality": "medium",
            "should_extract": True,
            "suggested_action": "clarify",
            "reasoning": f"Fallback assessment due to: {error_reason}"
        }


# Global instance for easy import
quality_checker = QualityChecker()
