from fastapi import APIRouter
from pydantic import BaseModel
from backend.api_gateway.app.services.ragllm_client import RagLLMClient

router = APIRouter(prefix="/ragllm", tags=["ragllm"])

class RagLLMRequest(BaseModel):
    user_id: str
    session_id: str
    message: str

@router.post("/test")
async def test_ragllm(req: RagLLMRequest):
    client = RagLLMClient()
    try:
        print("🛰️ Calling ragllm_service...")
        answer = await client.generate_answer(
            user_id=req.user_id,
            session_id=req.session_id,
            message=req.message
        )
        print(f"✅ Got answer: {answer}")
        return {"answer": answer}
    except Exception as e:
        print(f"❌ gRPC call failed: {e}")
        return {"error": str(e)}
