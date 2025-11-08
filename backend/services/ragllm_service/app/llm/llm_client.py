import os
import logging
from openai import AsyncOpenAI

logger = logging.getLogger("ragllm_service.llm_client")

# ✅ Inisialisasi client OpenAI
openai_api_key = os.getenv("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None

# ✅ Fungsi reasoning utama
async def call_llm_reasoning(prompt: str) -> str:
    if not client:
        logger.warning("⚠️ OPENAI_API_KEY not set! Returning fallback.")
        return "OPENAI tidak tersedia saat ini."

    try:
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Asisten MilkyHoop yang ramah dan jujur. Jawab hanya dari info yang ada, jangan menebak. Gunakan bahasa natural Indonesia."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"🔥 Gagal memanggil OpenAI: {e}")
        return "Maaf, terjadi kesalahan saat menjawab pertanyaanmu."
