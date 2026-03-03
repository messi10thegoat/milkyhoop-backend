-- Mapping Templates: stores learned column mappings for bank statement imports
-- Used by column_mapper.py Tier 1 (template lookup) for instant cache hits

CREATE TABLE IF NOT EXISTS mapping_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    source_entity VARCHAR(100) NOT NULL DEFAULT '',
    document_type VARCHAR(50) NOT NULL DEFAULT 'bank_statement',
    column_mapping JSONB NOT NULL,
    normalized_columns TEXT NOT NULL,
    date_format VARCHAR(30) DEFAULT 'DD/MM/YYYY',
    decimal_separator VARCHAR(5) DEFAULT ',',
    skip_rows INTEGER DEFAULT 0,
    confidence DECIMAL(5,4) DEFAULT 0.0,
    use_count INTEGER DEFAULT 1,
    last_used_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, normalized_columns)
);

-- Index for fast lookup by tenant + normalized columns
CREATE INDEX IF NOT EXISTS idx_mapping_templates_lookup
    ON mapping_templates(tenant_id, normalized_columns);

-- Index for cleanup of rarely-used templates
CREATE INDEX IF NOT EXISTS idx_mapping_templates_usage
    ON mapping_templates(last_used_at);
