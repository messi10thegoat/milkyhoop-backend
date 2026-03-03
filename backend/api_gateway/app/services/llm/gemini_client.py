"""
Google Gemini LLM client - STUB.
Ready for implementation when needed.
"""

import logging
from typing import Any, Dict, List

from .llm_client import LLMClient, LLMMessage, LLMResponse

logger = logging.getLogger("llm.gemini")


class GeminiClient(LLMClient):
    """Google Gemini API client - STUB."""

    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout

    async def chat(self, messages: List[LLMMessage], tools: List[Dict[str, Any]],
                   model: str, temperature: float = 0.1, max_tokens: int = 4096) -> LLMResponse:
        raise NotImplementedError(
            "GeminiClient not yet implemented. "
            "Key differences: 1) System instruction separate param. "
            "2) FunctionDeclaration format. 3) FunctionResponse for results."
        )

    def convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{"name": t["name"], "description": t.get("description", ""),
                 "parameters": t.get("parameters", {"type": "object", "properties": {}})}
                for t in tools]

    def convert_messages(self, messages: List[LLMMessage]) -> Any:
        system_instruction = ""
        gemini_msgs = []
        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content or ""
            else:
                role = "model" if msg.role == "assistant" else "user"
                gemini_msgs.append({"role": role, "parts": [{"text": msg.content or ""}]})
        return {"system_instruction": system_instruction, "contents": gemini_msgs}
