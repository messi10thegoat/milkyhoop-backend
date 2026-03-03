"""
LLM Abstraction Layer for MilkyHoop.

Provider-agnostic interface for multi-model support.
"""

from .llm_client import LLMClient, LLMMessage, LLMToolCall, LLMResponse
from .openai_client import OpenAIClient
from .claude_client import ClaudeClient
from .gemini_client import GeminiClient
from .llm_router import LLMRouter, TaskComplexity, MODEL_ROUTING

__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMToolCall",
    "LLMResponse",
    "OpenAIClient",
    "ClaudeClient",
    "GeminiClient",
    "LLMRouter",
    "TaskComplexity",
    "MODEL_ROUTING",
]
