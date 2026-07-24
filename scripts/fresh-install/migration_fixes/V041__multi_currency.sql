-- ============================================================================
-- V041: Multi-currency Support
-- ============================================================================
-- Purpose: Support transactions in multiple currencies with exchange rates
-- Adds forex gain/loss tracking for payment settlements
-- ============================================================================

-- ============================================================================
-- 1. CURRENCIES TABLE - Currency master data
-- ============================================================================

CREATE TABLE IF NOT EXISTS currencies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Currency info (ISO 4217)
    code VARCHAR(3) NOT NULL,
    name VARCHAR(100) NOT NULL,
    symbol VARCHAR(10),

    -- Settings
    decimal_places INTEGER DEFAULT 2,
    is_base_currency BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_currencies_tenant_code UNIQUE(tenant_id, code)
);

COMMENT ON TABLE currencies IS 'Currency master data per tenant';
COMMENT ON COLUMN currencies.is_base_currency IS 'Only one currency per tenant can be base';

-- ============================================================================
-- 2. EXCHANGE RATES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS exchange_rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Currencies (1 from_currency = rate * to_currency)
    from_currency_id UUID NOT NULL REFERENCES currencies(id),
    to_currency_id UUID NOT NULL REFERENCES currencies(id),

    -- Rate info
    rate_date DATE NOT NULL,
    rate DECIMAL(20,10) NOT NULL,

    -- Source
    source VARCHAR(50) DEFAULT 'manual', -- manual, api, bank

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_exchange_rates UNIQUE(tenant_id, from_currency_id, to_currency_id, rate_date),
    CONSTRAINT chk_different_currencies CHECK (from_currency_id != to_currency_id)
);

COMMENT ON TABLE exchange_rates IS 'Historical exchange rates per tenant';

-- ============================================================================
-- 3. FOREX GAIN/LOSS TRACKING TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS forex_gain_loss (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Source transaction
    source_type VARCHAR(50) NOT NULL, -- INVOICE_PAYMENT, BILL_PAYMENT, REVALUATION
    source_id UUID,

    -- Transaction info
    transaction_date DATE NOT NULL,
    original_currency_id UUID NOT NULL REFERENCES currencies(id),
    original_amount BIGINT NOT NULL,

    -- Rates
    original_rate DECIMAL(20,10) NOT NULL,
    settlement_rate DECIMAL(20,10) NOT NULL,

    -- Gain/Loss (positive = gain, negative = loss)
    gain_loss_amount BIGINT NOT NULL,
    is_realized BOOLEAN DEFAULT true, -- true = realized, false = unrealized

    -- Journal link
    journal_id UUID,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE forex_gain_loss IS 'Forex gain/loss tracking for settlements and revaluations';

-- ============================================================================
-- 4. ADD CURRENCY COLUMNS TO EXISTING TABLES
-- ============================================================================

-- Sales Invoices
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS currency_id UUID REFERENCES currencies(id);
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS exchange_rate DECIMAL(20,10) DEFAULT 1;
ALTER TABLE sales_invoices ADD COLUMN IF NOT EXISTS base_currency_total BIGINT;

-- Bills
ALTER TABLE bills ADD COLUMN IF NOT EXISTS currency_id UUID REFERENCES currencies(id);
ALTER TABLE bills ADD COLUMN IF NOT EXISTS exchange_rate DECIMAL(20,10) DEFAULT 1;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS base_currency_total BIGINT;

-- Credit Notes
ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS currency_id UUID REFERENCES currencies(id);
ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS exchange_rate DECIMAL(20,10) DEFAULT 1;
ALTER TABLE credit_notes ADD COLUMN IF NOT EXISTS base_currency_total BIGINT;

-- Vendor Credits
ALTER TABLE vendor_credits ADD COLUMN IF NOT EXISTS currency_id UUID REFERENCES currencies(id);
ALTER TABLE vendor_credits ADD COLUMN IF NOT EXISTS exchange_rate DECIMAL(20,10) DEFAULT 1;
ALTER TABLE vendor_credits ADD COLUMN IF NOT EXISTS base_currency_total BIGINT;

-- Quotes
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS currency_id UUID REFERENCES currencies(id);
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS exchange_rate DECIMAL(20,10) DEFAULT 1;

-- Sales Orders
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS currency_id UUID REFERENCES currencies(id);
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS exchange_rate DECIMAL(20,10) DEFAULT 1;

-- Customers - default currency
ALTER TABLE customers ADD COLUMN IF NOT EXISTS default_currency_id UUID REFERENCES currencies(id);

-- Vendors - default currency
ALTER TABLE vendors ADD COLUMN IF NOT EXISTS default_currency_id UUID REFERENCES currencies(id);

-- ============================================================================
-- 5. SEED FOREX ACCOUNTS FOR ALL TENANTS
-- ============================================================================

-- Forex Gain Account (8-10100)
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '8-10100',
    'Laba Selisih Kurs',
    'OTHER_INCOME',
    'CREDIT',
    '8-10000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '8-10100' AND tenant_id = t.tenant_id
);

