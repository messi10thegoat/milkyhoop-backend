"""
Setup Chat Router - Business Owner Setup Mode
URL: POST /api/setup/chat
Auth: Required (JWT Bearer token)
Purpose: Conversational chatbot setup for business owners
"""

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional, Dict, List
import logging
import uuid

from backend.api_gateway.app.services.setup_orchestrator_client import SetupOrchestratorClient

logger = logging.getLogger(__name__)
router = APIRouter()

setup_client = SetupOrchestratorClient()

class SetupChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None  # Will be populated from JWT
    user_id: Optional[str] = None      # Will be populated from JWT

class BusinessData(BaseModel):
    business_type: Optional[str] = ""
    business_name: Optional[str] = ""
    products_services: Optional[str] = ""
    target_customers: Optional[str] = ""
    hours: Optional[str] = ""
    location: Optional[str] = ""
    pricing: Optional[str] = ""

class FAQSuggestion(BaseModel):
    question: str
    answer: str
    category: str
    confidence: Optional[float] = 0.0

class SetupChatResponse(BaseModel):
    status: str
    mode: str = "setup"
    tenant_id: str
    milky_response: str
    current_state: str
    session_id: str
    progress_percentage: Optional[int] = 0  # NEW: Progress tracking
    business_data: Optional[BusinessData] = None
    suggested_faqs: Optional[List[FAQSuggestion]] = []
    next_action: Optional[str] = ""

@router.post("/chat", response_model=SetupChatResponse)
async def setup_mode_chat(
    body: SetupChatRequest,
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """
    Setup Mode Chat Endpoint
    
    Flow:
    1. Verify JWT token (business owner authentication)
    2. Extract user_id and tenant_id from token OR request body
    3. Check if first-time user (auto-trigger welcome)
    4. Call setup_orchestrator via gRPC
    5. Return structured response with business_data & progress
    
    Example Request:
        POST /api/setup/chat
        Headers: Authorization: Bearer <jwt_token>
        Body: {
            "message": "Bisnis saya cafe, jual kopi dan pastry",
            "session_id": "optional-session-id",
            "tenant_id": "cafeanna",
            "user_id": "anna_owner"
        }
    
    Example Response:
        {
            "status": "success",
            "mode": "setup",
            "tenant_id": "cafeanna",
            "milky_response": "Oke sipp! Jadi kamu punya bisnis cafe ya...",
            "current_state": "collecting_info",
            "session_id": "uuid-here",
            "progress_percentage": 30,
            "business_data": {
                "business_type": "cafe",
                "products_services": "kopi, pastry",
                "pricing_info": "25-50rb"
            },
            "suggested_faqs": [],
            "next_action": "continue_collecting_info"
        }
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authentication required for setup mode. Please provide JWT token."
        )
    
    try:
        # Extract user context from JWT (injected by auth_middleware)
        if not hasattr(request.state, 'user'):
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication state. Token validation failed."
            )
        
        # Use tenant_id and user_id from JWT token (via middleware)
        user_id = request.state.user.get("user_id")
        tenant_id = request.state.user.get("tenant_id")
        
        if not user_id or not tenant_id:
            raise HTTPException(
                status_code=401,
                detail="Missing user_id or tenant_id in JWT token"
            )
        
        # Generate session ID if not provided
        session_id = body.session_id or str(uuid.uuid4())
        
        # NEW: First-time user detection (auto-trigger welcome)
        message = body.message
        if not body.session_id:
            # No session_id = likely first message from new user
            # Check if this looks like an exploratory message
            exploratory_keywords = ["help", "bingung", "apa ini", "gimana", "mulai"]
            if any(keyword in message.lower() for keyword in exploratory_keywords):
                logger.info(f"[Setup] First-time user detected, triggering welcome | user={user_id}")
                message = "__WELCOME__"
        
        logger.info(
            f"[Setup] Processing setup chat | "
            f"user={user_id} | tenant={tenant_id} | session={session_id} | "
            f"message={'[WELCOME_TRIGGER]' if message == '__WELCOME__' else message[:50]}"
        )
        
        # Call setup orchestrator gRPC service
        print(f"🔴 ROUTER DEBUG: user_id={user_id}, tenant_id={tenant_id}", flush=True)
        result = await setup_client.process_setup_chat(
            user_id=user_id,
            tenant_id=tenant_id,
            message=message,
            session_id=session_id
        )
        
        # Handle error response
        if result.get("status") == "error":
            logger.error(f"[Setup] Orchestrator error: {result.get('error')}")
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Unknown error from setup orchestrator")
            )
        
        # Parse business_data if present
        business_data = None
        if result.get("business_data"):
            bd = result["business_data"]
            business_data = BusinessData(
                business_type=bd.get("business_type", ""),
                business_name=bd.get("business_name", ""),
                products_services=bd.get("products_services", ""),
                target_customers=bd.get("target_customers", ""),
                hours=bd.get("hours", ""),
                location=bd.get("location", ""),
                pricing=bd.get("pricing", "")
            )
        
        # Parse suggested_faqs if present
        suggested_faqs = []
        if result.get("suggested_faqs"):
            for faq in result["suggested_faqs"]:
                suggested_faqs.append(FAQSuggestion(
                    question=faq.get("question", ""),
                    answer=faq.get("answer", ""),
                    category=faq.get("category", ""),
                    confidence=faq.get("confidence", 0.0)
                ))
        
        # NEW: Extract progress_percentage from orchestrator
        progress_percentage = result.get("progress_percentage", 0)
        
        logger.info(
            f"[Setup] Success | state={result.get('current_state')} | "
            f"progress={progress_percentage}% | "
            f"business_data_present={business_data is not None} | "
            f"faqs_count={len(suggested_faqs)}"
        )
        
        return SetupChatResponse(
            status="success",
            tenant_id=tenant_id,
            milky_response=result.get("milky_response", ""),
            current_state=result.get("current_state", "initial"),
            session_id=session_id,
            progress_percentage=progress_percentage,  # NEW: Return progress
            business_data=business_data,
            suggested_faqs=suggested_faqs,
            next_action=result.get("next_action", "")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Setup] Unexpected error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )