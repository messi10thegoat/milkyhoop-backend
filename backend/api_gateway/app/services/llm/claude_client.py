"""
Anthropic Claude LLM client - STUB.
Ready for implementation when needed.
"""

import logging
from typing import Any, Dict, List

from .llm_client import LLMClient, LLMMessage, LLMResponse

logger = logging.getLogger("llm.claude")


class ClaudeClient(LLMClient):
    """Anthropic Claude API client - STUB."""

    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout

    async def chat(self, messages: List[LLMMessage], tools: List[Dict[str, Any]],
                   model: str, temperature: float = 0.1, max_tokens: int = 4096) -> LLMResponse:
        raise NotImplementedError(
            "ClaudeClient not yet implemented. "
            "Key differences: 1) System prompt separate param. "
            "2) input_schema instead of parameters. "
            "3) tool_result role with tool_use_id."
        )

    def convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{"name": t["name"], "description": t.get("description", ""),
                 "input_schema": t.get("parameters", {"type": "object", "properties": {}})}
                for t in tools]

    def convert_messages(self, messages: List[LLMMessage]) -> Any:
        system_prompt = ""
        claude_msgs = []
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content or ""
            elif msg.role == "tool":
                claude_msgs.append({"role": "user", "content": [{"type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "", "content": msg.content or ""}]})
            else:
                claude_msgs.append({"role": msg.role, "content": msg.content or ""})
        return {"system": system_prompt, "messages": claude_msgs}
