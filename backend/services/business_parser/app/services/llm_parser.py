"""
Compatibility wrapper for backward compatibility

This module provides backward-compatible imports from the new modular structure.
All parsing logic has been refactored into app.parsers with the following architecture:

- parsers/keyword_parser.py: Layer 0 - Fast keyword matching (5ms)
- parsers/regex_parser.py: Layer 1 - Regex extraction (10ms)
- parsers/llm_parser.py: Layer 2 - OpenAI GPT classification (800ms)
- parsers/cascade_parser.py: Main orchestrator (Layer 0 → 1 → 2)

Supporting modules:
- utils/: Utilities (fuzzy_match, text_utils)
- extractors/: Entity extractors (item, payment, employee, product)
- prompts/: LLM prompt templates

Author: MilkyHoop Team
Version: 3.0.0 (Refactored 18-Nov-2025)
"""

# Import the main parsing function from new modular structure
# Use absolute import from app.parsers module
from app.parsers.cascade_parser import parse_tenant_intent_entities
from app.parsers.llm_parser import LLMParser

# Create LLMParser instance for accessing _rule_fallback
_llm_parser_instance = LLMParser()

# Export _rule_fallback for backward compatibility with grpc_server.py
def _rule_fallback(text: str):
    """Backward compatibility wrapper for _rule_fallback"""
    return _llm_parser_instance._rule_fallback(text)

# Export for backward compatibility
__all__ = ["parse_tenant_intent_entities", "_rule_fallback"]