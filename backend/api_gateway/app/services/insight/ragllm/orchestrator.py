"""
Insight Orchestrator — LLM + Tool-Use Loop.

Uses OpenAI function calling via httpx (raw HTTP, not SDK).
The LLM decides which API tools to call, system executes them,
LLM narrates the results in natural Indonesian.

Iron Law 0/10: LLM never writes data. All tool calls are GET-only.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from .api_tools import get_tools_for_openai
from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

# OpenAI config
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"
OPENAI_TIMEOUT = 30.0

# Safety guards
MAX_ITERATIONS = 5       # Max tool-use loop iterations
MAX_TOTAL_TOKENS = 4000  # Max output tokens

# System prompt for the insight LLM
SYSTEM_PROMPT = """Kamu adalah asisten akuntansi MilkyHoop yang cerdas dan membantu.

PERAN:
- Kamu menjawab pertanyaan tentang data keuangan, akuntansi, dan bisnis pengguna.
- Kamu HANYA MEMBACA data. Kamu TIDAK PERNAH mengubah, menambah, atau menghapus data.
- Gunakan tools yang tersedia untuk mengambil data yang diperlukan sebelum menjawab.

ATURAN:
1. Selalu ambil data aktual menggunakan tools — JANGAN mengarang angka.
2. Jika data tidak tersedia, katakan "Data tidak ditemukan" — JANGAN hallucinate.
3. Jawab dalam Bahasa Indonesia yang natural dan ringkas.
4. Format angka mata uang dengan "Rp" dan pemisah ribuan titik (contoh: Rp 1.500.000).
5. Jika pertanyaan memerlukan beberapa sumber data, panggil tools yang diperlukan satu per satu.
6. Berikan insight dan analisis singkat di akhir jawaban jika relevan.
7. Jika diminta perbandingan, tunjukkan data periode yang diminta.
8. JANGAN menyarankan pengguna untuk mengubah data melalui chat — arahkan ke menu yang sesuai.

FORMAT JAWABAN:
- Gunakan paragraf pendek (2-3 kalimat per paragraf)
- Gunakan bullet points untuk daftar
- Tebalkan angka penting
- Maksimal 300 kata per jawaban
"""


class InsightOrchestrator:
    """LLM-driven insight engine with tool-use loop."""

    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            logger.warning("OPENAI_API_KEY not set — ragllm will not work")

    async def answer(
        self,
        question: str,
        auth_token: str,
        tenant_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Answer a user question using LLM + tool calls.

        Args:
            question: User's natural language question
            auth_token: Bearer token for API calls
            tenant_id: Tenant slug
            context: Optional tenant context (company name, etc.)

        Returns: {"answer": str, "tools_used": list, "iterations": int, "error": str|None}
        """
        if not self.api_key:
            return {
                "answer": "Maaf, layanan AI belum dikonfigurasi.",
                "tools_used": [],
                "iterations": 0,
                "error": "OPENAI_API_KEY not set",
            }

        start_time = time.time()
        tool_executor = ToolExecutor(auth_token=auth_token, tenant_id=tenant_id)
        tools_used = []

        # Build initial messages
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # Add context if available
        if context:
            ctx_text = f"Konteks bisnis: {json.dumps(context, ensure_ascii=False)}"
            messages.append({"role": "system", "content": ctx_text})

        messages.append({"role": "user", "content": question})

        # Tool-use loop
        iteration = 0
        while iteration < MAX_ITERATIONS:
            iteration += 1

            try:
                response = await self._call_openai(messages)
            except Exception as e:
                logger.error(f"OpenAI call failed (iter {iteration}): {e}")
                return {
                    "answer": "Maaf, terjadi kesalahan saat memproses pertanyaan Anda. Silakan coba lagi.",
                    "tools_used": tools_used,
                    "iterations": iteration,
                    "error": str(e),
                }

            choice = response["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason", "")

            # If LLM wants to call tools
            if message.get("tool_calls"):
                # Append assistant message with tool_calls
                messages.append(message)

                for tool_call in message["tool_calls"]:
                    func_name = tool_call["function"]["name"]
                    try:
                        func_args = json.loads(tool_call["function"]["arguments"])
                    except json.JSONDecodeError:
                        func_args = {}

                    logger.info(f"Iteration {iteration}: calling {func_name}({func_args})")
                    tools_used.append(func_name)

                    # Execute the tool
                    result = await tool_executor.execute_tool(func_name, func_args)

                    # Append tool result as message
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result["data"] if result["success"] else {"error": result["error"]}, default=str, ensure_ascii=False),
                    })

                # Continue loop — LLM will process tool results
                continue

            # If LLM returned a final answer (no more tool calls)
            answer_text = message.get("content", "")
            elapsed = time.time() - start_time
            logger.info(f"InsightOrchestrator: {iteration} iterations, {len(tools_used)} tools, {elapsed:.1f}s")

            return {
                "answer": answer_text,
                "tools_used": tools_used,
                "iterations": iteration,
                "error": None,
            }

        # Max iterations reached
        logger.warning(f"InsightOrchestrator: max iterations ({MAX_ITERATIONS}) reached")
        return {
            "answer": "Maaf, saya memerlukan terlalu banyak langkah untuk menjawab pertanyaan ini. Coba pertanyaan yang lebih spesifik.",
            "tools_used": tools_used,
            "iterations": iteration,
            "error": "max_iterations_reached",
        }

    async def _call_openai(self, messages: List[Dict]) -> Dict:
        """Call OpenAI API with function calling support via httpx."""
        payload = {
            "model": OPENAI_MODEL,
            "messages": messages,
            "tools": get_tools_for_openai(),
            "tool_choice": "auto",
            "max_tokens": MAX_TOTAL_TOKENS,
            "temperature": 0.3,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=OPENAI_TIMEOUT) as client:
            resp = await client.post(OPENAI_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
