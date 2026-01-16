-- =============================================
-- V050: Fixed Assets (Aset Tetap)
-- Purpose: Manage fixed assets with automatic depreciation
-- =============================================

-- Asset categories
CREATE TABLE asset_categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    name VARCHAR(100) NOT NULL,
    code VARCHAR(50),

    -- Default depreciation settings
    depreciation_method VARCHAR(20) DEFAULT 'straight_line', -- straight_line, declining_balance, units_of_production
    useful_life_months INTEGER,
    salvage_value_percent DECIMAL(5,2) DEFAULT 0,

    -- Default accounts
    asset_account_id UUID REFERENCES chart_of_accounts(id),
    depreciation_account_id UUID REFERENCES chart_of_accounts(id),
    accumulated_depreciation_account_id UUID REFERENCES chart_of_accounts(id),

    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_asset_categories_code UNIQUE(tenant_id, code)
);

-- Fixed assets master
CREATE TABLE fixed_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,

    -- Basic info
    asset_number VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,

    -- Category
    category_id UUID REFERENCES asset_categories(id),

    -- Purchase info
    purchase_date DATE NOT NULL,
    purchase_price BIGINT NOT NULL,
    vendor_id UUID REFERENCES vendors(id),
    bill_id UUID REFERENCES bills(id),

    -- Location
    warehouse_id UUID REFERENCES warehouses(id),
    location_detail VARCHAR(255), -- room, floor, etc

    -- Depreciation settings
    depreciation_method VARCHAR(20) NOT NULL DEFAULT 'straight_line',
    useful_life_months INTEGER NOT NULL,
    salvage_value BIGINT DEFAULT 0,
    depreciation_start_date DATE NOT NULL,

    -- Current values
    current_value BIGINT NOT NULL, -- purchase_price - accumulated_depreciation
    accumulated_depreciation BIGINT DEFAULT 0,

    -- Accounts (override category if set)
    asset_account_id UUID REFERENCES chart_of_accounts(id),
    depreciation_account_id UUID REFERENCES chart_of_accounts(id),
    accumulated_depreciation_account_id UUID REFERENCES chart_of_accounts(id),

    -- Status
    status VARCHAR(20) DEFAULT 'draft', -- draft, active, fully_depreciated, disposed, sold

    -- Disposal info
    disposal_date DATE,
    disposal_method VARCHAR(20), -- sold, scrapped, donated, lost
    disposal_price BIGINT,
    disposal_journal_id UUID REFERENCES journal_entries(id),
    gain_loss_amount BIGINT,

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,

    CONSTRAINT uq_fixed_assets_number UNIQUE(tenant_id, asset_number),
    CONSTRAINT chk_fixed_assets_salvage CHECK (salvage_value >= 0 AND salvage_value <= purchase_price)
);

-- Depreciation schedule/history
CREATE TABLE asset_depreciations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES fixed_assets(id),

    -- Period
    depreciation_date DATE NOT NULL,
    period_year INTEGER NOT NULL,
    period_month INTEGER NOT NULL,

    -- Amounts
    depreciation_amount BIGINT NOT NULL,
    accumulated_amount BIGINT NOT NULL, -- running total after this depreciation
    book_value BIGINT NOT NULL, -- value after this depreciation

    -- Journal
    journal_id UUID REFERENCES journal_entries(id),

    -- Status
    status VARCHAR(20) DEFAULT 'scheduled', -- scheduled, posted, reversed

    created_at TIMESTAMPTZ DEFAULT NOW(),
    posted_at TIMESTAMPTZ,

    CONSTRAINT uq_asset_depreciation_period UNIQUE(asset_id, period_year, period_month)
);

-- Asset maintenance/service history
CREATE TABLE asset_maintenance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES fixed_assets(id),

    maintenance_date DATE NOT NULL,
    description TEXT NOT NULL,
    cost BIGINT,
    vendor_id UUID REFERENCES vendors(id),
    bill_id UUID REFERENCES bills(id),

    -- Type
    maintenance_type VARCHAR(50), -- repair, service, upgrade, inspection

    next_maintenance_date DATE,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sequence
CREATE TABLE fixed_asset_sequences (
    tenant_id TEXT PRIMARY KEY,
    last_number INTEGER DEFAULT 0
);

