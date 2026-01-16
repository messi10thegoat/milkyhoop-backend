-- =============================================
-- V058: Cheque Management (Manajemen Giro/Cek Mundur)
-- Purpose: Manage post-dated cheques received from customers or issued to vendors
-- HAS JOURNAL ENTRIES - See journal mappings at bottom
-- =============================================

-- ============================================================================
-- CHEQUE MASTER TABLE
-- ============================================================================
CREATE TABLE cheques (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Cheque info
    cheque_number VARCHAR(100) NOT NULL,
    cheque_date DATE NOT NULL, -- Date on cheque (when it can be cashed)
    bank_name VARCHAR(100),
    bank_branch VARCHAR(100),

    -- Type
    cheque_type VARCHAR(20) NOT NULL, -- received (from customer), issued (to vendor)

    -- Amount
    amount BIGINT NOT NULL,

    -- Party
    customer_id UUID, -- if received (references customers table)
    vendor_id UUID REFERENCES vendors(id), -- if issued
    party_name VARCHAR(255), -- name on cheque

    -- Our bank account
    bank_account_id UUID REFERENCES bank_accounts(id),

    -- Reference
    reference_type VARCHAR(50), -- sales_invoice, bill, payment_receipt, bill_payment
    reference_id UUID,
    reference_number VARCHAR(100),

    -- Status
    status VARCHAR(20) DEFAULT 'pending', -- pending, deposited, cleared, bounced, cancelled, replaced

    -- Status dates
    received_date DATE, -- when we received it
    issued_date DATE, -- when we issued it
    deposited_date DATE, -- when deposited to bank
    cleared_date DATE, -- when cleared
    bounced_date DATE, -- if bounced

    -- Journals
    receipt_journal_id UUID REFERENCES journal_entries(id), -- when received/issued
    deposit_journal_id UUID REFERENCES journal_entries(id), -- when deposited
    clear_journal_id UUID REFERENCES journal_entries(id), -- when cleared
    bounce_journal_id UUID REFERENCES journal_entries(id), -- if bounced

    -- Bounced cheque handling
    replacement_cheque_id UUID REFERENCES cheques(id),
    bounce_charges BIGINT DEFAULT 0,
    bounce_reason TEXT,

    -- Notes
    notes TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_cheques UNIQUE(tenant_id, cheque_number, cheque_type),
    CONSTRAINT chk_cheque_type CHECK (cheque_type IN ('received', 'issued')),
    CONSTRAINT chk_cheque_status CHECK (status IN ('pending', 'deposited', 'cleared', 'bounced', 'cancelled', 'replaced')),
    CONSTRAINT chk_cheque_amount CHECK (amount > 0)
);

-- ============================================================================
-- CHEQUE STATUS HISTORY
-- ============================================================================
CREATE TABLE cheque_status_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cheque_id UUID NOT NULL REFERENCES cheques(id) ON DELETE CASCADE,

    old_status VARCHAR(20),
    new_status VARCHAR(20) NOT NULL,
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    changed_by UUID,

    notes TEXT,
    journal_id UUID REFERENCES journal_entries(id)
);

-- ============================================================================
-- SEQUENCE FOR CHEQUE TRACKING NUMBER
-- ============================================================================
CREATE TABLE cheque_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INTEGER DEFAULT 0
);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================
ALTER TABLE cheques ENABLE ROW LEVEL SECURITY;
ALTER TABLE cheque_status_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_cheques ON cheques
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_cheque_status_history ON cheque_status_history
    USING (cheque_id IN (SELECT id FROM cheques WHERE tenant_id = current_setting('app.tenant_id', true)));

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX idx_cheques_tenant ON cheques(tenant_id);
CREATE INDEX idx_cheques_status ON cheques(tenant_id, status);
CREATE INDEX idx_cheques_type ON cheques(tenant_id, cheque_type);
CREATE INDEX idx_cheques_date ON cheques(tenant_id, cheque_date);
CREATE INDEX idx_cheques_customer ON cheques(customer_id) WHERE cheque_type = 'received';
CREATE INDEX idx_cheques_vendor ON cheques(vendor_id) WHERE cheque_type = 'issued';
CREATE INDEX idx_cheques_pending ON cheques(tenant_id, cheque_date) WHERE status = 'pending';
CREATE INDEX idx_cheques_reference ON cheques(reference_type, reference_id);
CREATE INDEX idx_cheque_history_cheque ON cheque_status_history(cheque_id);

-- ============================================================================
-- SEED ACCOUNTS
-- ============================================================================

