"""
LLM Router - tiered model selection.
Routes requests to appropriate provider + model based on task complexity.
"""

import os
import logging
from enum import Enum
from typing import Dict, Tuple

from .llm_client import LLMClient

logger = logging.getLogger("llm.router")


class TaskComplexity(Enum):
    SIMPLE_READ = "simple_read"
    COMPLEX_READ = "complex_read"
    ACTION = "action"
    SELF_CORRECT = "self_correct"


MODEL_ROUTING: Dict[TaskComplexity, Dict[str, str]] = {
    TaskComplexity.SIMPLE_READ:  {"provider": "openai", "model": "gpt-4o-mini"},
    TaskComplexity.COMPLEX_READ: {"provider": "openai", "model": "gpt-4o-mini"},
    TaskComplexity.ACTION:       {"provider": "openai", "model": "gpt-4o-mini-2024-07-18"},
    TaskComplexity.SELF_CORRECT: {"provider": "openai", "model": "gpt-4o-mini-2024-07-18"},
}


class LLMRouter:
    """Routes LLM requests to appropriate provider and model."""

    def __init__(self, clients: Dict[str, LLMClient],
                 routing: Dict[TaskComplexity, Dict[str, str]] | None = None):
        self.clients = clients
        self.routing = routing or MODEL_ROUTING
        for complexity, config in self.routing.items():
            if config["provider"] not in self.clients:
                logger.warning(f"Provider {config['provider']} for {complexity.value} not registered")

    def get_client_and_model(self, complexity: TaskComplexity) -> Tuple[LLMClient, str]:
        config = self.routing[complexity]
        provider = config["provider"]
        if provider not in self.clients:
            raise KeyError(f"Provider {provider} not registered. Available: {list(self.clients.keys())}")
        return self.clients[provider], config["model"]

    @classmethod
    def from_env(cls) -> "LLMRouter":
        from .openai_client import OpenAIClient
        from .claude_client import ClaudeClient
        from .gemini_client import GeminiClient

        clients: Dict[str, LLMClient] = {}

        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            clients["openai"] = OpenAIClient(api_key=openai_key)
            logger.info("LLM provider registered: openai")

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            clients["claude"] = ClaudeClient(api_key=anthropic_key)
            logger.info("LLM provider registered: claude (stub)")

        google_key = os.environ.get("GOOGLE_API_KEY", "")
        if google_key:
            clients["gemini"] = GeminiClient(api_key=google_key)
            logger.info("LLM provider registered: gemini (stub)")

        if not clients:
            raise RuntimeError("No LLM provider configured. Set OPENAI_API_KEY at minimum.")

        return cls(clients=clients)