-- Forex Loss Account (8-20100)
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, is_active)
SELECT
    t.tenant_id,
    '8-20100',
    'Rugi Selisih Kurs',
    'OTHER_EXPENSE',
    'DEBIT',
    '8-20000',
    true
FROM (SELECT DISTINCT tenant_id FROM chart_of_accounts) t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '8-20100' AND tenant_id = t.tenant_id
);

-- ============================================================================
-- 6. SEED IDR AS DEFAULT BASE CURRENCY FOR EXISTING TENANTS
-- ============================================================================

INSERT INTO currencies (tenant_id, code, name, symbol, decimal_places, is_base_currency, is_active)
SELECT DISTINCT
    tenant_id,
    'IDR',
    'Indonesian Rupiah',
    'Rp',
    0,
    true,
    true
FROM chart_of_accounts
WHERE NOT EXISTS (
    SELECT 1 FROM currencies c WHERE c.tenant_id = chart_of_accounts.tenant_id AND c.code = 'IDR'
)
ON CONFLICT (tenant_id, code) DO NOTHING;

-- ============================================================================
-- 7. BACKFILL CURRENCY FOR EXISTING RECORDS
-- ============================================================================

-- Update sales_invoices with IDR
UPDATE sales_invoices si
SET
    currency_id = (SELECT c.id FROM currencies c WHERE c.tenant_id = si.tenant_id AND c.code = 'IDR' LIMIT 1),
    exchange_rate = 1,
    base_currency_total = total_amount
WHERE currency_id IS NULL;

-- Update bills with IDR
UPDATE bills b
SET
    currency_id = (SELECT c.id FROM currencies c WHERE c.tenant_id = b.tenant_id AND c.code = 'IDR' LIMIT 1),
    exchange_rate = 1,
    base_currency_total = total_amount
WHERE currency_id IS NULL;

-- Update credit_notes with IDR
UPDATE credit_notes cn
SET
    currency_id = (SELECT c.id FROM currencies c WHERE c.tenant_id = cn.tenant_id AND c.code = 'IDR' LIMIT 1),
    exchange_rate = 1,
    base_currency_total = total_amount
WHERE currency_id IS NULL;

-- Update vendor_credits with IDR
UPDATE vendor_credits vc
SET
    currency_id = (SELECT c.id FROM currencies c WHERE c.tenant_id = vc.tenant_id AND c.code = 'IDR' LIMIT 1),
    exchange_rate = 1,
    base_currency_total = total_amount
WHERE currency_id IS NULL;

-- Update quotes with IDR
UPDATE quotes q
SET
    currency_id = (SELECT c.id FROM currencies c WHERE c.tenant_id = q.tenant_id AND c.code = 'IDR' LIMIT 1),
    exchange_rate = 1
WHERE currency_id IS NULL;

-- Update sales_orders with IDR
UPDATE sales_orders so
SET
    currency_id = (SELECT c.id FROM currencies c WHERE c.tenant_id = so.tenant_id AND c.code = 'IDR' LIMIT 1),
    exchange_rate = 1
WHERE currency_id IS NULL;

-- ============================================================================
-- 8. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_currencies_tenant ON currencies(tenant_id);
CREATE INDEX IF NOT EXISTS idx_currencies_base ON currencies(tenant_id, is_base_currency) WHERE is_base_currency = true;
CREATE INDEX IF NOT EXISTS idx_exchange_rates_tenant_date ON exchange_rates(tenant_id, rate_date DESC);
CREATE INDEX IF NOT EXISTS idx_exchange_rates_lookup ON exchange_rates(tenant_id, from_currency_id, to_currency_id, rate_date DESC);
CREATE INDEX IF NOT EXISTS idx_forex_gain_loss_tenant_date ON forex_gain_loss(tenant_id, transaction_date);
CREATE INDEX IF NOT EXISTS idx_forex_gain_loss_source ON forex_gain_loss(source_type, source_id);

-- ============================================================================
-- 9. ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE currencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE exchange_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE forex_gain_loss ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_currencies ON currencies;
DROP POLICY IF EXISTS rls_exchange_rates ON exchange_rates;
DROP POLICY IF EXISTS rls_forex_gain_loss ON forex_gain_loss;

