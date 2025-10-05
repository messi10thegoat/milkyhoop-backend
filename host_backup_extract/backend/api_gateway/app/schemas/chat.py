from pydantic import BaseModel
from typing import Optional, Dict, Any

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(BaseModel):
    reply: str
    level13_intelligence: Optional[Dict[str, Any]] = None
    mood: Optional[str] = None
    lead_score: Optional[float] = None
    reference_resolved: Optional[bool] = None
    intent: Optional[str] = None
    entities: Optional[Dict[str, Any]] = None
    tenant_id: Optional[str] = None
