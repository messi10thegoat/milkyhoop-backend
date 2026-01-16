-- =============================================
-- V062: Multi-Branch (Multi-Cabang)
-- Purpose: Manage multiple branches with separate accounting within same tenant
-- =============================================

-- ============================================================================
-- 1. BRANCHES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS branches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,

    -- Location
    address TEXT,
    city VARCHAR(100),
    province VARCHAR(100),
    postal_code VARCHAR(20),
    country VARCHAR(100) DEFAULT 'Indonesia',
    phone VARCHAR(50),
    email VARCHAR(100),

    -- Hierarchy
    parent_branch_id UUID REFERENCES branches(id),
    branch_level INTEGER DEFAULT 1, -- 1=HQ, 2=Region, 3=Branch

    -- Settings
    is_headquarters BOOLEAN DEFAULT false,
    has_own_sequence BOOLEAN DEFAULT false, -- own invoice numbering

    -- Accounting
    default_warehouse_id UUID REFERENCES warehouses(id),
    default_bank_account_id UUID REFERENCES bank_accounts(id),
    profit_center_id UUID REFERENCES cost_centers(id),

    -- Status
    is_active BOOLEAN DEFAULT true,
    opened_date DATE,
    closed_date DATE,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_branches UNIQUE(tenant_id, code),
    CONSTRAINT chk_branch_level CHECK (branch_level >= 1 AND branch_level <= 5)
);

-- ============================================================================
-- 2. BRANCH SEQUENCES (If has_own_sequence = true)
-- ============================================================================

CREATE TABLE IF NOT EXISTS branch_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,

    document_type VARCHAR(50) NOT NULL, -- invoice, bill, receipt, etc
    prefix VARCHAR(20),
    last_number INTEGER DEFAULT 0,
    last_reset_year INTEGER,

    CONSTRAINT uq_branch_sequences UNIQUE(branch_id, document_type)
);

-- ============================================================================
-- 3. ADD BRANCH_ID TO TRANSACTION TABLES
-- ============================================================================

-- Sales Invoices
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'sales_invoices' AND column_name = 'branch_id') THEN
        ALTER TABLE sales_invoices ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- Bills
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'bills' AND column_name = 'branch_id') THEN
        ALTER TABLE bills ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- Sales Receipts
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'sales_receipts' AND column_name = 'branch_id') THEN
        ALTER TABLE sales_receipts ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- Expenses
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'expenses' AND column_name = 'branch_id') THEN
        ALTER TABLE expenses ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- Journal Entries
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'journal_entries' AND column_name = 'branch_id') THEN
        ALTER TABLE journal_entries ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- Purchase Orders
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'purchase_orders' AND column_name = 'branch_id') THEN
        ALTER TABLE purchase_orders ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- Sales Orders
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'sales_orders' AND column_name = 'branch_id') THEN
        ALTER TABLE sales_orders ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- Stock Transfers
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'stock_transfers' AND column_name = 'branch_id') THEN
        ALTER TABLE stock_transfers ADD COLUMN branch_id UUID REFERENCES branches(id);
    END IF;
END $$;

-- ============================================================================
-- 4. BRANCH PERMISSIONS (Access Control)
-- ============================================================================

CREATE TABLE IF NOT EXISTS branch_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    user_id UUID NOT NULL,
    branch_id UUID NOT NULL REFERENCES branches(id) ON DELETE CASCADE,

    -- Permissions
    can_view BOOLEAN DEFAULT true,
    can_create BOOLEAN DEFAULT false,
    can_edit BOOLEAN DEFAULT false,
    can_delete BOOLEAN DEFAULT false,
    can_approve BOOLEAN DEFAULT false,

    -- Default branch for user
    is_default BOOLEAN DEFAULT false,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_branch_permissions UNIQUE(tenant_id, user_id, branch_id)
);

-- ============================================================================
-- 5. BRANCH TRANSFERS (Goods Between Branches)
-- ============================================================================

CREATE TABLE IF NOT EXISTS branch_transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    transfer_number VARCHAR(50) NOT NULL,
    transfer_date DATE NOT NULL,

    from_branch_id UUID NOT NULL REFERENCES branches(id),
    to_branch_id UUID NOT NULL REFERENCES branches(id),

    -- Stock transfer reference
    stock_transfer_id UUID REFERENCES stock_transfers(id),

    -- Financial
    transfer_price BIGINT NOT NULL, -- at cost or with markup
    pricing_method VARCHAR(20) DEFAULT 'cost', -- cost, markup, market
    markup_percent DECIMAL(5,2),

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, in_transit, received, settled

    -- Settlement
    settlement_date DATE,
    settlement_journal_id UUID REFERENCES journal_entries(id),

    -- Journals
    from_journal_id UUID REFERENCES journal_entries(id),
    to_journal_id UUID REFERENCES journal_entries(id),

    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_branch_transfers UNIQUE(tenant_id, transfer_number),
    CONSTRAINT chk_branch_transfer_pricing CHECK (pricing_method IN ('cost', 'markup', 'market')),
    CONSTRAINT chk_branch_transfer_status CHECK (status IN ('pending', 'in_transit', 'received', 'settled')),
    CONSTRAINT chk_different_branches CHECK (from_branch_id != to_branch_id)
);

