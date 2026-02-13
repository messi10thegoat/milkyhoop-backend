"""
Schemas for Agentic Chat Action Mode
Handles: intent classification, action preview, confirmation, execution
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


class MessageType(str, Enum):
    TEXT = "TEXT"
    ACTION_PREVIEW = "ACTION_PREVIEW"
    ACTION_RESULT = "ACTION_RESULT"
    CLARIFICATION = "CLARIFICATION"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    FILE_RECEIVED = "FILE_RECEIVED"


class ActionType(str, Enum):
    CREATE_PURCHASE_INVOICE = "CREATE_PURCHASE_INVOICE"
    CREATE_SALES_INVOICE = "CREATE_SALES_INVOICE"
    CREATE_VENDOR = "CREATE_VENDOR"
    CREATE_CUSTOMER = "CREATE_CUSTOMER"
    RECEIVE_PAYMENT = "RECEIVE_PAYMENT"
    MAKE_PAYMENT = "MAKE_PAYMENT"


# === REQUEST MODELS ===

class ChatMessageRequest(BaseModel):
    """Request to send a message to the agentic chat"""
    conversation_id: str = Field(..., description="Conversation session ID")
    text: Optional[str] = Field(None, description="Natural language input")


class ConfirmActionRequest(BaseModel):
    """Request to confirm a pending action"""
    conversation_id: str = Field(..., description="Conversation session ID")
    pending_action_id: str = Field(..., description="ID of the pending action to confirm")


class CancelActionRequest(BaseModel):
    """Request to cancel a pending action"""
    conversation_id: str = Field(..., description="Conversation session ID")
    pending_action_id: str = Field(..., description="ID of the pending action to cancel")


# === RESPONSE DATA MODELS ===

class ItemPreview(BaseModel):
    name: str
    qty: int
    unit: str
    price: int
    discount_percent: float = 0
    subtotal: int


class CalculationPreview(BaseModel):
    subtotal: int
    item_discount: int = 0
    invoice_discount: int = 0
    cash_discount: int = 0
    dpp: int
    tax_rate: int
    tax_amount: int
    grand_total: int


class JournalLine(BaseModel):
    account_name: str
    debit: int = 0
    credit: int = 0


class ActionPreviewData(BaseModel):
    """Data for ACTION_PREVIEW message type"""
    pending_action_id: str
    action_type: str
    expires_at: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    items: List[ItemPreview] = Field(default_factory=list)
    calculation: Optional[CalculationPreview] = None
    journal_preview: List[JournalLine] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    side_effects: List[str] = Field(default_factory=list)


class EntityCreated(BaseModel):
    type: str
    id: str
    label: str


class ActionResultData(BaseModel):
    """Data for ACTION_RESULT message type"""
    success: bool
    action_type: str
    entities_created: List[EntityCreated] = Field(default_factory=list)
    journal_entry: Optional[Dict[str, Any]] = None
    impact: Dict[str, str] = Field(default_factory=dict)
    error_message: Optional[str] = None
    suggestions: List[str] = Field(default_factory=list)


class ClarificationOption(BaseModel):
    label: str
    value: str
    description: Optional[str] = None


class ClarificationData(BaseModel):
    """Data for CLARIFICATION message type"""
    question: str
    options: List[ClarificationOption] = Field(default_factory=list)
    allow_freetext: bool = True


class ValidationErrorItem(BaseModel):
    layer: str
    code: str
    message: str


class ValidationErrorData(BaseModel):
    """Data for VALIDATION_ERROR message type"""
    errors: List[ValidationErrorItem] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


# === MAIN RESPONSE MODEL ===

class ChatMessageResponse(BaseModel):
    """Response from agentic chat endpoints"""
    message_id: str = Field(..., description="Unique message ID")
    message_type: MessageType = Field(..., description="Type of message for frontend rendering")
    text: Optional[str] = Field(None, description="Narrative text from LLM/system")
    data: Optional[Dict[str, Any]] = Field(None, description="Typed data payload based on message_type")
    trace_id: Optional[str] = Field(None, description="Trace ID for debugging")
    pending_action_id: Optional[str] = Field(None, description="ID of pending action if applicable")


class ActionStatusResponse(BaseModel):
    """Response for polling action status"""
    pending_action_id: str
    status: str
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
