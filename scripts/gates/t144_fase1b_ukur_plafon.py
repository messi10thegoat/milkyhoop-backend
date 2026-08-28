import asyncio, os, sys, json
sys.path.insert(0,"/app/backend/api_gateway")
from app.services.unified_agent.entity_extractor import build_intent_schema, build_intent_prompt
from app.services.llm import LLMMessage
from app.services.llm.gemini_client import GeminiClient

def mk(n):
    parts=["Kaos Uji T144 "+chr(65+i)+f" {10+i} pcs @ {50000+i*1000}" for i in range(n)]
    return "Buat faktur pembelian dari PT Grosir Kaos, jatuh tempo 30 September 2026, isinya: " + "; ".join(parts)

async def main():
    client=GeminiClient(os.environ["GOOGLE_API_KEY"]); model="gemini-2.5-flash-lite"
    schema=build_intent_schema("create_bill")
    sysc=build_intent_prompt("create_bill",{})
    import httpx
    for n in (2,5,10,14,20,30):
        text=mk(n)
        for mt in (300,4096):
            resp=await client.chat(messages=[LLMMessage(role="system",content=sysc),LLMMessage(role="user",content=text)],tools=[],model=model,temperature=0.1,max_tokens=mt,response_format=schema)
            raw=(resp.content or "")
            ok=True
            try: json.loads(raw.strip().strip("`"))
            except Exception as e: ok=False
            print(f"n={n:>2} max_tokens={mt:>5} chars={len(raw):>5} usage={resp.usage} parse_ok={ok}")
    await client.close() if hasattr(client,"close") else None
asyncio.run(main())
