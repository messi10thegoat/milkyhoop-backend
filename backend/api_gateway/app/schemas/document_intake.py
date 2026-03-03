"""
Document Intake Schemas
=======================
Pydantic models for the Financial Intelligence pipeline.
Covers: upload, batch tracking, review queue, document detail.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# TYPES
# ============================================================================

DocIntakeStatus = Literal[
    "queued",
    "extracting",
    "extracted",
    "classifying",
    "classified",
    "analyzing",
    "analyzed",
    "draft_generating",
    "draft_ready",
    "draft_failed",
    "reviewing",
    "confirmed",
    "posting",
    "posted",
    "failed",
    "extraction_failed",
    "classification_failed",
    "analysis_failed",
    "rejected",
    "cancelled",
]

DocType = Literal[
    "purchase_invoice",
    "sales_invoice",
    "receipt",
    "bank_transfer",
    "expense_receipt",
    "bank_statement",
    "tax_document",
    "unknown",
]


# ============================================================================
# REQUEST MODELS
# ============================================================================

class UploadDocumentIntakeRequest(BaseModel):
    """Metadata sent alongside multipart file upload."""
    doc_type_hint: Optional[DocType] = Field(
        None,
        description="Optional hint for document type (skips classification if confident)",
    )
    batch_id: Optional[UUID] = Field(
        None,
        description="Existing batch to attach to. If None, a new batch is created.",
    )
    notes: Optional[str] = Field(None, max_length=500)


class ConfirmDraftRequest(BaseModel):
    """User confirms a draft plan, optionally with overrides."""
    overrides: Optional[Dict[str, Any]] = Field(
        None,
        description="Field-level overrides to the draft plan before posting.",
    )


class RejectDocumentRequest(BaseModel):
    """User rejects a document with reason."""
    reason: Optional[str] = Field(None, max_length=500)


# ============================================================================
# RESPONSE DATA MODELS
# ============================================================================

class BatchSummary(BaseModel):
    id: UUID
    total_documents: int
    processed_count: int
    failed_count: int
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentIntakeData(BaseModel):
    id: UUID
    batch_id: Optional[UUID] = None
    original_filename: str
    file_hash: str
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    status: DocIntakeStatus
    status_detail: Optional[str] = None
    retry_count: int = 0
    # OCR
    ocr_confidence: Optional[float] = None
    ocr_model_used: Optional[str] = None
    # Classification
    doc_type: Optional[str] = None
    classification_confidence: Optional[float] = None
    # Draft
    draft_plan: Optional[Dict[str, Any]] = None
    # Execution
    journal_entry_id: Optional[UUID] = None
    bank_transaction_id: Optional[UUID] = None
    # Confirmation
    confirmed_by: Optional[UUID] = None
    confirmed_at: Optional[datetime] = None
    posted_at: Optional[datetime] = None
    # Timestamps
    created_at: datetime
    updated_at: datetime


class DocumentIntakeDetail(DocumentIntakeData):
    """Extended detail including OCR result and analysis."""
    ocr_result: Optional[Dict[str, Any]] = None
    analysis_result: Optional[Dict[str, Any]] = None
    inventory_ledger_ids: Optional[List[UUID]] = None


# ============================================================================
# RESPONSE ENVELOPES
# ============================================================================

class UploadDocumentIntakeResponse(BaseModel):
    success: bool = True
    batch: BatchSummary
    documents: List[DocumentIntakeData]
    message: str = "Documents queued for processing"


class BatchStatusResponse(BaseModel):
    success: bool = True
    batch: BatchSummary
    documents: List[DocumentIntakeData]


class DocumentIntakeDetailResponse(BaseModel):
    success: bool = True
    data: DocumentIntakeDetail


class ReviewQueueResponse(BaseModel):
    success: bool = True
    data: List[DocumentIntakeData]
    total: int
    has_more: bool = False


class ConfirmDraftResponse(BaseModel):
    success: bool = True
    data: DocumentIntakeData
    message: str = "Document confirmed and queued for posting"


class RejectDocumentResponse(BaseModel):
    success: bool = True
    message: str = "Document rejected"


# ============================================================================
# PROCESSING MODELS (Phase 3)
# ============================================================================

class ProcessRequest(BaseModel):
    """Trigger document processing."""
    batch_id: Optional[UUID] = Field(
        None,
        description="Process specific batch. If None, process all queued.",
    )
    limit: int = Field(10, ge=1, le=50, description="Max documents to process")


class ProcessResultItem(BaseModel):
    document_id: str
    status: str
    doc_type: Optional[str] = None
    ocr_confidence: Optional[str] = None
    classification_confidence: Optional[str] = None
    model_used: Optional[str] = None
    error: Optional[str] = None


class ProcessResponse(BaseModel):
    success: bool = True
    processed: int
    failed: int
    remaining: int
    details: List[ProcessResultItem]
