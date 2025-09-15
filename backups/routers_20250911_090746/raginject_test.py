from fastapi import APIRouter
from pydantic import BaseModel
from backend.api_gateway.app.services.ragcrud_client import RagCrudClient

router = APIRouter()

class InjectRequest(BaseModel):
    tenant_id: str
    title: str
    content: str

@router.post("/rag-inject")
async def rag_inject(request: InjectRequest):
    client = RagCrudClient()
    doc = await client.create_document(
        tenant_id=request.tenant_id,
        title=request.title,
        content=request.content
    )
    return {"status": "ok", "doc_id": doc.id}
