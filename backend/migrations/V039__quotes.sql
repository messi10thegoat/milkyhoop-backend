-- ============================================================================
-- V039: Quotes Module (Penawaran)
-- ============================================================================
-- Purpose: Quote management before conversion to Sales Order or Invoice
-- NO journal entries - accounting impact happens on conversion to Invoice
-- ============================================================================

-- ============================================================================
-- 1. QUOTES TABLE - Quote header
-- ============================================================================

CREATE TABLE IF NOT EXISTS quotes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Header
    quote_number VARCHAR(50) NOT NULL,
    quote_date DATE NOT NULL,
    expiry_date DATE,

    -- Customer
    customer_id UUID NOT NULL,
    customer_name VARCHAR(255) NOT NULL,
    customer_email VARCHAR(255),

    -- Reference
    reference VARCHAR(100),
    subject VARCHAR(255),

    -- Amounts (stored as BIGINT - smallest currency unit)
    subtotal BIGINT NOT NULL DEFAULT 0,
    discount_type VARCHAR(20) DEFAULT 'fixed', -- fixed, percentage
    discount_value DECIMAL(15,2) DEFAULT 0,
    discount_amount BIGINT DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    total_amount BIGINT NOT NULL DEFAULT 0,

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, sent, viewed, accepted, declined, expired, converted

    -- Conversion tracking
    converted_to_type VARCHAR(20), -- sales_order, invoice
    converted_to_id UUID,
    converted_at TIMESTAMPTZ,

    -- Content
    notes TEXT,
    terms TEXT,
    footer TEXT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    sent_at TIMESTAMPTZ,
    viewed_at TIMESTAMPTZ,
    accepted_at TIMESTAMPTZ,
    declined_at TIMESTAMPTZ,
    declined_reason TEXT,

    CONSTRAINT uq_quotes_number UNIQUE(tenant_id, quote_number),
    CONSTRAINT chk_quotes_status CHECK (status IN ('draft', 'sent', 'viewed', 'accepted', 'declined', 'expired', 'converted')),
    CONSTRAINT chk_quotes_discount_type CHECK (discount_type IN ('fixed', 'percentage'))
);

COMMENT ON TABLE quotes IS 'Penawaran Harga - Quotes before conversion to Invoice/SO';
COMMENT ON COLUMN quotes.status IS 'Workflow: draft -> sent -> viewed -> accepted/declined/expired -> converted';

-- ============================================================================
-- 2. QUOTE ITEMS TABLE - Line items
-- ============================================================================

CREATE TABLE IF NOT EXISTS quote_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id UUID NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,

    -- Item reference (optional - can be free text)
    item_id UUID,
    description TEXT NOT NULL,

    -- Quantities
    quantity DECIMAL(15,4) NOT NULL DEFAULT 1,
    unit VARCHAR(50),

    -- Pricing (stored as BIGINT)
    unit_price BIGINT NOT NULL DEFAULT 0,
    discount_percent DECIMAL(5,2) DEFAULT 0,
    tax_id UUID,
    tax_rate DECIMAL(5,2) DEFAULT 0,
    tax_amount BIGINT DEFAULT 0,
    line_total BIGINT NOT NULL DEFAULT 0,

    -- Optional grouping
    group_name VARCHAR(100),

    -- Sort order
    sort_order INTEGER DEFAULT 0
);

COMMENT ON TABLE quote_items IS 'Item baris untuk penawaran';

-- ============================================================================
-- 3. QUOTE SEQUENCES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS quote_sequences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    year_month VARCHAR(7) NOT NULL,
    last_number INTEGER DEFAULT 0,
    prefix VARCHAR(10) DEFAULT 'QUO',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_quote_seq_tenant_month UNIQUE(tenant_id, year_month)
);

-- ============================================================================
-- 4. INDEXES
-- ============================================================================

-- Quotes
CREATE INDEX IF NOT EXISTS idx_quotes_tenant ON quotes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_quotes_customer ON quotes(customer_id, status);
CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_quotes_expiry ON quotes(tenant_id, expiry_date) WHERE status = 'sent';
CREATE INDEX IF NOT EXISTS idx_quotes_number ON quotes(tenant_id, quote_number);
CREATE INDEX IF NOT EXISTS idx_quotes_date ON quotes(tenant_id, quote_date DESC);

-- Quote items
CREATE INDEX IF NOT EXISTS idx_quote_items_quote ON quote_items(quote_id);
CREATE INDEX IF NOT EXISTS idx_quote_items_item ON quote_items(item_id) WHERE item_id IS NOT NULL;

-- ============================================================================
-- 5. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE quotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE quote_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE quote_sequences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_quotes ON quotes;
DROP POLICY IF EXISTS rls_quote_items ON quote_items;
DROP POLICY IF EXISTS rls_quote_sequences ON quote_sequences;

CREATE POLICY rls_quotes ON quotes
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_quote_items ON quote_items
    FOR ALL USING (quote_id IN (SELECT id FROM quotes WHERE tenant_id = current_setting('app.tenant_id', true)));

CREATE POLICY rls_quote_sequences ON quote_sequences
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 6. FUNCTIONS
-- ============================================================================

-- Generate quote number
CREATE OR REPLACE FUNCTION generate_quote_number(
    p_tenant_id TEXT,
    p_prefix VARCHAR DEFAULT 'QUO'
) RETURNS VARCHAR AS $$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_quote_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO quote_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = quote_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: QUO-YYMM-0001
    v_quote_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_quote_number;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION generate_quote_number IS 'Generates sequential quote number per tenant per month';

-- ============================================================================
-- 7. TRIGGERS
-- ============================================================================

-- Auto-expire trigger - check on update
CREATE OR REPLACE FUNCTION check_quote_expiry()
RETURNS TRIGGER AS $$
BEGIN
    -- Auto-expire sent quotes past expiry date
    IF NEW.expiry_date IS NOT NULL
       AND NEW.expiry_date < CURRENT_DATE
       AND OLD.status = 'sent'
       AND NEW.status = 'sent' THEN
        NEW.status := 'expired';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_quote_expiry ON quotes;
CREATE TRIGGER trg_quote_expiry
BEFORE UPDATE ON quotes
FOR EACH ROW
EXECUTE FUNCTION check_quote_expiry();

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_quotes_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_quotes_updated_at ON quotes;
CREATE TRIGGER trg_quotes_updated_at
BEFORE UPDATE ON quotes
FOR EACH ROW EXECUTE FUNCTION update_quotes_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V039: Quotes Module created successfully';
    RAISE NOTICE 'Tables: quotes, quote_items, quote_sequences';
    RAISE NOTICE 'RLS enabled on all tables';
    RAISE NOTICE 'NOTE: NO journal entries - quotes are pre-accounting documents';
END $$;
