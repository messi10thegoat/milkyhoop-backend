"""
Documents Schemas
=================
Pydantic models for file attachments with S3/MinIO storage.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# TYPES
# ============================================================================

StorageType = Literal["s3", "local", "external"]
DocumentCategory = Literal["invoice", "receipt", "contract", "photo", "report", "statement", "certificate", "other"]
ProcessingStatus = Literal["pending", "processing", "completed", "failed"]
AttachmentType = Literal["attachment", "photo", "signature", "receipt"]
EntityType = Literal[
    "sales_invoice", "bill", "expense", "customer", "vendor", "item",
    "journal", "quote", "purchase_order", "sales_order", "sales_receipt",
    "payment", "credit_note", "vendor_credit", "stock_adjustment", "stock_transfer",
    "employee", "asset", "project", "contract", "other"
]


# ============================================================================
# REQUEST MODELS
# ============================================================================

class UploadDocumentRequest(BaseModel):
    """Metadata for document upload (file comes via multipart form)"""
    category: Optional[DocumentCategory] = "other"
    subcategory: Optional[str] = Field(None, max_length=50)
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class UpdateDocumentRequest(BaseModel):
    """Update document metadata"""
    category: Optional[DocumentCategory] = None
    subcategory: Optional[str] = None
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    tags: Optional[List[str]] = None


class AttachDocumentRequest(BaseModel):
    """Attach document to an entity"""
    entity_type: EntityType
    entity_id: UUID
    attachment_type: AttachmentType = "attachment"
    display_order: int = 0


class DetachDocumentRequest(BaseModel):
    """Detach document from an entity"""
    entity_type: EntityType
    entity_id: UUID


class UploadAndAttachRequest(BaseModel):
    """Upload and attach document in one request"""
    entity_type: EntityType
    entity_id: UUID
    category: Optional[DocumentCategory] = "other"
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    attachment_type: AttachmentType = "attachment"


class SearchDocumentsRequest(BaseModel):
    """Search documents"""
    search_term: Optional[str] = None
    category: Optional[DocumentCategory] = None
    tags: Optional[List[str]] = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class DocumentData(BaseModel):
    """Document details"""
    id: UUID
    file_name: str
    original_name: Optional[str] = None
    file_type: Optional[str] = None
    file_extension: Optional[str] = None
    file_size: Optional[int] = None  # bytes
    file_size_formatted: Optional[str] = None  # "2.5 MB"
    storage_type: StorageType
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    category: Optional[DocumentCategory] = None
    subcategory: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    width: Optional[int] = None  # For images
    height: Optional[int] = None
    processing_status: ProcessingStatus
    uploaded_at: datetime
    uploaded_by: Optional[UUID] = None
    attachment_count: int = 0


class DocumentAttachmentData(BaseModel):
    """Document attachment to entity"""
    id: UUID
    document_id: UUID
    entity_type: EntityType
    entity_id: UUID
    attachment_type: AttachmentType
    display_order: int
    attached_at: datetime
    attached_by: Optional[UUID] = None


class DocumentWithAttachments(DocumentData):
    """Document with its attachments"""
    attachments: List[DocumentAttachmentData]


class EntityDocument(BaseModel):
    """Document attached to an entity"""
    document_id: UUID
    file_name: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    category: Optional[DocumentCategory] = None
    title: Optional[str] = None
    uploaded_at: datetime
    attachment_type: AttachmentType
    display_order: int


class DocumentListResponse(BaseModel):
    """Response for document list"""
    success: bool = True
    data: List[DocumentData]
    total: int
    has_more: bool = False


class DocumentDetailResponse(BaseModel):
    """Response for single document"""
    success: bool = True
    data: DocumentWithAttachments


class UploadDocumentResponse(BaseModel):
    """Response for document upload"""
    success: bool = True
    data: DocumentData
    message: str = "Document uploaded successfully"


class UpdateDocumentResponse(BaseModel):
    """Response for document update"""
    success: bool = True
    data: DocumentData
    message: str = "Document updated successfully"


class DeleteDocumentResponse(BaseModel):
    """Response for document deletion"""
    success: bool = True
    message: str = "Document deleted successfully"


class AttachDocumentResponse(BaseModel):
    """Response for attaching document"""
    success: bool = True
    attachment: DocumentAttachmentData
    message: str = "Document attached successfully"


class DetachDocumentResponse(BaseModel):
    """Response for detaching document"""
    success: bool = True
    message: str = "Document detached successfully"


class EntityDocumentsResponse(BaseModel):
    """Response for documents attached to an entity"""
    success: bool = True
    entity_type: EntityType
    entity_id: UUID
    data: List[EntityDocument]
    total: int


class SearchDocumentsResponse(BaseModel):
    """Response for document search"""
    success: bool = True
    query: Optional[str] = None
    category: Optional[DocumentCategory] = None
    tags: Optional[List[str]] = None
    data: List[DocumentData]
    total: int
    has_more: bool = False


class RecentDocumentsResponse(BaseModel):
    """Response for recent documents"""
    success: bool = True
    data: List[DocumentData]
    total: int


# ============================================================================
# STORAGE MODELS
# ============================================================================

class StorageUsageByCategory(BaseModel):
    """Storage usage per category"""
    category: str
    count: int
    size_bytes: int
    size_formatted: str


class StorageUsageData(BaseModel):
    """Storage usage statistics"""
    total_documents: int
    total_size_bytes: int
    total_size_mb: float
    total_size_formatted: str
    by_category: List[StorageUsageByCategory]


class StorageUsageResponse(BaseModel):
    """Response for storage usage"""
    success: bool = True
    data: StorageUsageData


# ============================================================================
# PRESIGNED URL MODELS
# ============================================================================

class PresignedUrlRequest(BaseModel):
    """Request for presigned URL"""
    expires_in: int = Field(default=3600, ge=60, le=86400)  # 1 min to 24 hours


class PresignedUrlResponse(BaseModel):
    """Response for presigned URL"""
    success: bool = True
    document_id: UUID
    url: str
    expires_at: datetime
    method: Literal["GET", "PUT"] = "GET"


class UploadPresignedUrlResponse(BaseModel):
    """Response for upload presigned URL"""
    success: bool = True
    upload_url: str
    document_id: UUID
    file_path: str
    expires_at: datetime
    fields: Optional[Dict[str, Any]] = None  # For multipart form upload
