-- =============================================
-- V061: Intercompany Transactions (Transaksi Antar Cabang)
-- Purpose: Record and reconcile transactions between entities within a group
-- =============================================

-- ============================================================================
-- 1. INTERCOMPANY TRANSACTIONS
-- ============================================================================

CREATE TABLE IF NOT EXISTS intercompany_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Transaction info
    transaction_number VARCHAR(50) NOT NULL,
    transaction_date DATE NOT NULL,
    description TEXT,

    -- Parties
    from_entity_tenant_id TEXT NOT NULL,
    to_entity_tenant_id TEXT NOT NULL,

    -- Type
    transaction_type VARCHAR(50) NOT NULL, -- sale, purchase, loan, expense_allocation, transfer

    -- Amounts
    amount BIGINT NOT NULL,
    currency_id UUID REFERENCES currencies(id),
    exchange_rate DECIMAL(15,6) DEFAULT 1,

    -- Reference documents
    from_document_type VARCHAR(50),
    from_document_id UUID,
    from_document_number VARCHAR(100),
    to_document_type VARCHAR(50),
    to_document_id UUID,
    to_document_number VARCHAR(100),

    -- Status
    from_status VARCHAR(20) DEFAULT 'pending', -- pending, confirmed, reconciled
    to_status VARCHAR(20) DEFAULT 'pending',

    -- Journals
    from_journal_id UUID REFERENCES journal_entries(id),
    to_journal_id UUID REFERENCES journal_entries(id),

    -- Reconciliation
    is_reconciled BOOLEAN DEFAULT false,
    reconciled_at TIMESTAMPTZ,
    reconciled_by UUID,
    variance_amount BIGINT DEFAULT 0,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_intercompany_transactions UNIQUE(tenant_id, transaction_number),
    CONSTRAINT chk_ic_transaction_type CHECK (transaction_type IN ('sale', 'purchase', 'loan', 'expense_allocation', 'transfer')),
    CONSTRAINT chk_ic_from_status CHECK (from_status IN ('pending', 'confirmed', 'reconciled', 'rejected')),
    CONSTRAINT chk_ic_to_status CHECK (to_status IN ('pending', 'confirmed', 'reconciled', 'rejected')),
    CONSTRAINT chk_different_entities CHECK (from_entity_tenant_id != to_entity_tenant_id)
);

-- ============================================================================
-- 2. INTERCOMPANY BALANCES (Running Balance Between Entities)
-- ============================================================================

CREATE TABLE IF NOT EXISTS intercompany_balances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Entities
    entity_a_tenant_id TEXT NOT NULL,
    entity_b_tenant_id TEXT NOT NULL,

    -- Balance (positive = A owes B, negative = B owes A)
    balance BIGINT DEFAULT 0,
    currency_id UUID REFERENCES currencies(id),

    last_transaction_date DATE,
    last_reconciled_date DATE,

    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_intercompany_balances UNIQUE(tenant_id, entity_a_tenant_id, entity_b_tenant_id)
);

-- ============================================================================
-- 3. INTERCOMPANY SEQUENCES
-- ============================================================================

CREATE TABLE IF NOT EXISTS intercompany_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'IC',
    last_reset_year INTEGER
);

-- ============================================================================
-- 4. INTERCOMPANY SETTLEMENT (Payment/Settlement Between Entities)
-- ============================================================================

CREATE TABLE IF NOT EXISTS intercompany_settlements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    settlement_number VARCHAR(50) NOT NULL,
    settlement_date DATE NOT NULL,

    -- Parties
    payer_tenant_id TEXT NOT NULL,
    payee_tenant_id TEXT NOT NULL,

    -- Amount
    amount BIGINT NOT NULL,
    currency_id UUID REFERENCES currencies(id),

    -- Method
    settlement_method VARCHAR(50) DEFAULT 'bank_transfer', -- bank_transfer, offset, cash
    reference_number VARCHAR(100),

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, completed, cancelled

    -- Journals
    payer_journal_id UUID REFERENCES journal_entries(id),
    payee_journal_id UUID REFERENCES journal_entries(id),

    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_ic_settlements UNIQUE(tenant_id, settlement_number),
    CONSTRAINT chk_settlement_status CHECK (status IN ('pending', 'completed', 'cancelled'))
);

