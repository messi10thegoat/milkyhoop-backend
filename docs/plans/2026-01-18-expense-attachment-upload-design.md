# Expense Attachment Upload - Design Document

**Date:** 2026-01-18
**Status:** Approved
**Authors:** Backend + Frontend collaboration

---

## Overview

Implement file upload capability for expense receipts, enabling users to attach proof of payment (struk, nota, invoice) to expense records.

## Problem Statement

Frontend sends `has_receipt: true` but files are only stored in client state. Backend needs endpoints to:
1. Accept file uploads
2. Store files in object storage (MinIO)
3. Link attachments to expenses
4. Serve files via signed URLs

## Architecture Decision

**Option A: Upload Terpisah** - SELECTED

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│   Frontend  │────▶│   API Gateway    │────▶│    MinIO    │
│             │     │  (FastAPI)       │     │  (S3-compat)│
└─────────────┘     └──────────────────┘     └─────────────┘
                            │
                            ▼
                    ┌──────────────────┐
                    │   PostgreSQL     │
                    │  (metadata only) │
                    └──────────────────┘
```

**Flow:**
1. User selects file → Frontend uploads to `/api/documents/upload`
2. Backend validates, stores in MinIO, generates thumbnail
3. Backend returns `document_id` + signed URLs
4. Frontend includes `attachment_ids` in expense creation
5. Backend links documents via `document_attachments` table

**Rationale:**
- Database schema already supports polymorphic attachments
- More reliable for large files (retry without losing form data)
- Reusable for other modules (bills, invoices)

---

## Endpoint Specifications

### 1. Upload Document

```
POST /api/documents/upload
Content-Type: multipart/form-data
Authorization: Bearer {token}

Form Fields:
  - file: File (required, max 10MB)
  - category: "receipt" | "invoice" | "other" (optional, default: "other")
  - title: string (optional, default: filename)

Allowed MIME Types:
  - image/jpeg, image/png, image/heic, image/webp
  - application/pdf

Response 201:
{
  "id": "doc-uuid",
  "file_name": "struk-pln.jpg",
  "original_name": "IMG_1234.jpg",
  "file_size": 245678,
  "mime_type": "image/jpeg",
  "width": 1080,
  "height": 1920,
  "url": "https://minio.../signed...",
  "thumbnail_url": "https://minio.../thumb-signed...",
  "category": "receipt",
  "uploaded_at": "2026-01-18T10:30:00Z"
}

Error 400:
{ "error": "file_too_large", "message": "Max file size is 10MB" }
{ "error": "invalid_file_type", "message": "Allowed: jpg, png, heic, webp, pdf" }
```

### 2. Create Expense (Updated)

```
POST /api/expenses
Content-Type: application/json

{
  "expense_date": "2026-01-18",
  "paid_through_id": "bank-uuid",
  "account_id": "coa-uuid",
  "amount": 500000,
  "vendor_id": "vendor-uuid",
  "tax_id": "tax-uuid",
  "reference": "INV-001",
  "notes": "Tagihan listrik",
  "is_billable": true,
  "billed_to_customer_id": "customer-uuid",
  "attachment_ids": ["doc-uuid-1", "doc-uuid-2"]
}

Response 201:
{
  "id": "expense-uuid",
  "expense_number": "EXP-2601-0001",
  "status": "posted",
  "has_receipt": true,
  "attachments": [...],
  ...
}
```

### 3. Get Expense Detail

```
GET /api/expenses/{expense_id}

Response 200:
{
  "id": "expense-uuid",
  "expense_number": "EXP-2601-0001",
  "expense_date": "2026-01-18",

  "account_id": "coa-uuid",
  "account_name": "Biaya Listrik",
  "account_code": "6-10400",

  "paid_through_id": "bank-uuid",
  "paid_through_name": "Kas Kecil",

  "vendor_id": "vendor-uuid",
  "vendor_name": "PLN",

  "subtotal": 500000,
  "tax_id": "tax-uuid",
  "tax_name": "PPN 11%",
  "tax_rate": 11,
  "tax_amount": 55000,
  "total_amount": 555000,

  "is_billable": true,
  "billed_to_customer_id": "customer-uuid",
  "billed_to_customer_name": "PT. ABC",
  "billed_invoice_id": null,

  "has_receipt": true,
  "attachments": [
    {
      "id": "doc-uuid",
      "file_name": "struk-pln.jpg",
      "file_size": 245678,
      "mime_type": "image/jpeg",
      "width": 1080,
      "height": 1920,
      "url": "https://signed-url...",
      "thumbnail_url": "https://signed-url-thumb...",
      "uploaded_at": "2026-01-18T10:30:00Z"
    }
  ],

  "reference": "INV-001",
  "notes": "Tagihan listrik",
  "status": "posted",
  "created_at": "2026-01-18T10:30:00Z",
  "updated_at": "2026-01-18T10:30:00Z"
}
```

### 4. List Expenses (Minimal Attachment Info)

```
GET /api/expenses

