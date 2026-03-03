"""
Provider-agnostic LLM client interface.

All providers (OpenAI, Claude, Gemini) implement this interface.
Tool schemas use JSON Schema standard - each provider converts internally.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMMessage:
    """Universal message format across all providers."""
    role: str
    content: Optional[Any] = None  # str for text, List[Dict] for multimodal
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    name: Optional[str] = None


@dataclass
class LLMToolCall:
    """Parsed tool call from LLM response."""
    id: str
    function_name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    content: Optional[str] = None
    tool_calls: List[LLMToolCall] = field(default_factory=list)
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    raw_message: Optional[Dict] = None


class LLMClient(ABC):
    """
    Abstract LLM client. All providers implement this.

    Tool schemas MUST be in JSON Schema standard format:
    {
        "name": "tool_name",
        "description": "What the tool does",
        "parameters": {
            "type": "object",
            "properties": { ... },
            "required": [ ... ]
        }
    }
    """

    @abstractmethod
    async def chat(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def convert_messages(self, messages: List[LLMMessage]) -> Any:
        ...
