"""
Google Gemini LLM client — full REST implementation (no SDK).

Uses httpx for HTTP calls, same pattern as OpenAIClient.
Endpoint: generativelanguage.googleapis.com/v1beta
"""

import asyncio
import json
import logging
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional

import httpx

from .llm_client import LLMClient, LLMMessage, LLMToolCall, LLMResponse

logger = logging.getLogger("llm.gemini")

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash-lite"
DEFAULT_TIMEOUT = 60.0


class GeminiClient(LLMClient):
    """Google Gemini API client via REST (no SDK dependency)."""

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def chat(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
        model: str = DEFAULT_MODEL,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: Any = None,
        thinking_budget: Optional[int] = None,
        **_extra: Any,
    ) -> LLMResponse:
        converted = self.convert_messages(messages)
        system_instruction = converted["system_instruction"]
        contents = converted["contents"]

        gen_config: Dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        # Phase 2B-1.7: thinking budget control for gemini-2.5-flash family.
        # thinking_budget=0 disables internal reasoning -> faster on short generation.
        if thinking_budget is not None:
            gen_config["thinkingConfig"] = {"thinkingBudget": int(thinking_budget)}
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_config,
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }

        gemini_tools = self.convert_tools(tools)
        if gemini_tools:
            payload["tools"] = [{"functionDeclarations": gemini_tools}]

        if response_format:
            # Structured output via responseSchema
            payload["generationConfig"]["responseMimeType"] = "application/json"
            if isinstance(response_format, dict):
                schema = response_format.get("json_schema", {}).get("schema")
                if schema:
                    payload["generationConfig"]["responseSchema"] = self._clean_schema(
                        deepcopy(schema)
                    )

        url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={self.api_key}"

        # Retry loop for 429 rate limits
        max_retries = 3
        for attempt in range(max_retries + 1):
            resp = await self._client.post(url, json=payload)

            if resp.status_code == 429 and attempt < max_retries:
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    wait = min(float(retry_after), 30.0)
                else:
                    wait = min(2**attempt * 1.5, 15.0)
                logger.warning(
                    "Gemini 429 rate limit (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    max_retries,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                logger.warning(
                    "Gemini HTTP %d: %s",
                    resp.status_code,
                    resp.text[:600],
                )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_response(data, model)

        # Safety net
        resp.raise_for_status()
        return self._parse_response(resp.json(), model)

    # ── Tool conversion ──────────────────────────────────────────────

    def convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert JSON Schema tool definitions to Gemini FunctionDeclaration format."""
        declarations = []
        for tool in tools:
            params = tool.get("parameters", {"type": "object", "properties": {}})
            declarations.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": self._clean_schema(deepcopy(params)),
                }
            )
        return declarations

    # ── Message conversion ───────────────────────────────────────────

    def convert_messages(self, messages: List[LLMMessage]) -> Dict[str, Any]:
        """Convert LLMMessage list to Gemini contents + system_instruction."""
        system_instruction = ""
        gemini_contents: List[Dict[str, Any]] = []

        for i, msg in enumerate(messages):
            if isinstance(msg, dict):
                msg = LLMMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    tool_call_id=msg.get("tool_call_id"),
                    tool_calls=msg.get("tool_calls"),
                    name=msg.get("name"),
                )

            if msg.role == "system":
                system_instruction = (
                    msg.content
                    if isinstance(msg.content, str)
                    else str(msg.content or "")
                )
                continue

            if msg.role == "assistant" and msg.tool_calls:
                # Assistant with function calls
                parts: List[Dict[str, Any]] = []
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    func = tc.get("function", tc) if isinstance(tc, dict) else tc
                    fname = func.get("name", "") if isinstance(func, dict) else ""
                    raw_args = (
                        func.get("arguments", "{}") if isinstance(func, dict) else "{}"
                    )
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = {}
                    else:
                        args = raw_args
                    parts.append(
                        {
                            "functionCall": {"name": fname, "args": args},
                        }
                    )
                gemini_contents.append({"role": "model", "parts": parts})
                continue

            if msg.role == "tool":
                # Tool result → functionResponse
                gemini_contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": msg.name or "",
                                    "response": {"result": msg.content or ""},
                                },
                            }
                        ],
                    }
                )
                continue

            # Regular user or assistant message
            role = "model" if msg.role == "assistant" else "user"
            parts = self._build_content_parts(msg.content)
            if parts:
                gemini_contents.append({"role": role, "parts": parts})

        return {"system_instruction": system_instruction, "contents": gemini_contents}

    def _build_content_parts(self, content: Any) -> List[Dict[str, Any]]:
        """Build parts list from string or multimodal content blocks."""
        if content is None:
            return [{"text": ""}]
        if isinstance(content, str):
            return [{"text": content}]
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                    elif block.get("type") == "image_url":
                        url_data = block.get("image_url", {})
                        url = (
                            url_data.get("url", "")
                            if isinstance(url_data, dict)
                            else str(url_data)
                        )
                        if url.startswith("data:"):
                            # data:image/png;base64,AAAA...
                            header, b64 = url.split(",", 1) if "," in url else (url, "")
                            mime = header.split(";")[0].replace("data:", "")
                            parts.append(
                                {
                                    "inlineData": {
                                        "mimeType": mime,
                                        "data": b64,
                                    },
                                }
                            )
                        else:
                            parts.append({"text": f"[image: {url}]"})
                else:
                    parts.append({"text": str(block)})
            return parts or [{"text": ""}]
        return [{"text": str(content)}]

    # ── Schema cleaning ──────────────────────────────────────────────

    @staticmethod
    def _clean_schema(schema: Any) -> Any:
        """Clean JSON Schema for Gemini compatibility.

        Removes unsupported keys and converts union types.
        """
        if not isinstance(schema, dict):
            return schema

        # Remove keys Gemini does not support
        for key in ("additionalProperties", "$schema", "title", "default", "strict"):
            schema.pop(key, None)

        # Convert union types: {"type": ["string", "null"]} -> nullable
        t = schema.get("type")
        if isinstance(t, list):
            non_null = [x for x in t if x != "null"]
            has_null = "null" in t
            schema["type"] = non_null[0] if non_null else "string"
            if has_null:
                schema["nullable"] = True

        # Handle anyOf / oneOf: pick first non-null type
        for union_key in ("anyOf", "oneOf"):
            variants = schema.get(union_key)
            if variants and isinstance(variants, list):
                non_null = [v for v in variants if v.get("type") != "null"]
                has_null = len(non_null) < len(variants)
                if non_null:
                    picked = non_null[0]
                    schema.pop(union_key)
                    schema.update(GeminiClient._clean_schema(picked))
                    if has_null:
                        schema["nullable"] = True

        # Recurse into properties
        props = schema.get("properties")
        if isinstance(props, dict):
            for k, v in props.items():
                props[k] = GeminiClient._clean_schema(v)

        # Recurse into items (arrays)
        items = schema.get("items")
        if isinstance(items, dict):
            schema["items"] = GeminiClient._clean_schema(items)

        return schema

    # ── Response parsing ─────────────────────────────────────────────

    def _parse_response(self, data: Dict[str, Any], model: str) -> LLMResponse:
        """Parse Gemini generateContent response into LLMResponse."""
        candidates = data.get("candidates", [])
        if not candidates:
            return LLMResponse(content=None, model=model, finish_reason="error")

        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])
        finish_reason = candidate.get("finishReason", "")

        text_parts: List[str] = []
        tool_calls: List[LLMToolCall] = []

        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_calls.append(
                    LLMToolCall(
                        id=f"call_{uuid.uuid4().hex[:24]}",
                        function_name=fc.get("name", ""),
                        arguments=fc.get("args", {}),
                    )
                )

        usage_meta = data.get("usageMetadata", {})
        usage = {
            "prompt_tokens": usage_meta.get("promptTokenCount", 0),
            "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
            "total_tokens": usage_meta.get("totalTokenCount", 0),
        }

        combined_text = "\n".join(text_parts) if text_parts else None

        return LLMResponse(
            content=combined_text,
            tool_calls=tool_calls,
            model=model,
            usage=usage,
            finish_reason=finish_reason.lower() if finish_reason else "",
            raw_message=content_obj,
        )
