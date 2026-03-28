"""
LLM Abstraction Layer for MilkyHoop.

Provider-agnostic interface for multi-model support.
"""

from .llm_client import LLMClient, LLMMessage, LLMToolCall, LLMResponse
from .openai_client import OpenAIClient
from .gemini_client import GeminiClient
from .llm_router import LLMRouter, TaskComplexity, MODEL_ROUTING, TASK_TYPE_ROUTING

__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMToolCall",
    "LLMResponse",
    "OpenAIClient",
        "GeminiClient",
    "LLMRouter",
    "TaskComplexity",
    "MODEL_ROUTING",
]
