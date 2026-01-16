-- =============================================
-- V060: Report Consolidation (Konsolidasi Laporan)
-- Purpose: Combine financial reports from multiple entities/branches for group reporting
-- =============================================

-- ============================================================================
-- 1. CONSOLIDATION GROUPS (Holding/Parent Company View)
-- ============================================================================

CREATE TABLE IF NOT EXISTS consolidation_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,

    -- Consolidation settings
    consolidation_currency_id UUID REFERENCES currencies(id),
    elimination_method VARCHAR(20) DEFAULT 'full', -- full, proportional, equity

    -- Fiscal settings
    fiscal_year_end_month INTEGER DEFAULT 12,
    fiscal_year_end_day INTEGER DEFAULT 31,

    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_consolidation_groups UNIQUE(tenant_id, code),
    CONSTRAINT chk_elimination_method CHECK (elimination_method IN ('full', 'proportional', 'equity'))
);

-- ============================================================================
-- 2. CONSOLIDATION ENTITIES (Entities within Group)
-- ============================================================================

CREATE TABLE IF NOT EXISTS consolidation_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES consolidation_groups(id) ON DELETE CASCADE,

    -- Entity info (can be same tenant or different)
    entity_tenant_id TEXT NOT NULL,
    entity_name VARCHAR(100) NOT NULL,
    entity_code VARCHAR(50) NOT NULL,

    -- Ownership
    ownership_percent DECIMAL(5,2) NOT NULL DEFAULT 100.00,
    is_parent BOOLEAN DEFAULT false,
    parent_entity_id UUID REFERENCES consolidation_entities(id),

    -- Currency
    functional_currency_id UUID REFERENCES currencies(id),

    -- Status
    consolidation_type VARCHAR(20) DEFAULT 'full', -- full, proportional, equity, none
    is_active BOOLEAN DEFAULT true,
    effective_date DATE,

    CONSTRAINT uq_consolidation_entities UNIQUE(group_id, entity_tenant_id),
    CONSTRAINT chk_ownership_percent CHECK (ownership_percent >= 0 AND ownership_percent <= 100),
    CONSTRAINT chk_consolidation_type CHECK (consolidation_type IN ('full', 'proportional', 'equity', 'none'))
);

-- ============================================================================
-- 3. ACCOUNT MAPPINGS (Map Child Accounts to Parent CoA)
-- ============================================================================

CREATE TABLE IF NOT EXISTS consolidation_account_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES consolidation_groups(id) ON DELETE CASCADE,

    -- Source (child entity)
    source_entity_id UUID NOT NULL REFERENCES consolidation_entities(id) ON DELETE CASCADE,
    source_account_code VARCHAR(50) NOT NULL,

    -- Target (consolidated/parent)
    target_account_code VARCHAR(50) NOT NULL,

    -- Mapping rules
    sign_flip BOOLEAN DEFAULT false,
    elimination_account BOOLEAN DEFAULT false,

    CONSTRAINT uq_consolidation_mappings UNIQUE(group_id, source_entity_id, source_account_code)
);

-- ============================================================================
-- 4. INTERCOMPANY RELATIONSHIPS (For Elimination)
-- ============================================================================

CREATE TABLE IF NOT EXISTS intercompany_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES consolidation_groups(id) ON DELETE CASCADE,

    entity_a_id UUID NOT NULL REFERENCES consolidation_entities(id) ON DELETE CASCADE,
    entity_b_id UUID NOT NULL REFERENCES consolidation_entities(id) ON DELETE CASCADE,

    -- Relationship type
    relationship_type VARCHAR(50), -- parent_subsidiary, sister_companies, etc

    -- Elimination accounts
    ar_account_code VARCHAR(50),
    ap_account_code VARCHAR(50),

    is_active BOOLEAN DEFAULT true,

    CONSTRAINT chk_different_entities CHECK (entity_a_id != entity_b_id)
);

-- ============================================================================
-- 5. CONSOLIDATION RUNS (Snapshots)
-- ============================================================================

CREATE TABLE IF NOT EXISTS consolidation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    group_id UUID NOT NULL REFERENCES consolidation_groups(id) ON DELETE CASCADE,

    -- Period
    period_type VARCHAR(20) NOT NULL, -- monthly, quarterly, yearly
    period_year INTEGER NOT NULL,
    period_month INTEGER,
    period_quarter INTEGER,
    as_of_date DATE NOT NULL,

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, processing, completed, error
    error_message TEXT,

    -- Results (stored as JSONB for flexibility)
    consolidated_trial_balance JSONB,
    consolidated_balance_sheet JSONB,
    consolidated_income_statement JSONB,
    elimination_entries JSONB,

    -- Exchange rates used
    exchange_rates_snapshot JSONB,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_by UUID,

    CONSTRAINT uq_consolidation_runs UNIQUE(group_id, period_type, period_year, period_month, period_quarter),
    CONSTRAINT chk_period_type CHECK (period_type IN ('monthly', 'quarterly', 'yearly')),
    CONSTRAINT chk_status CHECK (status IN ('draft', 'processing', 'completed', 'error'))
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE consolidation_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE consolidation_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE consolidation_account_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE intercompany_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE consolidation_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_consolidation_groups ON consolidation_groups;
CREATE POLICY rls_consolidation_groups ON consolidation_groups
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_consolidation_entities ON consolidation_entities;
CREATE POLICY rls_consolidation_entities ON consolidation_entities
    USING (group_id IN (SELECT id FROM consolidation_groups WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_consolidation_account_mappings ON consolidation_account_mappings;
CREATE POLICY rls_consolidation_account_mappings ON consolidation_account_mappings
    USING (group_id IN (SELECT id FROM consolidation_groups WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_intercompany_relationships ON intercompany_relationships;
CREATE POLICY rls_intercompany_relationships ON intercompany_relationships
    USING (group_id IN (SELECT id FROM consolidation_groups WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_consolidation_runs ON consolidation_runs;
CREATE POLICY rls_consolidation_runs ON consolidation_runs
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_consolidation_groups_tenant ON consolidation_groups(tenant_id);
CREATE INDEX IF NOT EXISTS idx_consolidation_groups_active ON consolidation_groups(tenant_id, is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_consolidation_entities_group ON consolidation_entities(group_id);
CREATE INDEX IF NOT EXISTS idx_consolidation_entities_parent ON consolidation_entities(parent_entity_id);
CREATE INDEX IF NOT EXISTS idx_consolidation_mappings_group ON consolidation_account_mappings(group_id);
CREATE INDEX IF NOT EXISTS idx_consolidation_mappings_source ON consolidation_account_mappings(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_intercompany_relationships_group ON intercompany_relationships(group_id);
CREATE INDEX IF NOT EXISTS idx_consolidation_runs_group ON consolidation_runs(group_id);
CREATE INDEX IF NOT EXISTS idx_consolidation_runs_period ON consolidation_runs(group_id, period_year, period_month);
CREATE INDEX IF NOT EXISTS idx_consolidation_runs_status ON consolidation_runs(tenant_id, status);

-- ============================================================================
-- NOTE: No journal entries - Consolidation is reporting only
-- Elimination entries exist in reports but are not posted to ledger
-- ============================================================================
