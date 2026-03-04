"""
Streaming Chat Router - NDJSON streaming proxy to OpenAI API.
Used by the Claude-style streaming chat panel in the frontend.

Protocol: Each line is a JSON event (NDJSON).
Events: thinking_start, thinking_delta, thinking_end,
        content_delta, content_end, done, error
"""
import os
import json
import logging
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services.unified_agent.system_prompt import build_system_prompt, get_intent_bias

logger = logging.getLogger("streaming_chat")
router = APIRouter()


# --- Request/Response models ---

class ChatMessage(BaseModel):
    role: str  # user or assistant
    content: str


class StreamingChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = None
    max_tokens: Optional[int] = 4096
    system: Optional[str] = None


# --- NDJSON helpers ---

def ndjson_line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


# --- Auth Helper ---

def _get_user_context(request: Request) -> dict:
    """
    Extract user context from request.state (set by AuthMiddleware).
    Returns dict with tenant info for building ERP-aware system prompt.
    Falls back tenant_name -> tenant_id -> 'MilkyHoop'.
    """
    user = getattr(request.state, "user", None) or {}
    tenant_id = user.get("tenant_id", "")
    return {
        "tenant_id": tenant_id,
        "tenant_name": user.get("tenant_name", tenant_id),
        "user_name": user.get("name", user.get("username", "")),
        "user_role": user.get("role", ""),
    }


# --- Streaming endpoint ---

@router.post("/stream")
async def streaming_chat(request: Request, body: StreamingChatRequest):
    """
    Stream a chat completion from OpenAI API as NDJSON events.
    Converts OpenAI SSE chunks into our NDJSON protocol.

    Now ERP-aware: injects the MilkyHoop constitutional system prompt
    with tenant context from the authenticated user.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY not configured."
        )

    try:
        from openai import OpenAI
    except ImportError:
        raise HTTPException(status_code=503, detail="openai package not installed")

    model = body.model or os.getenv("STREAMING_CHAT_MODEL", "gpt-4o-mini-2024-07-18")

    # --- Build ERP-aware system prompt ---
    ctx = _get_user_context(request)
    tenant_name = ctx.get("tenant_name") or "MilkyHoop"

    if body.system:
        # If client explicitly sends a system prompt, use it (backward compat)
        system_prompt = body.system
    else:
        # Build the full ERP-aware system prompt
        system_prompt = build_system_prompt(
            tenant_name=tenant_name,
            today=date.today().isoformat(),
        )

        # Add intent bias based on the last user message
        user_text = ""
        if body.messages:
            last_msg = body.messages[-1]
            if last_msg.role == "user":
                user_text = last_msg.content
        system_prompt += get_intent_bias(user_text)

        # Add streaming-specific caveat (no tools available)
        system_prompt += (
            "\n\n## STREAMING MODE NOTICE\n"
            "Kamu sedang dalam mode streaming tanpa akses ke tools/API. "
            "Jawab berdasarkan pengetahuan umum tentang akuntansi dan ERP. "
            "Jika user meminta data spesifik (saldo, daftar customer, laporan), "
            "sarankan mereka menggunakan fitur chat utama yang memiliki akses ke data live. "
            "Tetap gunakan persona dan gaya bicara MilkyHoop."
        )

    logger.info(
        "[StreamingChat] tenant=%s user=%s model=%s messages=%d",
        tenant_name, ctx.get("user_name", "?"), model, len(body.messages),
    )

    # Build messages for API
    api_messages = [{"role": "system", "content": system_prompt}]
    api_messages.extend([{"role": m.role, "content": m.content} for m in body.messages])

    async def event_stream():
        client = OpenAI(api_key=api_key)

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=api_messages,
                max_tokens=body.max_tokens or 4096,
                stream=True,
                stream_options={"include_usage": True},
            )

            input_tokens = 0
            output_tokens = 0

            for chunk in stream:
                # Usage comes in the final chunk
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                if delta and delta.content:
                    yield ndjson_line({
                        "type": "content_delta",
                        "text": delta.content,
                    })

                # Check for finish
                if choice.finish_reason:
                    break

            # Send end events
            yield ndjson_line({"type": "content_end"})
            yield ndjson_line({
                "type": "done",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            })

        except Exception as e:
            logger.error("[StreamingChat] OpenAI error: %s", e)
            yield ndjson_line({
                "type": "error",
                "message": f"OpenAI API error: {str(e)}",
            })

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/health")
async def streaming_chat_health():
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    return {
        "status": "ok" if has_key else "unconfigured",
        "openai_key_set": has_key,
        "model": os.getenv("STREAMING_CHAT_MODEL", "gpt-4o-mini-2024-07-18"),
    }