-- ============================================================================
-- 6. BRANCH TRANSFER LINES
-- ============================================================================

CREATE TABLE IF NOT EXISTS branch_transfer_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_transfer_id UUID NOT NULL REFERENCES branch_transfers(id) ON DELETE CASCADE,

    product_id UUID NOT NULL REFERENCES products(id),
    quantity DECIMAL(15,4) NOT NULL,
    unit VARCHAR(50),

    unit_cost BIGINT NOT NULL,
    line_total BIGINT NOT NULL,

    -- Batch/Serial
    batch_id UUID REFERENCES item_batches(id),
    serial_ids UUID[],

    notes TEXT
);

-- ============================================================================
-- 7. BRANCH SEQUENCES FOR DOCUMENT NUMBERING
-- ============================================================================

CREATE TABLE IF NOT EXISTS branch_transfer_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'BT',
    last_reset_year INTEGER
);

-- ============================================================================
-- SEED BRANCH ACCOUNTS
-- 1-10950 Piutang Cabang (Branch Receivable)
-- 2-10950 Hutang Cabang (Branch Payable)
-- ============================================================================

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE branch_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE branch_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE branch_transfers ENABLE ROW LEVEL SECURITY;
ALTER TABLE branch_transfer_lines ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_branches ON branches;
CREATE POLICY rls_branches ON branches
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_branch_sequences ON branch_sequences;
CREATE POLICY rls_branch_sequences ON branch_sequences
    USING (branch_id IN (SELECT id FROM branches WHERE tenant_id = current_setting('app.tenant_id', true)));

DROP POLICY IF EXISTS rls_branch_permissions ON branch_permissions;
CREATE POLICY rls_branch_permissions ON branch_permissions
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_branch_transfers ON branch_transfers;
CREATE POLICY rls_branch_transfers ON branch_transfers
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_branch_transfer_lines ON branch_transfer_lines;
CREATE POLICY rls_branch_transfer_lines ON branch_transfer_lines
    USING (branch_transfer_id IN (SELECT id FROM branch_transfers WHERE tenant_id = current_setting('app.tenant_id', true)));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_branches_tenant ON branches(tenant_id);
CREATE INDEX IF NOT EXISTS idx_branches_parent ON branches(parent_branch_id);
CREATE INDEX IF NOT EXISTS idx_branches_active ON branches(tenant_id, is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_branch_permissions_user ON branch_permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_branch_permissions_branch ON branch_permissions(branch_id);
CREATE INDEX IF NOT EXISTS idx_branch_transfers_status ON branch_transfers(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_branch_transfers_date ON branch_transfers(transfer_date);

-- Add indexes for branch_id on transaction tables
CREATE INDEX IF NOT EXISTS idx_sales_invoices_branch ON sales_invoices(branch_id) WHERE branch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bills_branch ON bills(branch_id) WHERE branch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_journal_entries_branch ON journal_entries(branch_id) WHERE branch_id IS NOT NULL;

-- ============================================================================
-- FUNCTION: Generate Branch Transfer Number
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_branch_transfer_number(p_tenant_id TEXT)
RETURNS TEXT AS $$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM CURRENT_DATE);

    INSERT INTO branch_transfer_sequences (tenant_id, last_number, prefix, last_reset_year)
    VALUES (p_tenant_id, 1, 'BT', v_year)
    ON CONFLICT (tenant_id) DO UPDATE SET
        last_number = CASE
            WHEN branch_transfer_sequences.last_reset_year != v_year THEN 1
            ELSE branch_transfer_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || '-' || v_year || '-' || LPAD(v_number::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- FUNCTION: Get Branch Document Number
-- ============================================================================

CREATE OR REPLACE FUNCTION get_branch_document_number(
    p_branch_id UUID,
    p_document_type TEXT
) RETURNS TEXT AS $$
DECLARE
    v_branch RECORD;
    v_prefix VARCHAR(20);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM CURRENT_DATE);

    -- Get branch info
    SELECT * INTO v_branch FROM branches WHERE id = p_branch_id;

    IF NOT v_branch.has_own_sequence THEN
        RETURN NULL; -- Use tenant-level sequence
    END IF;

    -- Get or create branch sequence
    INSERT INTO branch_sequences (branch_id, document_type, prefix, last_number, last_reset_year)
    VALUES (p_branch_id, p_document_type, v_branch.code || '-', 1, v_year)
    ON CONFLICT (branch_id, document_type) DO UPDATE SET
        last_number = CASE
            WHEN branch_sequences.last_reset_year != v_year THEN 1
            ELSE branch_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || v_year || '-' || LPAD(v_number::TEXT, 5, '0');
END;
$$ LANGUAGE plpgsql;
