import httpx
import os

RAG_HOST = os.getenv("API_GATEWAY_HOST", "http://api_gateway:8000")

async def fetch_faqs_for_tenant(tenant_id: str):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{RAG_HOST}/onboarding/faq", params={"tenant_id": tenant_id})
            response.raise_for_status()
            return response.json().get("faqs", [])
    except Exception as e:
        print(f"❌ Failed to fetch FAQ for tenant {tenant_id}: {e}")
        return []
