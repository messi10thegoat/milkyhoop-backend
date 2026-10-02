-- V359 (2 Okt 2026, pemilik + MASTER, pola SAP number range / NetSuite auto-generated numbers) -- KODE ORDER produksi.
-- Nomor SO tetap (boleh bolong); kode order terpisah, format per tenant (templat), terbit di uang masuk pertama SO
-- (atau pemicu lain per pengaturan), tak pernah diterbitkan ulang / dikosongkan. ADITIF: kolom NULL, tabel baru,
-- pengaturan default MATI -> nol perubahan perilaku sampai tenant dinyalakan.

ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS order_code varchar(40);
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS order_title varchar(60);
ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS order_code_source varchar(10);
ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS chk_so_order_code_source;
ALTER TABLE sales_orders ADD CONSTRAINT chk_so_order_code_source
    CHECK (order_code_source IS NULL OR order_code_source IN ('auto', 'manual', 'import'));
CREATE UNIQUE INDEX IF NOT EXISTS uq_so_order_code ON sales_orders (tenant_id, order_code) WHERE order_code IS NOT NULL;

CREATE TABLE IF NOT EXISTS order_code_settings (
    tenant_id      text         PRIMARY KEY REFERENCES "Tenant"(id),
    enabled        boolean      NOT NULL DEFAULT false,
    template       varchar(40)  NOT NULL DEFAULT '{SEQ}',
    min_digits     smallint     NOT NULL DEFAULT 3 CHECK (min_digits BETWEEN 1 AND 9),
    reset          varchar(8)   NOT NULL DEFAULT 'never' CHECK (reset IN ('never', 'yearly', 'monthly')),
    trigger        varchar(16)  NOT NULL DEFAULT 'first_payment'
                   CHECK (trigger IN ('first_payment', 'so_confirmed', 'manual_only')),
    allow_override boolean      NOT NULL DEFAULT false,
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    updated_by     varchar(255),
    -- tepat satu {SEQ}; reset bulanan wajib {MM}, tahunan wajib {YY}/{YYYY} (kalau tidak, kode bentrok lintas periode)
    CONSTRAINT chk_ocs_seq CHECK ((length(template) - length(replace(template, '{SEQ}', ''))) = 5),
    CONSTRAINT chk_ocs_reset_token CHECK (
        (reset <> 'monthly' OR position('{MM}' in template) > 0)
        AND (reset <> 'yearly' OR position('{YY}' in template) > 0 OR position('{YYYY}' in template) > 0))
);

CREATE TABLE IF NOT EXISTS order_code_counters (
    tenant_id  text         NOT NULL REFERENCES "Tenant"(id),
    period_key varchar(7)   NOT NULL,   -- 'ALL' | 'YYYY' | 'YYYY-MM'
    last_seq   integer      NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
    updated_at timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, period_key)
);

CREATE TABLE IF NOT EXISTS order_code_events (
    id             uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      text         NOT NULL,
    sales_order_id uuid         NOT NULL,
    old_code       varchar(40),
    new_code       varchar(40)  NOT NULL,
    source         varchar(10)  NOT NULL CHECK (source IN ('auto', 'manual', 'import')),
    actor          varchar(255),
    created_at     timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_oce_so ON order_code_events (tenant_id, sales_order_id);

-- Pencarian daftar SO ikut kode + judul order
CREATE OR REPLACE FUNCTION fn_search_text_sales_orders() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_text := lower(unaccent(concat_ws(' ', NEW.order_number, NEW.customer_name, NEW.reference,
                                               NEW.shipping_address, NEW.notes, NEW.order_code, NEW.order_title)));
  RETURN NEW;
END $$;

ALTER TABLE order_code_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_code_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_code_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS order_code_settings_tenant ON order_code_settings;
CREATE POLICY order_code_settings_tenant ON order_code_settings USING (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS order_code_counters_tenant ON order_code_counters;
CREATE POLICY order_code_counters_tenant ON order_code_counters USING (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS order_code_events_tenant ON order_code_events;
CREATE POLICY order_code_events_tenant ON order_code_events USING (tenant_id = current_setting('app.tenant_id', true));