-- Function to seed cheque accounts for tenant
CREATE OR REPLACE FUNCTION seed_cheque_accounts(p_tenant_id TEXT)
RETURNS VOID AS $$
BEGIN
    -- 1-10600 Giro Diterima (Cheques Receivable - Asset)
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, is_system, is_active, description
    ) VALUES (
        p_tenant_id, '1-10600', 'Giro Diterima', 'ASSET', true, true,
        'Post-dated cheques received from customers'
    ) ON CONFLICT (tenant_id, account_code) DO NOTHING;

    -- 2-10500 Giro Diberikan (Cheques Payable - Liability)
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, is_system, is_active, description
    ) VALUES (
        p_tenant_id, '2-10500', 'Giro Diberikan', 'LIABILITY', true, true,
        'Post-dated cheques issued to vendors'
    ) ON CONFLICT (tenant_id, account_code) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Get cheques by status
CREATE OR REPLACE FUNCTION get_cheques_by_status(
    p_tenant_id TEXT,
    p_status VARCHAR(20) DEFAULT NULL,
    p_cheque_type VARCHAR(20) DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    cheque_number VARCHAR(100),
    cheque_date DATE,
    bank_name VARCHAR(100),
    cheque_type VARCHAR(20),
    amount BIGINT,
    party_name VARCHAR(255),
    status VARCHAR(20),
    reference_number VARCHAR(100)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.cheque_number,
        c.cheque_date,
        c.bank_name,
        c.cheque_type,
        c.amount,
        c.party_name,
        c.status,
        c.reference_number
    FROM cheques c
    WHERE c.tenant_id = p_tenant_id
    AND (p_status IS NULL OR c.status = p_status)
    AND (p_cheque_type IS NULL OR c.cheque_type = p_cheque_type)
    ORDER BY c.cheque_date ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get cheques due for deposit
CREATE OR REPLACE FUNCTION get_cheques_due_for_deposit(
    p_tenant_id TEXT,
    p_as_of_date DATE DEFAULT CURRENT_DATE
)
RETURNS TABLE (
    id UUID,
    cheque_number VARCHAR(100),
    cheque_date DATE,
    bank_name VARCHAR(100),
    amount BIGINT,
    party_name VARCHAR(255),
    customer_id UUID,
    days_until_due INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.cheque_number,
        c.cheque_date,
        c.bank_name,
        c.amount,
        c.party_name,
        c.customer_id,
        (c.cheque_date - p_as_of_date)::INTEGER as days_until_due
    FROM cheques c
    WHERE c.tenant_id = p_tenant_id
    AND c.cheque_type = 'received'
    AND c.status = 'pending'
    AND c.cheque_date <= p_as_of_date
    ORDER BY c.cheque_date ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get upcoming cheques (next N days)
CREATE OR REPLACE FUNCTION get_upcoming_cheques(
    p_tenant_id TEXT,
    p_days INTEGER DEFAULT 30,
    p_cheque_type VARCHAR(20) DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    cheque_number VARCHAR(100),
    cheque_date DATE,
    cheque_type VARCHAR(20),
    amount BIGINT,
    party_name VARCHAR(255),
    days_until_due INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.cheque_number,
        c.cheque_date,
        c.cheque_type,
        c.amount,
        c.party_name,
        (c.cheque_date - CURRENT_DATE)::INTEGER as days_until_due
    FROM cheques c
    WHERE c.tenant_id = p_tenant_id
    AND c.status = 'pending'
    AND (p_cheque_type IS NULL OR c.cheque_type = p_cheque_type)
    AND c.cheque_date BETWEEN CURRENT_DATE AND (CURRENT_DATE + p_days)
    ORDER BY c.cheque_date ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get cheques by customer
CREATE OR REPLACE FUNCTION get_customer_cheques(
    p_customer_id UUID,
    p_status VARCHAR(20) DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    cheque_number VARCHAR(100),
    cheque_date DATE,
    bank_name VARCHAR(100),
    amount BIGINT,
    status VARCHAR(20),
    reference_number VARCHAR(100),
    deposited_date DATE,
    cleared_date DATE
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.cheque_number,
        c.cheque_date,
        c.bank_name,
        c.amount,
        c.status,
        c.reference_number,
        c.deposited_date,
        c.cleared_date
    FROM cheques c
    WHERE c.customer_id = p_customer_id
    AND c.cheque_type = 'received'
    AND (p_status IS NULL OR c.status = p_status)
    ORDER BY c.cheque_date DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get cheques by vendor
CREATE OR REPLACE FUNCTION get_vendor_cheques(
    p_vendor_id UUID,
    p_status VARCHAR(20) DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    cheque_number VARCHAR(100),
    cheque_date DATE,
    bank_name VARCHAR(100),
    amount BIGINT,
    status VARCHAR(20),
    reference_number VARCHAR(100),
    issued_date DATE,
    cleared_date DATE
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.id,
        c.cheque_number,
        c.cheque_date,
        c.bank_name,
        c.amount,
        c.status,
        c.reference_number,
        c.issued_date,
        c.cleared_date
    FROM cheques c
    WHERE c.vendor_id = p_vendor_id
    AND c.cheque_type = 'issued'
    AND (p_status IS NULL OR c.status = p_status)
    ORDER BY c.cheque_date DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get cheque summary
CREATE OR REPLACE FUNCTION get_cheque_summary(p_tenant_id TEXT)
RETURNS TABLE (
    cheque_type VARCHAR(20),
    status VARCHAR(20),
    count BIGINT,
    total_amount BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        c.cheque_type,
        c.status,
        COUNT(*)::BIGINT,
        COALESCE(SUM(c.amount), 0)::BIGINT
    FROM cheques c
    WHERE c.tenant_id = p_tenant_id
    GROUP BY c.cheque_type, c.status
    ORDER BY c.cheque_type, c.status;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get cheque aging
CREATE OR REPLACE FUNCTION get_cheque_aging(
    p_tenant_id TEXT,
    p_cheque_type VARCHAR(20) DEFAULT 'received'
)
RETURNS TABLE (
    aging_bucket VARCHAR(20),
    count BIGINT,
    total_amount BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        CASE
            WHEN cheque_date > CURRENT_DATE THEN 'future'
            WHEN (CURRENT_DATE - cheque_date) BETWEEN 0 AND 30 THEN '0-30 days'
            WHEN (CURRENT_DATE - cheque_date) BETWEEN 31 AND 60 THEN '31-60 days'
            WHEN (CURRENT_DATE - cheque_date) BETWEEN 61 AND 90 THEN '61-90 days'
            ELSE '90+ days'
        END as aging_bucket,
        COUNT(*)::BIGINT,
        COALESCE(SUM(amount), 0)::BIGINT
    FROM cheques
    WHERE tenant_id = p_tenant_id
    AND cheque_type = p_cheque_type
    AND status = 'pending'
    GROUP BY aging_bucket
    ORDER BY aging_bucket;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Record status change
CREATE OR REPLACE FUNCTION record_cheque_status_change()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        INSERT INTO cheque_status_history (
            cheque_id, old_status, new_status, changed_by, journal_id
        ) VALUES (
            NEW.id, OLD.status, NEW.status, NEW.created_by,
            CASE NEW.status
                WHEN 'pending' THEN NEW.receipt_journal_id
                WHEN 'deposited' THEN NEW.deposit_journal_id
                WHEN 'cleared' THEN NEW.clear_journal_id
                WHEN 'bounced' THEN NEW.bounce_journal_id
                ELSE NULL
            END
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cheque_status_change
AFTER UPDATE ON cheques
FOR EACH ROW
EXECUTE FUNCTION record_cheque_status_change();

-- ============================================================================
-- COMMENTS
-- ============================================================================
COMMENT ON TABLE cheques IS 'Post-dated cheques received from customers or issued to vendors';
COMMENT ON TABLE cheque_status_history IS 'Status change history for cheques';
COMMENT ON COLUMN cheques.cheque_type IS 'received = from customer, issued = to vendor';
COMMENT ON COLUMN cheques.cheque_date IS 'Date printed on cheque (when it can be cashed)';

/*
============================================================================
JOURNAL ENTRY MAPPINGS
============================================================================

1. RECEIVE CHEQUE (from Customer as payment):
   Dr. Giro Diterima (1-10600)          amount
       Cr. Piutang Usaha (1-10300)          amount

   Note: This reduces AR and records the cheque as a receivable asset

2. DEPOSIT CHEQUE (to bank):
   Dr. Bank (1-10200)                   amount
       Cr. Giro Diterima (1-10600)          amount

   Note: Move from cheque receivable to bank (pending clearing)
   Some systems use a Bank Clearing account instead

3. CLEAR CHEQUE:
   No additional journal if deposit already moved to bank.
   If using clearing account pattern:
   Dr. Bank - Cleared (1-10200)         amount
       Cr. Bank - In Clearing (1-10250)     amount

4. BOUNCE CHEQUE:
   a) Reverse the deposit:
   Dr. Giro Diterima (1-10600)          amount
       Cr. Bank (1-10200)                   amount

   b) Reinstate AR:
   Dr. Piutang Usaha (1-10300)          amount
       Cr. Giro Diterima (1-10600)          amount

   c) Record bounce charges (if applicable):
   Dr. Piutang Usaha (1-10300)          bounce_charges
       Cr. Pendapatan Lain-lain (4-20100)   bounce_charges

5. ISSUE CHEQUE (to Vendor as payment):
   Dr. Hutang Usaha (2-10100)           amount
       Cr. Giro Diberikan (2-10500)         amount

   Note: This reduces AP and records the cheque as a liability

6. ISSUED CHEQUE CLEARED:
   Dr. Giro Diberikan (2-10500)         amount
       Cr. Bank (1-10200)                   amount

   Note: Bank account decreases when our issued cheque is cashed

============================================================================
*/