Response 200:
{
  "items": [
    {
      "id": "expense-uuid",
      "expense_number": "EXP-2601-0001",
      "has_receipt": true,
      "attachment_count": 2,
      "first_thumbnail_url": "https://signed-url-thumb..."
    }
  ],
  "total": 50
}
```

### 5. Manage Attachments

```
# Add attachment to existing expense
POST /api/expenses/{expense_id}/attachments
{ "document_id": "doc-uuid" }

# Remove attachment
DELETE /api/expenses/{expense_id}/attachments/{attachment_id}

# Download (redirect to signed URL)
GET /api/documents/{document_id}/download
→ 302 Redirect to signed URL
```

---

## Technical Implementation

### New Files

```
backend/api_gateway/app/services/
├── storage_service.py      # MinIO client wrapper
└── thumbnail_service.py    # Image processing (Pillow)
```

### Storage Service

```python
class StorageService:
    async def upload_file(file, tenant_id, category) -> StorageResult
    async def generate_signed_url(file_path, expires_in=3600) -> str
    async def delete_file(file_path) -> bool
```

### Thumbnail Service

```python
class ThumbnailService:
    THUMBNAIL_SIZE = (200, 200)

    async def generate_thumbnail(file_content, mime_type) -> tuple[bytes, int, int]
    def get_image_dimensions(file_content) -> tuple[int, int]
```

### File Path Convention

```
{bucket}/{tenant_id}/{category}/{year}/{month}/{uuid}_{filename}
{bucket}/{tenant_id}/{category}/{year}/{month}/{uuid}_{filename}_thumb.jpg
```

Example:
```
milkyhoop-documents/tenant-abc/receipts/2026/01/a1b2c3d4_struk-pln.jpg
milkyhoop-documents/tenant-abc/receipts/2026/01/a1b2c3d4_struk-pln_thumb.jpg
```

### Configuration

```bash
# .env additions
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=milkyhoop-documents
MINIO_USE_SSL=false
MINIO_URL_EXPIRY=3600
MINIO_THUMBNAIL_EXPIRY=86400
```

### Dependencies

```txt
boto3>=1.34.0
Pillow>=10.0.0
python-multipart>=0.0.6
```

### Docker Compose

```yaml
services:
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - minio_data:/data
```

---

## Validation Rules

| Rule | Value |
|------|-------|
| Max file size | 10MB |
| Max attachments per expense | 5 |
| Allowed image types | jpg, png, heic, webp |
| Allowed document types | pdf |
| Signed URL expiry | 1 hour |
| Thumbnail URL expiry | 24 hours |
| Thumbnail size | 200x200 px |

---

## Database (Already Exists)

Tables ready for use:
- `documents` - File metadata storage
- `document_attachments` - Polymorphic linking
- `expense_attachments` - Direct expense-attachment relation

---

## Frontend Integration

### Field Name Alignment

| Frontend (Current) | Backend (Spec) |
|--------------------|----------------|
| `customer_id` | `billed_to_customer_id` |
| `customer_name` | `billed_to_customer_name` |
| `attachment_id` | `attachment_ids` (array) |

### Files to Update

```
src/components/app/Expenses/AddExpense/
├── index.tsx                    # Update payload field names
├── sheets/ReceiptSheet.tsx      # Support multiple files (max 5)

src/components/app/Expenses/
├── ExpenseItem.tsx              # Show attachment thumbnails
├── ExpenseDetail.tsx            # NEW: Image gallery viewer
```

---

## Implementation Scope

| Component | Action | Priority |
|-----------|--------|----------|
| Storage Service | New | P0 |
| Thumbnail Service | New | P0 |
| Documents Router (upload) | Update | P0 |
| Expenses Router (attachment linking) | Update | P0 |
| Expenses Schema | Update | P0 |
| Docker Compose (MinIO) | Update | P0 |
| Config/Environment | Update | P0 |

---

## Notes

- Virus scanning deferred to Phase 2
- Chunked/resumable upload deferred (10MB limit sufficient)
- OCR for receipt scanning deferred to Phase 2