-- RLS
ALTER TABLE asset_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE fixed_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_depreciations ENABLE ROW LEVEL SECURITY;
ALTER TABLE asset_maintenance ENABLE ROW LEVEL SECURITY;

CREATE POLICY rls_asset_categories ON asset_categories
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_fixed_assets ON fixed_assets
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY rls_asset_depreciations ON asset_depreciations
    USING (asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = current_setting('app.tenant_id', true)));
CREATE POLICY rls_asset_maintenance ON asset_maintenance
    USING (asset_id IN (SELECT id FROM fixed_assets WHERE tenant_id = current_setting('app.tenant_id', true)));

-- Indexes
CREATE INDEX idx_asset_categories_tenant ON asset_categories(tenant_id);
CREATE INDEX idx_fixed_assets_tenant ON fixed_assets(tenant_id);
CREATE INDEX idx_fixed_assets_status ON fixed_assets(tenant_id, status);
CREATE INDEX idx_fixed_assets_category ON fixed_assets(category_id);
CREATE INDEX idx_fixed_assets_warehouse ON fixed_assets(warehouse_id);
CREATE INDEX idx_asset_depreciations_asset ON asset_depreciations(asset_id);
CREATE INDEX idx_asset_depreciations_date ON asset_depreciations(depreciation_date) WHERE status = 'scheduled';
CREATE INDEX idx_asset_depreciations_period ON asset_depreciations(period_year, period_month);
CREATE INDEX idx_asset_maintenance_asset ON asset_maintenance(asset_id);
CREATE INDEX idx_asset_maintenance_next ON asset_maintenance(next_maintenance_date) WHERE next_maintenance_date IS NOT NULL;

-- =============================================
-- Seed Fixed Asset Accounts
-- =============================================

CREATE OR REPLACE FUNCTION seed_fixed_asset_accounts(p_tenant_id TEXT)
RETURNS VOID AS $$
BEGIN
    -- Asset account
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, is_system, is_active)
    VALUES (p_tenant_id, '1-20100', 'Aset Tetap', 'ASSET', true, true)
    ON CONFLICT (tenant_id, account_code) DO NOTHING;

    -- Accumulated depreciation (contra asset)
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, is_system, is_active)
    VALUES (p_tenant_id, '1-20200', 'Akumulasi Penyusutan', 'ASSET', true, true)
    ON CONFLICT (tenant_id, account_code) DO NOTHING;

    -- Depreciation expense
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, is_system, is_active)
    VALUES (p_tenant_id, '5-30100', 'Beban Penyusutan', 'EXPENSE', true, true)
    ON CONFLICT (tenant_id, account_code) DO NOTHING;

    -- Gain on asset sale
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, is_system, is_active)
    VALUES (p_tenant_id, '8-10200', 'Laba Penjualan Aset', 'OTHER_INCOME', true, true)
    ON CONFLICT (tenant_id, account_code) DO NOTHING;

    -- Loss on asset sale
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, is_system, is_active)
    VALUES (p_tenant_id, '8-20200', 'Rugi Penjualan Aset', 'OTHER_EXPENSE', true, true)
    ON CONFLICT (tenant_id, account_code) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

-- =============================================
-- Helper Functions
-- =============================================

-- Generate asset number
CREATE OR REPLACE FUNCTION generate_asset_number(p_tenant_id TEXT)
RETURNS VARCHAR(50) AS $$
DECLARE
    v_number INTEGER;
    v_year TEXT;
BEGIN
    v_year := TO_CHAR(CURRENT_DATE, 'YYYY');

    INSERT INTO fixed_asset_sequences (tenant_id, last_number)
    VALUES (p_tenant_id, 1)
    ON CONFLICT (tenant_id)
    DO UPDATE SET last_number = fixed_asset_sequences.last_number + 1
    RETURNING last_number INTO v_number;

    RETURN 'FA-' || v_year || '-' || LPAD(v_number::TEXT, 5, '0');
END;
$$ LANGUAGE plpgsql;

-- Calculate straight line depreciation
CREATE OR REPLACE FUNCTION calculate_straight_line_depreciation(
    p_purchase_price BIGINT,
    p_salvage_value BIGINT,
    p_useful_life_months INTEGER
)
RETURNS BIGINT AS $$
BEGIN
    IF p_useful_life_months <= 0 THEN
        RETURN 0;
    END IF;
    RETURN (p_purchase_price - p_salvage_value) / p_useful_life_months;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Calculate declining balance depreciation
