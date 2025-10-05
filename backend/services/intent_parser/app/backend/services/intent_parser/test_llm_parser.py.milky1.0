import asyncio
import os
from app.services.llm_parser import parse_intent_entities

async def main():
    # Pastikan OPENAI_API_KEY sudah diset di environment sebelum running script ini
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ ERROR: Environment variable OPENAI_API_KEY belum diset!")
        return

    test_texts = [
        "Saya kecewa sekali dengan cake dari Tart Top. Tidak segar, tidak lezat, dan teksturnya kurang memuaskan.",
        "Halo, saya mau pesan roti gandum 2 loyang dan brownies cokelat 1 kotak.",
        "Tolong cek status pengiriman order 123456 untuk Anton di Bandung."
    ]

    for text in test_texts:
        print("\n=== Test input ===")
        print(text)
        try:
            result = await parse_intent_entities(text)
            print("🎯 Parsed JSON:\n", result)
        except Exception as e:
            print(f"🔥 Error saat parsing: {e}")

if __name__ == "__main__":
    asyncio.run(main())
