-- V118__document_intelligence.sql
-- Financial Intelligence Layer — Document Intake Foundation
-- Companion: milkyhoop-ironlaws v3.3, Financial Intelligence Layer v3.0
-- Purpose: Batch tracking + individual document pipeline for OCR/classification/draft review

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- BATCH TRACKING
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE TABLE IF NOT EXISTS document_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id UUID NOT NULL,
    total_documents INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'processing',
        -- processing, completed, partial_failure, cancelled
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_doc_batch_tenant
    ON document_batches(tenant_id, user_id, status);

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- INDIVIDUAL DOCUMENTS
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE TABLE IF NOT EXISTS uploaded_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    batch_id UUID REFERENCES document_batches(id),
    user_id UUID NOT NULL,

    -- File metadata
    original_filename VARCHAR(500) NOT NULL,
    file_path TEXT NOT NULL,
    file_hash VARCHAR(64) NOT NULL,           -- SHA256 of file content
    file_size_bytes BIGINT,
    mime_type VARCHAR(100),

    -- Idempotency: prevent re-processing same file (Law 14)
    idempotency_key VARCHAR(128) NOT NULL,    -- SHA256(file_hash + tenant_id)

    -- Processing pipeline status
    status VARCHAR(30) NOT NULL DEFAULT 'queued',
        -- Pipeline happy path:
        --   queued → extracting → extracted → classifying → classified
        --   → analyzing → analyzed → draft_ready
        --   → confirmed → posting → posted
        --
        -- Error states:
        --   extraction_failed, classification_failed, analysis_failed, posting_failed
        --
        -- Terminal states:
        --   posted, rejected, cancelled
    status_detail TEXT,                       -- Error message or additional info
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,

    -- OCR output (Layer 2)
    ocr_result JSONB,                         -- Structured OCR extraction
    ocr_model_used VARCHAR(50),               -- 'parser', 'haiku', 'sonnet'
    ocr_confidence NUMERIC(4,3),              -- 0.000 to 1.000

    -- Classification output (Layer 3)
    doc_type VARCHAR(50),
        -- invoice_purchase, invoice_sales, receipt, bank_transfer_out,
        -- bank_transfer_in, credit_note, debit_note, unknown
    classification_confidence NUMERIC(4,3),    -- 0.000 to 1.000

    -- Intelligence output (Layer 4)
    analysis_result JSONB,                     -- AR/AP match, inventory match, account recommendation

    -- Draft plan (Layer 5)
    draft_plan JSONB,                          -- DocumentActionPlan: balanced journal + inventory + bank draft

    -- Execution output (Layer 7) — linked AFTER Kernel posts
    journal_entry_id UUID,                     -- Set after posting (no FK — set post-execution)
    inventory_ledger_ids UUID[],               -- Array of inventory_ledger.id (set after posting)
    bank_transaction_id UUID,                  -- Set after posting (no FK — set post-execution)

    -- User review audit
    confirmed_by UUID,                         -- User who confirmed the draft
    confirmed_at TIMESTAMPTZ,
    posted_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotency: same file content for same tenant = reject (Law 14)
-- Only enforced for active documents (not rejected/cancelled)
CREATE UNIQUE INDEX idx_doc_idempotency
    ON uploaded_documents(tenant_id, idempotency_key)
    WHERE status NOT IN ('rejected', 'cancelled');

-- Processing queue lookup (for async worker)
CREATE INDEX idx_doc_processing
    ON uploaded_documents(tenant_id, status, created_at);

-- Batch lookup
CREATE INDEX idx_doc_batch
    ON uploaded_documents(batch_id, status);

-- Journal linkage lookup (untuk traceability — Law 6)
CREATE INDEX idx_doc_journal
    ON uploaded_documents(journal_entry_id)
    WHERE journal_entry_id IS NOT NULL;

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- ROW LEVEL SECURITY (Law 24 — Tenant Isolation)
-- Uses app.tenant_id per existing codebase convention
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALTER TABLE document_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE uploaded_documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_document_batches ON document_batches
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_uploaded_documents ON uploaded_documents
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- updated_at AUTO-TRIGGERS (reuse existing function)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE TRIGGER trg_document_batches_updated_at
    BEFORE UPDATE ON document_batches
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_uploaded_documents_updated_at
    BEFORE UPDATE ON uploaded_documents
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