CREATE OR REPLACE FUNCTION calculate_declining_balance_depreciation(
    p_current_value BIGINT,
    p_salvage_value BIGINT,
    p_useful_life_months INTEGER,
    p_rate_multiplier DECIMAL DEFAULT 2
)
RETURNS BIGINT AS $$
DECLARE
    v_useful_life_years DECIMAL;
    v_annual_rate DECIMAL;
    v_monthly_depreciation BIGINT;
BEGIN
    IF p_useful_life_months <= 0 OR p_current_value <= p_salvage_value THEN
        RETURN 0;
    END IF;

    v_useful_life_years := p_useful_life_months / 12.0;
    v_annual_rate := (1.0 / v_useful_life_years) * p_rate_multiplier;
    v_monthly_depreciation := (p_current_value * v_annual_rate / 12)::BIGINT;

    -- Don't depreciate below salvage value
    IF (p_current_value - v_monthly_depreciation) < p_salvage_value THEN
        v_monthly_depreciation := p_current_value - p_salvage_value;
    END IF;

    RETURN GREATEST(0, v_monthly_depreciation);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Generate depreciation schedule for an asset
CREATE OR REPLACE FUNCTION generate_depreciation_schedule(p_asset_id UUID)
RETURNS INTEGER AS $$
DECLARE
    v_asset RECORD;
    v_current_date DATE;
    v_depreciation_amount BIGINT;
    v_accumulated BIGINT := 0;
    v_book_value BIGINT;
    v_month_count INTEGER := 0;
BEGIN
    -- Get asset
    SELECT * INTO v_asset FROM fixed_assets WHERE id = p_asset_id;

    IF v_asset.id IS NULL OR v_asset.status != 'active' THEN
        RETURN 0;
    END IF;

    -- Delete existing scheduled depreciations
    DELETE FROM asset_depreciations
    WHERE asset_id = p_asset_id AND status = 'scheduled';

    -- Initialize
    v_accumulated := v_asset.accumulated_depreciation;
    v_book_value := v_asset.current_value;
    v_current_date := v_asset.depreciation_start_date;

    -- If asset already partially depreciated, start from next month
    IF v_accumulated > 0 THEN
        SELECT COALESCE(MAX(depreciation_date) + INTERVAL '1 month', v_current_date)::DATE
        INTO v_current_date
        FROM asset_depreciations WHERE asset_id = p_asset_id AND status = 'posted';
    END IF;

    -- Generate schedule until fully depreciated
    WHILE v_book_value > v_asset.salvage_value AND v_month_count < v_asset.useful_life_months LOOP
        -- Calculate depreciation for this month
        IF v_asset.depreciation_method = 'straight_line' THEN
            v_depreciation_amount := calculate_straight_line_depreciation(
                v_asset.purchase_price, v_asset.salvage_value, v_asset.useful_life_months
            );
        ELSIF v_asset.depreciation_method = 'declining_balance' THEN
            v_depreciation_amount := calculate_declining_balance_depreciation(
                v_book_value, v_asset.salvage_value, v_asset.useful_life_months
            );
        ELSE
            v_depreciation_amount := calculate_straight_line_depreciation(
                v_asset.purchase_price, v_asset.salvage_value, v_asset.useful_life_months
            );
        END IF;

        -- Don't depreciate below salvage value
        IF (v_book_value - v_depreciation_amount) < v_asset.salvage_value THEN
            v_depreciation_amount := v_book_value - v_asset.salvage_value;
        END IF;

        -- Skip if no depreciation
        IF v_depreciation_amount <= 0 THEN
            EXIT;
        END IF;

        v_accumulated := v_accumulated + v_depreciation_amount;
        v_book_value := v_book_value - v_depreciation_amount;

        -- Insert schedule entry
        INSERT INTO asset_depreciations (
            asset_id, depreciation_date, period_year, period_month,
            depreciation_amount, accumulated_amount, book_value, status
        ) VALUES (
            p_asset_id,
            (DATE_TRUNC('month', v_current_date) + INTERVAL '1 month - 1 day')::DATE, -- last day of month
            EXTRACT(YEAR FROM v_current_date)::INTEGER,
            EXTRACT(MONTH FROM v_current_date)::INTEGER,
            v_depreciation_amount,
            v_accumulated,
            v_book_value,
            'scheduled'
        ) ON CONFLICT (asset_id, period_year, period_month) DO NOTHING;

        v_month_count := v_month_count + 1;
        v_current_date := (v_current_date + INTERVAL '1 month')::DATE;
    END LOOP;

    RETURN v_month_count;