-- ============================================================================
-- SEED INTERCOMPANY ACCOUNTS
-- These should be added to chart_of_accounts if not exist
-- 1-10900 Piutang Antar Cabang (Intercompany Receivable)
-- 2-10900 Hutang Antar Cabang (Intercompany Payable)
-- 4-10200 Penjualan Antar Cabang (Intercompany Sales)
-- 5-10400 Pembelian Antar Cabang (Intercompany Purchases)
-- ============================================================================

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE intercompany_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE intercompany_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE intercompany_settlements ENABLE ROW LEVEL SECURITY;

-- RLS for IC transactions - allows access if user is from either party
DROP POLICY IF EXISTS rls_intercompany_transactions ON intercompany_transactions;
CREATE POLICY rls_intercompany_transactions ON intercompany_transactions
    USING (
        tenant_id = current_setting('app.tenant_id', true) OR
        from_entity_tenant_id = current_setting('app.tenant_id', true) OR
        to_entity_tenant_id = current_setting('app.tenant_id', true)
    );

DROP POLICY IF EXISTS rls_intercompany_balances ON intercompany_balances;
CREATE POLICY rls_intercompany_balances ON intercompany_balances
    USING (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS rls_intercompany_settlements ON intercompany_settlements;
CREATE POLICY rls_intercompany_settlements ON intercompany_settlements
    USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_ic_transactions_tenant ON intercompany_transactions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ic_transactions_from ON intercompany_transactions(from_entity_tenant_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_ic_transactions_to ON intercompany_transactions(to_entity_tenant_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_ic_transactions_status ON intercompany_transactions(is_reconciled) WHERE NOT is_reconciled;
CREATE INDEX IF NOT EXISTS idx_ic_transactions_date ON intercompany_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_ic_balances_entities ON intercompany_balances(entity_a_tenant_id, entity_b_tenant_id);
CREATE INDEX IF NOT EXISTS idx_ic_settlements_date ON intercompany_settlements(settlement_date);

-- ============================================================================
-- FUNCTION: Update Intercompany Balance
-- ============================================================================

CREATE OR REPLACE FUNCTION update_intercompany_balance()
RETURNS TRIGGER AS $$
BEGIN
    -- Update or insert balance record
    INSERT INTO intercompany_balances (
        tenant_id, entity_a_tenant_id, entity_b_tenant_id, balance, last_transaction_date
    )
    VALUES (
        NEW.tenant_id, NEW.from_entity_tenant_id, NEW.to_entity_tenant_id,
        NEW.amount, NEW.transaction_date
    )
    ON CONFLICT (tenant_id, entity_a_tenant_id, entity_b_tenant_id)
    DO UPDATE SET
        balance = intercompany_balances.balance + NEW.amount,
        last_transaction_date = NEW.transaction_date,
        updated_at = NOW();

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_ic_balance ON intercompany_transactions;
CREATE TRIGGER trg_update_ic_balance
AFTER INSERT ON intercompany_transactions
FOR EACH ROW
WHEN (NEW.is_reconciled = false AND NEW.from_status = 'confirmed')
EXECUTE FUNCTION update_intercompany_balance();

-- ============================================================================
-- FUNCTION: Generate IC Transaction Number
-- ============================================================================

CREATE OR REPLACE FUNCTION generate_ic_transaction_number(p_tenant_id TEXT)
RETURNS TEXT AS $$
DECLARE
    v_prefix VARCHAR(10);
    v_number INTEGER;
    v_year INTEGER;
BEGIN
    v_year := EXTRACT(YEAR FROM CURRENT_DATE);

    INSERT INTO intercompany_sequences (tenant_id, last_number, prefix, last_reset_year)
    VALUES (p_tenant_id, 1, 'IC', v_year)
    ON CONFLICT (tenant_id) DO UPDATE SET
        last_number = CASE
            WHEN intercompany_sequences.last_reset_year != v_year THEN 1
            ELSE intercompany_sequences.last_number + 1
        END,
        last_reset_year = v_year
    RETURNING prefix, last_number INTO v_prefix, v_number;

    RETURN v_prefix || '-' || v_year || '-' || LPAD(v_number::TEXT, 6, '0');
END;
$$ LANGUAGE plpgsql;
