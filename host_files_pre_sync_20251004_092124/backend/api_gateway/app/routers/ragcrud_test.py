from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from backend.api_gateway.app.services.ragcrud_client import RagCrudClient

router = APIRouter()

# Model untuk request create FAQ
class FAQCreateRequest(BaseModel):
    tenant_id: str
    title: str
    content: str
    tags: List[str]
    source: str

# Endpoint GET untuk list dokumen RAG
@router.get("/test-ragcrud")
async def test_ragcrud():
    try:
        client = RagCrudClient(host="ragcrud_service", port=5001)
        docs = await client.list_documents()
        return {"documents": [d.to_dict() for d in docs]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"gRPC call failed: {str(e)}")

# Endpoint POST untuk create FAQ
@router.post("/create")
async def create_faq(faq: FAQCreateRequest):
    try:
        client = RagCrudClient(host="ragcrud_service", port=5001)
        doc = await client.create_document(
            tenant_id=faq.tenant_id,
            title=faq.title,
            content=faq.content,
            source=faq.source,
            tags=faq.tags
        )
        return {"message": "FAQ created", "doc_id": doc.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create failed: {str(e)}")
