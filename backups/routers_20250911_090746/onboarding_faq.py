from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from pydantic import BaseModel
import logging

from ..prisma_client import get_prisma_client

logger = logging.getLogger(__name__)
router = APIRouter()

class FAQResponse(BaseModel):
    id: int
    question: str
    answer: str
    tenant_id: str

class FAQListResponse(BaseModel):
    faqs: List[FAQResponse]
    total: int

@router.get("/onboarding/faq", response_model=FAQListResponse)
async def get_tenant_faqs(tenant_id: str):
    """Get FAQ data from RagDocument table with tenantId column"""
    try:
        prisma = get_prisma_client()
        
        # Query RagDocument with tenantId column (not tenant_id)
        documents = await prisma.ragdocument.find_many(
            where={
                "tenantId": tenant_id
            },
            order_by={
                "id": "asc"
            }
        )
        
        # Transform RagDocument to FAQ format
        faqs = []
        for doc in documents:
            # Extract question and answer from content
            content = doc.content or ""
            lines = content.split('\n')
            
            question = doc.title.replace("FAQ: ", "").replace("...", "")
            answer = content.split('A: ', 1)[1] if 'A: ' in content else content
            
            faqs.append(FAQResponse(
                id=doc.id,
                question=question,
                answer=answer,
                tenant_id=doc.tenantId
            ))
        
        logger.info(f"✅ Retrieved {len(faqs)} FAQs for tenant {tenant_id}")
        
        return FAQListResponse(
            faqs=faqs,
            total=len(faqs)
        )
        
    except Exception as e:
        logger.error(f"❌ Error retrieving FAQs for tenant {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve FAQs: {str(e)}")

@router.post("/onboarding/faq")
async def create_faq(tenant_id: str, question: str, answer: str):
    """Create new FAQ in RagDocument table"""
    try:
        prisma = get_prisma_client()
        
        # Create new RagDocument entry
        new_doc = await prisma.ragdocument.create(
            data={
                "tenantId": tenant_id,
                "title": f"FAQ: {question}",
                "content": f"Q: {question}\nA: {answer}",
                "source": "manual_entry",
                "embeddings": []
            }
        )
        
        logger.info(f"✅ Created new FAQ for tenant {tenant_id}")
        
        return {"id": new_doc.id, "message": "FAQ created successfully"}
        
    except Exception as e:
        logger.error(f"❌ Error creating FAQ for tenant {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create FAQ: {str(e)}")
