-- ============================================================================
-- V049: Documents (Attachments/Lampiran)
-- ============================================================================
-- Purpose: File attachments for any entity (polymorphic)
-- Tables: documents, document_attachments
-- Storage: S3/MinIO (file_path contains S3 key)
-- ============================================================================

-- ============================================================================
-- 1. DOCUMENTS TABLE - File metadata
-- ============================================================================

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- File info
    file_name VARCHAR(255) NOT NULL,
    original_name VARCHAR(255), -- Original filename before sanitization
    file_type VARCHAR(100), -- MIME type (image/jpeg, application/pdf, etc.)
    file_extension VARCHAR(20),
    file_size BIGINT, -- Size in bytes

    -- Storage (S3/MinIO)
    storage_type VARCHAR(20) DEFAULT 's3', -- s3, local
    file_path TEXT NOT NULL, -- S3 key: {tenant_id}/{entity_type}/{year}/{month}/{filename}
    thumbnail_path TEXT, -- For images: smaller version

    -- URLs (pre-signed or CDN)
    file_url TEXT, -- Cached pre-signed URL
    url_expires_at TIMESTAMPTZ, -- URL expiration time

    -- Categorization
    category VARCHAR(50), -- invoice, receipt, contract, photo, report, other
    subcategory VARCHAR(50),

    -- Description & search
    title VARCHAR(255),
    description TEXT,
    tags TEXT[], -- Searchable tags

    -- Image metadata (if applicable)
    width INTEGER,
    height INTEGER,

    -- Checksum for integrity
    checksum_md5 VARCHAR(32),
    checksum_sha256 VARCHAR(64),

    -- Processing status
    processing_status VARCHAR(20) DEFAULT 'completed', -- pending, processing, completed, failed
    processing_error TEXT,

    -- Upload info
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    uploaded_by UUID,
    source VARCHAR(50) DEFAULT 'upload', -- upload, scan, email, api

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ, -- Soft delete

    CONSTRAINT chk_doc_storage CHECK (storage_type IN ('s3', 'local', 'external')),
    CONSTRAINT chk_doc_category CHECK (category IN ('invoice', 'receipt', 'contract', 'photo', 'report', 'statement', 'certificate', 'other')),
    CONSTRAINT chk_doc_status CHECK (processing_status IN ('pending', 'processing', 'completed', 'failed'))
);

COMMENT ON TABLE documents IS 'File storage metadata with S3/MinIO backend';
COMMENT ON COLUMN documents.file_path IS 'S3 key: {tenant_id}/{entity_type}/{year}/{month}/{uuid}_{filename}';
COMMENT ON COLUMN documents.tags IS 'Array of searchable tags';

-- ============================================================================
-- 2. DOCUMENT ATTACHMENTS TABLE - Polymorphic link to entities
-- ============================================================================

CREATE TABLE IF NOT EXISTS document_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- Polymorphic reference
    entity_type VARCHAR(50) NOT NULL, -- invoice, bill, expense, customer, vendor, item, journal, etc.
    entity_id UUID NOT NULL,

    -- Attachment metadata
    attachment_type VARCHAR(50) DEFAULT 'attachment', -- attachment, photo, signature, receipt
    display_order INT DEFAULT 0,

    -- Audit
    attached_at TIMESTAMPTZ DEFAULT NOW(),
    attached_by UUID,

    CONSTRAINT uq_document_attachment UNIQUE(document_id, entity_type, entity_id),
    CONSTRAINT chk_da_entity CHECK (entity_type IN (
        'sales_invoice', 'bill', 'expense', 'customer', 'vendor', 'item',
        'journal', 'quote', 'purchase_order', 'sales_order', 'sales_receipt',
        'payment', 'credit_note', 'vendor_credit', 'stock_adjustment', 'stock_transfer',
        'employee', 'asset', 'project', 'contract', 'other'
    ))
);

COMMENT ON TABLE document_attachments IS 'Links documents to any entity (polymorphic)';
COMMENT ON COLUMN document_attachments.entity_type IS 'Type of entity this document is attached to';

-- ============================================================================
-- 3. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_attachments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_documents ON documents;
DROP POLICY IF EXISTS rls_document_attachments ON document_attachments;

