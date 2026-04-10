"""
LLM Router - tiered model selection with task-type routing and circuit breaker.
Routes requests to appropriate provider + model based on task complexity or task type.
"""

import os
import time
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import LLMClient, LLMMessage, LLMResponse

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


# Task-type routing: maps semantic task types to provider + model
TASK_TYPE_ROUTING: Dict[str, Dict[str, str]] = {
    "extraction":     {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    "shadow_router":  {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    "field_extract":  {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    "polish":         {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    "clarification":  {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    "chitchat":       {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    "vision_extract": {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
    "agent_loop":     {"provider": "openai", "model": "gpt-4o-mini"},
    "planning":       {"provider": "openai", "model": "gpt-4o-mini"},
    "vision_ocr":     {"provider": "openai", "model": "gpt-4o"},
    "fallback":       {"provider": "openai", "model": "gpt-4o-mini"},
}

# Circuit breaker constants
CB_FAILURE_THRESHOLD = 3
CB_FAILURE_WINDOW = 60.0      # seconds
CB_COOLDOWN = 300.0            # 5 minutes


class _CircuitBreaker:
    """Per-provider circuit breaker. Opens after N failures in a window."""

    def __init__(self):
        self._failures: Dict[str, List[float]] = {}  # provider -> list of failure timestamps
        self._open_until: Dict[str, float] = {}       # provider -> reopen time

    def record_failure(self, provider: str) -> None:
        now = time.monotonic()
        fails = self._failures.setdefault(provider, [])
        fails.append(now)
        # Prune old failures outside the window
        cutoff = now - CB_FAILURE_WINDOW
        self._failures[provider] = [t for t in fails if t > cutoff]
        if len(self._failures[provider]) >= CB_FAILURE_THRESHOLD:
            self._open_until[provider] = now + CB_COOLDOWN
            self._failures[provider] = []
            logger.warning(
                "Circuit breaker OPEN for provider %s — falling back for %.0fs",
                provider, CB_COOLDOWN,
            )

    def record_success(self, provider: str) -> None:
        self._failures.pop(provider, None)

    def is_open(self, provider: str) -> bool:
        deadline = self._open_until.get(provider)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            del self._open_until[provider]
            logger.info("Circuit breaker CLOSED for provider %s", provider)
            return False
        return True


class LLMRouter:
    """Routes LLM requests to appropriate provider and model."""

    def __init__(
        self,
        clients: Dict[str, LLMClient],
        routing: Optional[Dict[TaskComplexity, Dict[str, str]]] = None,
        task_routing: Optional[Dict[str, Dict[str, str]]] = None,
    ):
        self.clients = clients
        self.routing = routing or MODEL_ROUTING
        self.task_routing = task_routing or TASK_TYPE_ROUTING
        self._cb = _CircuitBreaker()

        for complexity, config in self.routing.items():
            if config["provider"] not in self.clients:
                logger.warning("Provider %s for %s not registered", config["provider"], complexity.value)

    # ── Legacy interface (backwards compat) ──────────────────────────

    def get_client_and_model(self, complexity: TaskComplexity) -> Tuple[LLMClient, str]:
        config = self.routing[complexity]
        provider = config["provider"]
        if provider not in self.clients:
            raise KeyError(f"Provider {provider} not registered. Available: {list(self.clients.keys())}")
        return self.clients[provider], config["model"]

    # ── Task-type routing with circuit breaker ───────────────────────

    def route(self, task_type: str) -> Tuple[LLMClient, str]:
        """Return (client, model) for a task type, respecting circuit breaker."""
        config = self.task_routing.get(task_type, self.task_routing["fallback"])
        provider = config["provider"]
        model = config["model"]

        # If circuit breaker is open for this provider, fall back to OpenAI
        if self._cb.is_open(provider) and provider != "openai":
            fallback = self.task_routing["fallback"]
            provider = fallback["provider"]
            model = fallback["model"]
            logger.info("Circuit breaker: rerouting %s → %s/%s", task_type, provider, model)

        if provider not in self.clients:
            # Provider not available, fall back to OpenAI
            fallback = self.task_routing["fallback"]
            provider = fallback["provider"]
            model = fallback["model"]

        return self.clients[provider], model

    async def complete(
        self,
        task_type: str,
        messages: List[LLMMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: Any = None,
    ) -> LLMResponse:
        """High-level: route, call, fallback on error."""
        client, model = self.route(task_type)
        config = self.task_routing.get(task_type, self.task_routing["fallback"])
        provider = config["provider"]

        try:
            kwargs: Dict[str, Any] = {
                "messages": messages,
                "tools": tools or [],
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format
            resp = await client.chat(**kwargs)
            self._cb.record_success(provider)
            return resp
        except Exception as exc:
            logger.warning("LLM call failed (%s/%s): %s", provider, model, exc)
            self._cb.record_failure(provider)

            # Fallback to OpenAI if primary was different
            fallback_cfg = self.task_routing["fallback"]
            fb_provider = fallback_cfg["provider"]
            fb_model = fallback_cfg["model"]
            if fb_provider != provider and fb_provider in self.clients:
                logger.info("Falling back to %s/%s for task_type=%s", fb_provider, fb_model, task_type)
                fb_client = self.clients[fb_provider]
                kwargs["model"] = fb_model
                return await fb_client.chat(**kwargs)
            raise

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "LLMRouter":
        from .openai_client import OpenAIClient
        from .gemini_client import GeminiClient

        clients: Dict[str, LLMClient] = {}

        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            clients["openai"] = OpenAIClient(api_key=openai_key)
            logger.info("LLM provider registered: openai")

        google_key = os.environ.get("GOOGLE_API_KEY", "")
        if google_key:
            clients["gemini"] = GeminiClient(api_key=google_key)
            logger.info("LLM provider registered: gemini")

        if not clients:
            raise RuntimeError("No LLM provider configured. Set OPENAI_API_KEY at minimum.")

        return cls(clients=clients)
