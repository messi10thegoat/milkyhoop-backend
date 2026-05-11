"""
OpenAI LLM client implementation.

Uses raw httpx (no SDK dependency) - same pattern as existing orchestrator.
Converts JSON Schema tools -> OpenAI function calling format.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List

import httpx

from .llm_client import LLMClient, LLMMessage, LLMToolCall, LLMResponse

logger = logging.getLogger("llm.openai")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_TIMEOUT = 60.0


class OpenAIClient(LLMClient):
    """OpenAI API client. Wraps httpx calls to chat completions."""

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.timeout = timeout
        # Persistent connection pool — avoids TCP+TLS handshake per call
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format=None,
        **_extra: Any,  # Phase 2B-1.7: absorb provider-specific kwargs (e.g. thinking_budget for Gemini)
    ) -> LLMResponse:
        openai_messages = self.convert_messages(messages)
        openai_tools = self.convert_tools(tools)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            # Structured output mode — cannot combine with tools
            payload["response_format"] = response_format
        elif openai_tools:
            payload["tools"] = openai_tools
            payload["tool_choice"] = "auto"

        # Retry loop for 429 (rate limit) with exponential backoff
        max_retries = 3
        for attempt in range(max_retries + 1):
            resp = await self._client.post(
                OPENAI_API_URL, json=payload, headers=self._headers
            )

            if resp.status_code == 429 and attempt < max_retries:
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    wait = min(float(retry_after), 30.0)
                else:
                    wait = min(2**attempt * 1.5, 15.0)  # 1.5s, 3s, 6s
                logger.warning(
                    "OpenAI 429 rate limit (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    max_retries,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            return self._parse_response(data)

        # Should not reach here, but safety net
        resp.raise_for_status()
        return self._parse_response(resp.json())

    def convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        openai_tools = []
        for tool in tools:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return openai_tools

    def convert_messages(self, messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        openai_msgs = []
        for i, msg in enumerate(messages):
            # Auto-convert dicts to LLMMessage
            if isinstance(msg, dict):
                import logging

                logging.getLogger("unified_agent").warning(
                    f"[WARN] messages[{i}] is dict: {list(msg.keys())}"
                )
                msg = LLMMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    tool_call_id=msg.get("tool_call_id"),
                    tool_calls=msg.get("tool_calls"),
                )
            if msg.role == "tool":
                openai_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "content": msg.content or "",
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                m: Dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": msg.tool_calls,
                }
                if msg.content:
                    m["content"] = msg.content
                openai_msgs.append(m)
            else:
                if isinstance(msg.content, list):
                    # Multimodal content blocks (vision) — pass through as-is
                    openai_msgs.append(
                        {
                            "role": msg.role,
                            "content": msg.content,
                        }
                    )
                else:
                    openai_msgs.append(
                        {
                            "role": msg.role,
                            "content": msg.content or "",
                        }
                    )
        return openai_msgs

    def _parse_response(self, data: Dict[str, Any]) -> LLMResponse:
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")
        usage = data.get("usage", {})

        tool_calls: List[LLMToolCall] = []
        raw_tool_calls = message.get("tool_calls", [])
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                LLMToolCall(
                    id=tc.get("id", ""),
                    function_name=func.get("name", ""),
                    arguments=args,
                )
            )

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            model=data.get("model", ""),
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=finish_reason,
            raw_message=message,
        )
