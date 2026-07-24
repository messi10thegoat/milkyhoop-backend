CREATE TABLE IF NOT EXISTS accounting_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL UNIQUE,

    -- Report basis preference
    default_report_basis VARCHAR(10) DEFAULT 'accrual', -- 'cash' or 'accrual'

    -- Fiscal year settings
    fiscal_year_start_month INTEGER DEFAULT 1, -- 1-12 (January default)

    -- Currency settings
    base_currency_code CHAR(3) DEFAULT 'IDR',

    -- Number formatting
    decimal_places INTEGER DEFAULT 0,
    thousand_separator VARCHAR(1) DEFAULT '.',
    decimal_separator VARCHAR(1) DEFAULT ',',

    -- Date format preference
    date_format VARCHAR(20) DEFAULT 'DD/MM/YYYY',

    -- Audit
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT chk_report_basis CHECK (default_report_basis IN ('cash', 'accrual')),
    CONSTRAINT chk_fiscal_month CHECK (fiscal_year_start_month BETWEEN 1 AND 12)
);
ALTER TABLE accounting_settings ENABLE ROW LEVEL SECURITY;