END;
$$ LANGUAGE plpgsql;

-- Get assets due for depreciation
CREATE OR REPLACE FUNCTION get_depreciation_due(
    p_tenant_id TEXT,
    p_year INTEGER,
    p_month INTEGER
)
RETURNS TABLE (
    asset_id UUID,
    asset_number VARCHAR(50),
    asset_name VARCHAR(255),
    depreciation_date DATE,
    depreciation_amount BIGINT,
    accumulated_amount BIGINT,
    book_value BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        fa.id as asset_id,
        fa.asset_number,
        fa.name as asset_name,
        ad.depreciation_date,
        ad.depreciation_amount,
        ad.accumulated_amount,
        ad.book_value
    FROM asset_depreciations ad
    JOIN fixed_assets fa ON ad.asset_id = fa.id
    WHERE fa.tenant_id = p_tenant_id
    AND ad.period_year = p_year
    AND ad.period_month = p_month
    AND ad.status = 'scheduled'
    ORDER BY fa.asset_number;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get asset register (all assets)
CREATE OR REPLACE FUNCTION get_asset_register(p_tenant_id TEXT, p_status VARCHAR(20) DEFAULT NULL)
RETURNS TABLE (
    id UUID,
    asset_number VARCHAR(50),
    name VARCHAR(255),
    category_name VARCHAR(100),
    purchase_date DATE,
    purchase_price BIGINT,
    depreciation_method VARCHAR(20),
    useful_life_months INTEGER,
    salvage_value BIGINT,
    current_value BIGINT,
    accumulated_depreciation BIGINT,
    status VARCHAR(20),
    location_detail VARCHAR(255)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        fa.id,
        fa.asset_number,
        fa.name,
        ac.name as category_name,
        fa.purchase_date,
        fa.purchase_price,
        fa.depreciation_method,
        fa.useful_life_months,
        fa.salvage_value,
        fa.current_value,
        fa.accumulated_depreciation,
        fa.status,
        fa.location_detail
    FROM fixed_assets fa
    LEFT JOIN asset_categories ac ON fa.category_id = ac.id
    WHERE fa.tenant_id = p_tenant_id
    AND (p_status IS NULL OR fa.status = p_status)
    ORDER BY fa.asset_number;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get assets by category summary
CREATE OR REPLACE FUNCTION get_assets_by_category(p_tenant_id TEXT)
RETURNS TABLE (
    category_id UUID,
    category_name VARCHAR(100),
    asset_count BIGINT,
    total_purchase_price BIGINT,
    total_current_value BIGINT,
    total_accumulated_depreciation BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ac.id as category_id,
        COALESCE(ac.name, 'Uncategorized')::VARCHAR(100) as category_name,
        COUNT(fa.id)::BIGINT as asset_count,
        COALESCE(SUM(fa.purchase_price), 0)::BIGINT as total_purchase_price,
        COALESCE(SUM(fa.current_value), 0)::BIGINT as total_current_value,
        COALESCE(SUM(fa.accumulated_depreciation), 0)::BIGINT as total_accumulated_depreciation
    FROM fixed_assets fa
    LEFT JOIN asset_categories ac ON fa.category_id = ac.id
    WHERE fa.tenant_id = p_tenant_id
    AND fa.status IN ('active', 'fully_depreciated')
    GROUP BY ac.id, ac.name
    ORDER BY ac.name NULLS LAST;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get assets by location summary
CREATE OR REPLACE FUNCTION get_assets_by_location(p_tenant_id TEXT)
RETURNS TABLE (
    warehouse_id UUID,
    warehouse_name VARCHAR(100),
    asset_count BIGINT,
    total_purchase_price BIGINT,
    total_current_value BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        w.id as warehouse_id,
        COALESCE(w.name, 'No Location')::VARCHAR(100) as warehouse_name,
        COUNT(fa.id)::BIGINT as asset_count,
        COALESCE(SUM(fa.purchase_price), 0)::BIGINT as total_purchase_price,
        COALESCE(SUM(fa.current_value), 0)::BIGINT as total_current_value
    FROM fixed_assets fa
    LEFT JOIN warehouses w ON fa.warehouse_id = w.id
    WHERE fa.tenant_id = p_tenant_id
    AND fa.status IN ('active', 'fully_depreciated')
    GROUP BY w.id, w.name
    ORDER BY w.name NULLS LAST;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Get upcoming maintenance
CREATE OR REPLACE FUNCTION get_maintenance_due(
    p_tenant_id TEXT,
    p_days_ahead INTEGER DEFAULT 30
)
RETURNS TABLE (
    asset_id UUID,
    asset_number VARCHAR(50),
    asset_name VARCHAR(255),
    last_maintenance_date DATE,
    next_maintenance_date DATE,
    maintenance_type VARCHAR(50),
    days_until INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT ON (fa.id)
        fa.id as asset_id,
        fa.asset_number,
        fa.name as asset_name,
        am.maintenance_date as last_maintenance_date,
        am.next_maintenance_date,
        am.maintenance_type,
        (am.next_maintenance_date - CURRENT_DATE)::INTEGER as days_until
    FROM fixed_assets fa
    JOIN asset_maintenance am ON fa.id = am.asset_id
    WHERE fa.tenant_id = p_tenant_id
    AND fa.status = 'active'
    AND am.next_maintenance_date IS NOT NULL
    AND am.next_maintenance_date <= CURRENT_DATE + (p_days_ahead || ' days')::INTERVAL
    ORDER BY fa.id, am.maintenance_date DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Update asset values after depreciation posting
CREATE OR REPLACE FUNCTION update_asset_after_depreciation()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'posted' AND (OLD.status IS NULL OR OLD.status != 'posted') THEN
        UPDATE fixed_assets SET
            accumulated_depreciation = NEW.accumulated_amount,
            current_value = NEW.book_value,
            status = CASE
                WHEN NEW.book_value <= salvage_value THEN 'fully_depreciated'
                ELSE status
            END,
            updated_at = NOW()
        WHERE id = NEW.asset_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_asset_depreciation_posted
    AFTER UPDATE ON asset_depreciations
    FOR EACH ROW
    EXECUTE FUNCTION update_asset_after_depreciation();

-- =============================================
-- Comments
-- =============================================
COMMENT ON TABLE asset_categories IS 'Categories for grouping fixed assets with default depreciation settings';
COMMENT ON TABLE fixed_assets IS 'Fixed assets master with depreciation tracking';
COMMENT ON TABLE asset_depreciations IS 'Depreciation schedule and history per asset';
COMMENT ON TABLE asset_maintenance IS 'Maintenance/service history for fixed assets';
COMMENT ON COLUMN fixed_assets.depreciation_method IS 'straight_line, declining_balance, units_of_production';
COMMENT ON COLUMN fixed_assets.current_value IS 'purchase_price - accumulated_depreciation';

/*
JOURNAL ENTRIES:

1. Asset Purchase (on activate):
   Dr. Aset Tetap (1-20100)               purchase_price
       Cr. Kas/Bank/Hutang                    purchase_price

2. Monthly Depreciation:
   Dr. Beban Penyusutan (5-30100)         depreciation_amount
       Cr. Akumulasi Penyusutan (1-20200)     depreciation_amount

3. Asset Sale:
   Dr. Kas/Bank/Piutang                   sale_price
   Dr. Akumulasi Penyusutan (1-20200)     accumulated_depreciation
       Cr. Aset Tetap (1-20100)               purchase_price
       Cr. Laba Penjualan Aset (8-10200)      gain (if sale > book value)
   -- OR --
       Dr. Rugi Penjualan Aset (8-20200)      loss (if sale < book value)

4. Asset Disposal (scrapped):
   Dr. Akumulasi Penyusutan (1-20200)     accumulated_depreciation
   Dr. Rugi Penjualan Aset (8-20200)      remaining_book_value
       Cr. Aset Tetap (1-20100)               purchase_price
*/