CREATE POLICY rls_documents ON documents
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_document_attachments ON document_attachments
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 4. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_doc_tenant ON documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_doc_category ON documents(tenant_id, category);
CREATE INDEX IF NOT EXISTS idx_doc_uploaded ON documents(tenant_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_tags ON documents USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_doc_filename ON documents(tenant_id, file_name);
CREATE INDEX IF NOT EXISTS idx_doc_type ON documents(tenant_id, file_type);
CREATE INDEX IF NOT EXISTS idx_doc_deleted ON documents(deleted_at) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_da_document ON document_attachments(document_id);
CREATE INDEX IF NOT EXISTS idx_da_entity ON document_attachments(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_da_type_id ON document_attachments(tenant_id, entity_type, entity_id);

-- ============================================================================
-- 5. FUNCTIONS
-- ============================================================================

-- Generate S3 key for a document
CREATE OR REPLACE FUNCTION generate_document_key(
    p_tenant_id TEXT,
    p_entity_type VARCHAR,
    p_filename VARCHAR
) RETURNS TEXT AS $$
DECLARE
    v_uuid TEXT;
    v_year TEXT;
    v_month TEXT;
    v_clean_name TEXT;
BEGIN
    v_uuid := gen_random_uuid()::TEXT;
    v_year := TO_CHAR(CURRENT_DATE, 'YYYY');
    v_month := TO_CHAR(CURRENT_DATE, 'MM');

    -- Sanitize filename (remove special chars, keep extension)
    v_clean_name := REGEXP_REPLACE(p_filename, '[^a-zA-Z0-9._-]', '_', 'g');

    RETURN p_tenant_id || '/' || p_entity_type || '/' || v_year || '/' || v_month || '/' || v_uuid || '_' || v_clean_name;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_document_key IS 'Generates S3 key for document storage';

-- Get documents for an entity
CREATE OR REPLACE FUNCTION get_entity_documents(
    p_tenant_id TEXT,
    p_entity_type VARCHAR,
    p_entity_id UUID
) RETURNS TABLE(
    document_id UUID,
    file_name VARCHAR,
    file_type VARCHAR,
    file_size BIGINT,
    file_url TEXT,
    category VARCHAR,
    title VARCHAR,
    uploaded_at TIMESTAMPTZ,
    attachment_type VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        d.id as document_id,
        d.file_name,
        d.file_type,
        d.file_size,
        d.file_url,
        d.category,
        d.title,
        d.uploaded_at,
        da.attachment_type
    FROM documents d
    JOIN document_attachments da ON d.id = da.document_id
    WHERE d.tenant_id = p_tenant_id
    AND da.entity_type = p_entity_type
    AND da.entity_id = p_entity_id
    AND d.deleted_at IS NULL
    ORDER BY da.display_order ASC, d.uploaded_at DESC;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_entity_documents IS 'Returns all documents attached to an entity';

-- Get document count by entity
CREATE OR REPLACE FUNCTION get_document_count(
    p_tenant_id TEXT,
    p_entity_type VARCHAR,
    p_entity_id UUID
) RETURNS INT AS $$
DECLARE
    v_count INT;
BEGIN
    SELECT COUNT(*)::INT INTO v_count
    FROM document_attachments da
    JOIN documents d ON da.document_id = d.id
    WHERE da.tenant_id = p_tenant_id
    AND da.entity_type = p_entity_type
    AND da.entity_id = p_entity_id
    AND d.deleted_at IS NULL;

    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_document_count IS 'Returns count of documents attached to an entity';

-- Search documents
CREATE OR REPLACE FUNCTION search_documents(
    p_tenant_id TEXT,
    p_search_term VARCHAR DEFAULT NULL,
    p_category VARCHAR DEFAULT NULL,
    p_tags TEXT[] DEFAULT NULL,
    p_limit INT DEFAULT 50,
    p_offset INT DEFAULT 0
) RETURNS TABLE(
    document_id UUID,
    file_name VARCHAR,
    file_type VARCHAR,
    file_size BIGINT,
    category VARCHAR,
    title VARCHAR,
    tags TEXT[],
    uploaded_at TIMESTAMPTZ,
    attachment_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        d.id as document_id,
        d.file_name,
        d.file_type,
        d.file_size,
        d.category,
        d.title,
        d.tags,
        d.uploaded_at,
        (SELECT COUNT(*) FROM document_attachments WHERE document_id = d.id) as attachment_count
    FROM documents d
    WHERE d.tenant_id = p_tenant_id
    AND d.deleted_at IS NULL
    AND (p_search_term IS NULL OR
         d.file_name ILIKE '%' || p_search_term || '%' OR
         d.title ILIKE '%' || p_search_term || '%' OR
         d.description ILIKE '%' || p_search_term || '%')
    AND (p_category IS NULL OR d.category = p_category)
    AND (p_tags IS NULL OR d.tags && p_tags) -- Array overlap
    ORDER BY d.uploaded_at DESC
    LIMIT p_limit
    OFFSET p_offset;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION search_documents IS 'Search documents by name, title, category, or tags';

-- Get storage usage for tenant
CREATE OR REPLACE FUNCTION get_tenant_storage_usage(
    p_tenant_id TEXT
) RETURNS TABLE(
    total_documents BIGINT,
    total_size_bytes BIGINT,
    total_size_mb DECIMAL,
    by_category JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT as total_documents,
        COALESCE(SUM(d.file_size), 0)::BIGINT as total_size_bytes,
        ROUND(COALESCE(SUM(d.file_size), 0) / 1024.0 / 1024.0, 2) as total_size_mb,
        (
            SELECT jsonb_object_agg(
                COALESCE(category, 'uncategorized'),
                jsonb_build_object('count', cnt, 'size_bytes', sz)
            )
            FROM (
                SELECT category, COUNT(*) as cnt, COALESCE(SUM(file_size), 0) as sz
                FROM documents
                WHERE tenant_id = p_tenant_id AND deleted_at IS NULL
                GROUP BY category
            ) cat_stats
        ) as by_category
    FROM documents d
    WHERE d.tenant_id = p_tenant_id
    AND d.deleted_at IS NULL;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_tenant_storage_usage IS 'Returns storage usage statistics for a tenant';

-- ============================================================================
-- 6. TRIGGERS
-- ============================================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_documents_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_documents_updated_at();

-- Extract file extension on insert
CREATE OR REPLACE FUNCTION extract_file_extension()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.file_extension IS NULL AND NEW.file_name IS NOT NULL THEN
        NEW.file_extension := LOWER(SUBSTRING(NEW.file_name FROM '\.([^.]+)$'));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_extract_extension ON documents;
CREATE TRIGGER trg_extract_extension
    BEFORE INSERT ON documents
    FOR EACH ROW EXECUTE FUNCTION extract_file_extension();

-- ============================================================================
-- 7. S3 STORAGE CONFIGURATION NOTES
-- ============================================================================

/*
S3/MinIO Configuration (in .env):

# MinIO/S3 Settings
S3_ENDPOINT=http://minio:9000
S3_ACCESS_KEY=minio_access_key
S3_SECRET_KEY=minio_secret_key
S3_BUCKET=milkyhoop-documents
S3_REGION=us-east-1
S3_USE_SSL=false

# Pre-signed URL expiration (seconds)
S3_URL_EXPIRY=3600

Bucket Policy (public-read for thumbnails):
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": ["arn:aws:s3:::milkyhoop-documents/{tenant_id}/thumbnails/*"]
        }
    ]
}

Lifecycle Rules:
- Delete incomplete multipart uploads after 7 days
- Move to IA storage after 90 days (optional)
*/

-- ============================================================================
-- 8. SUPPORTED FILE TYPES
-- ============================================================================

/*
Supported file types by category:

Images: image/jpeg, image/png, image/gif, image/webp, image/svg+xml
Documents: application/pdf, application/msword, application/vnd.openxmlformats-officedocument.*
Spreadsheets: application/vnd.ms-excel, application/vnd.openxmlformats-officedocument.spreadsheetml.*
Text: text/plain, text/csv
Archives: application/zip, application/x-rar-compressed

Max file size: 50MB (configurable)
Max files per entity: 20 (configurable)

Image processing:
- Auto-generate thumbnails for images (max 300x300)
- Extract dimensions for images
- Convert HEIC to JPEG if needed
*/

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V049: Documents (Attachments) created successfully';
    RAISE NOTICE 'Tables: documents, document_attachments';
    RAISE NOTICE 'Storage: S3/MinIO with pre-signed URLs';
    RAISE NOTICE 'Functions: generate_document_key, get_entity_documents, search_documents';
    RAISE NOTICE 'Categories: invoice, receipt, contract, photo, report, statement, certificate, other';
    RAISE NOTICE 'RLS enabled on all tables';
END $$;
