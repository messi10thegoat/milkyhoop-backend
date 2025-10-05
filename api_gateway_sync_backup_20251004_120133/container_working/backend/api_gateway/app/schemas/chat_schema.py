from pydantic import BaseModel

class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    tenant_id: str
    message: str