CREATE POLICY rls_currencies ON currencies
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_exchange_rates ON exchange_rates
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY rls_forex_gain_loss ON forex_gain_loss
    FOR ALL USING (tenant_id = current_setting('app.tenant_id', true));

-- ============================================================================
-- 10. FUNCTIONS
-- ============================================================================

-- Get base currency for tenant
CREATE OR REPLACE FUNCTION get_base_currency(p_tenant_id TEXT)
RETURNS UUID AS $$
DECLARE
    v_currency_id UUID;
BEGIN
    SELECT id INTO v_currency_id
    FROM currencies
    WHERE tenant_id = p_tenant_id AND is_base_currency = true
    LIMIT 1;

    RETURN v_currency_id;
END;
$$ LANGUAGE plpgsql;

-- Get exchange rate with fallback to nearest date
CREATE OR REPLACE FUNCTION get_exchange_rate(
    p_tenant_id TEXT,
    p_from_currency_id UUID,
    p_to_currency_id UUID,
    p_as_of_date DATE
) RETURNS DECIMAL AS $$
DECLARE
    v_rate DECIMAL(20,10);
BEGIN
    -- If same currency, rate is 1
    IF p_from_currency_id = p_to_currency_id THEN
        RETURN 1.0;
    END IF;

    -- Try exact date first
    SELECT rate INTO v_rate
    FROM exchange_rates
    WHERE tenant_id = p_tenant_id
    AND from_currency_id = p_from_currency_id
    AND to_currency_id = p_to_currency_id
    AND rate_date = p_as_of_date;

    IF v_rate IS NOT NULL THEN
        RETURN v_rate;
    END IF;

    -- Fallback to most recent rate before date
    SELECT rate INTO v_rate
    FROM exchange_rates
    WHERE tenant_id = p_tenant_id
    AND from_currency_id = p_from_currency_id
    AND to_currency_id = p_to_currency_id
    AND rate_date <= p_as_of_date
    ORDER BY rate_date DESC
    LIMIT 1;

    IF v_rate IS NOT NULL THEN
        RETURN v_rate;
    END IF;

    -- Try inverse rate
    SELECT 1.0 / rate INTO v_rate
    FROM exchange_rates
    WHERE tenant_id = p_tenant_id
    AND from_currency_id = p_to_currency_id
    AND to_currency_id = p_from_currency_id
    AND rate_date <= p_as_of_date
    ORDER BY rate_date DESC
    LIMIT 1;

    RETURN v_rate; -- May be NULL if no rate found
END;
$$ LANGUAGE plpgsql;

-- Calculate forex gain/loss
CREATE OR REPLACE FUNCTION calculate_forex_gain_loss(
    p_original_amount BIGINT,
    p_original_rate DECIMAL,
    p_settlement_rate DECIMAL
) RETURNS BIGINT AS $$
DECLARE
    v_original_base DECIMAL;
    v_settlement_base DECIMAL;
BEGIN
    v_original_base := p_original_amount * p_original_rate;
    v_settlement_base := p_original_amount * p_settlement_rate;

    RETURN ROUND(v_settlement_base - v_original_base)::BIGINT;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 11. TRIGGERS
-- ============================================================================

-- Ensure only one base currency per tenant
CREATE OR REPLACE FUNCTION check_single_base_currency()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_base_currency = true THEN
        UPDATE currencies
        SET is_base_currency = false
        WHERE tenant_id = NEW.tenant_id
        AND id != NEW.id
        AND is_base_currency = true;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_single_base_currency ON currencies;
CREATE TRIGGER trg_single_base_currency
BEFORE INSERT OR UPDATE ON currencies
FOR EACH ROW
WHEN (NEW.is_base_currency = true)
EXECUTE FUNCTION check_single_base_currency();

-- Update updated_at
CREATE OR REPLACE FUNCTION update_currencies_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_currencies_updated_at ON currencies;
CREATE TRIGGER trg_currencies_updated_at
BEFORE UPDATE ON currencies
FOR EACH ROW EXECUTE FUNCTION update_currencies_updated_at();

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE 'Migration V041: Multi-currency Support created successfully';
    RAISE NOTICE 'Tables: currencies, exchange_rates, forex_gain_loss';
    RAISE NOTICE 'Added currency columns to: sales_invoices, bills, credit_notes, vendor_credits, quotes, sales_orders, customers, vendors';
    RAISE NOTICE 'Seeded IDR as base currency for all tenants';
    RAISE NOTICE 'Seeded forex accounts: 8-10100 (Laba Selisih Kurs), 8-20100 (Rugi Selisih Kurs)';
    RAISE NOTICE 'RLS enabled on all new tables';
END $$;
