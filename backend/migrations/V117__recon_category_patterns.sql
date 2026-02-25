-- V117__recon_category_patterns.sql
-- Category patterns for auto-categorization during bank reconciliation review

CREATE TABLE IF NOT EXISTS recon_category_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,                          -- NULL = system default (TEXT = slug, matches codebase convention)
    pattern_regex VARCHAR(200) NOT NULL,
    account_code VARCHAR(20) NOT NULL,
    description VARCHAR(100) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    is_system_default BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for tenant lookup (tenant-specific + system defaults)
CREATE INDEX IF NOT EXISTS idx_recon_cat_patterns_tenant
    ON recon_category_patterns (tenant_id)
    WHERE tenant_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_recon_cat_patterns_system
    ON recon_category_patterns (is_system_default)
    WHERE is_system_default = true;

-- Seed system defaults (tenant_id = NULL, is_system_default = true)
-- account_codes are resolved to account_id at runtime via CoA API (Law 27)
-- If tenant's CoA doesn't have these codes, pattern match is silently skipped with warning log
INSERT INTO recon_category_patterns (tenant_id, pattern_regex, account_code, description, priority, is_system_default)
VALUES
    (NULL, 'BIAYA ADM|ADMIN FEE|ADM BANK|BIAYA ADMINISTRASI', '6-60100', 'Biaya Admin Bank', 100, true),
    (NULL, 'BUNGA|INTEREST|JASA GIRO|BUNGA BANK', '4-40200', 'Pendapatan Bunga Bank', 90, true),
    (NULL, 'PAJAK|TAX|PPH|PPN|WITHHOLDING', '2-20100', 'Pajak', 80, true),
    (NULL, 'BIAYA TRANSFER|TRF FEE|TRANSFER FEE|BIAYA TRF', '6-60101', 'Biaya Transfer Bank', 70, true)
ON CONFLICT DO NOTHING;
