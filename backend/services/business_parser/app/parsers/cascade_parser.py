"""
Cascade parser - Main orchestrator

Implements 3-layer cascade classification:
- Layer 0: Keyword matching (5ms) - 30% traffic
- Layer 1: Regex extraction (10ms) - 30% traffic
- Layer 2: LLM (OpenAI) (800ms) - 40% traffic

This is the main entry point for tenant query parsing.
"""

import logging
from typing import Dict, Any, Optional
from .keyword_parser import KeywordParser
from .regex_parser import RegexParser
from .llm_parser import LLMParser

logger = logging.getLogger(__name__)


def parse_tenant_intent_entities(
    text: str,
    context: Optional[str] = None,
    tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Parse tenant query with 3-layer cascade classification.

    Layer 0: Keyword matching (5ms) - 30% traffic
    Layer 1: Regex extraction (10ms) - 30% traffic
    Layer 2: OpenAI (800ms) - 40% traffic

    Args:
        text: User query text
        context: Optional conversation context
        tenant_id: Optional tenant identifier

    Returns:
        Dict with keys:
        - intent: str
        - entities: dict
        - confidence: float
        - model_used: str

    Example:
        >>> parse_tenant_intent_entities("jual 10 kopi @15rb")
        {'intent': 'transaction_record', 'entities': {...}, 'confidence': 0.90, 'model_used': 'regex'}
    """

    # ===========================================
    # LAYER 0: KEYWORD MATCHING (5ms)
    # ===========================================
    keyword_parser = KeywordParser()
    keyword_result = keyword_parser.parse(text, context, tenant_id)

    if keyword_result and keyword_result.get("confidence", 0) >= 0.75:
        logger.info(f"[PHASE1.4] Layer 0 HIT (keyword): {keyword_result['intent']}")
        return keyword_result

    # ===========================================
    # LAYER 1: REGEX EXTRACTION (10ms)
    # ===========================================
    regex_parser = RegexParser()
    regex_result = regex_parser.parse(text, context, tenant_id)

    if regex_result and regex_result.get("confidence", 0) >= 0.80:
        logger.info(f"[PHASE1.4] Layer 1 HIT (regex): {regex_result['intent']}")
        return regex_result

    # ===========================================
    # LAYER 2: LLM (OPENAI) FALLBACK (800ms)
    # ===========================================
    logger.info(f"[PHASE1.4] Layer 0+1 MISS -> OpenAI fallback")

    llm_parser = LLMParser()
    llm_result = llm_parser.parse(text, context, tenant_id)

    return llm_result