-- V225__proformas.sql
-- T200 Tahap 3 — Proforma (dokumen penagih uang muka, merujuk Sales Order).
-- MUTLAK NON-POSTING: tabel ini tidak pernah menjurnal. Uang tetap masuk lewat
-- customer_deposits. Kolom terbayar SENGAJA TIDAK ADA — terbayar adalah TURUNAN
-- yang dihitung dari customer_deposits.proforma_id.

CREATE TABLE IF NOT EXISTS proformas (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              text NOT NULL,
    proforma_number        character varying(50) NOT NULL,
    proforma_date          date NOT NULL,
    due_date               date,
    sales_order_id         uuid NOT NULL REFERENCES sales_orders(id),
    customer_id            uuid NOT NULL,
    customer_name          character varying(255),
    purpose                character varying(20) NOT NULL,
    percent_of_order       numeric(5,2),
    amount                 numeric(18,2) NOT NULL,
    currency               character varying(10) DEFAULT 'IDR',
    terms                  text,
    notes                  text,
    payment_bank_name      text,
    payment_account_number text,
    payment_account_holder text,
    status                 character varying(20) NOT NULL DEFAULT 'draft',
    issued_at              timestamp with time zone,
    cancelled_at           timestamp with time zone,
    cancelled_reason       text,
    created_at             timestamp with time zone DEFAULT now(),
    updated_at             timestamp with time zone DEFAULT now(),
    created_by             uuid,
    CONSTRAINT uq_proformas_number UNIQUE (tenant_id, proforma_number),
    CONSTRAINT chk_proformas_status CHECK (
        status::text = ANY (ARRAY['draft'::text,'issued'::text,'cancelled'::text,'expired'::text])
    ),
    CONSTRAINT chk_proformas_purpose CHECK (
        purpose::text = ANY (ARRAY['DP'::text,'TERMIN'::text,'PELUNASAN'::text])
    ),
    CONSTRAINT chk_proformas_amount_positive CHECK (amount > 0)
);

CREATE INDEX IF NOT EXISTS idx_proformas_tenant       ON proformas(tenant_id);
CREATE INDEX IF NOT EXISTS idx_proformas_status       ON proformas(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_proformas_so           ON proformas(sales_order_id);
CREATE INDEX IF NOT EXISTS idx_proformas_customer     ON proformas(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_proformas_date         ON proformas(tenant_id, proforma_date DESC);
CREATE INDEX IF NOT EXISTS idx_proformas_number       ON proformas(tenant_id, proforma_number);

-- Atribusi pembayaran: LEWAT proforma_id, BUKAN tanggal (tanggal pecah pada cicilan).
ALTER TABLE customer_deposits ADD COLUMN IF NOT EXISTS proforma_id uuid NULL REFERENCES proformas(id);
CREATE INDEX IF NOT EXISTS idx_customer_deposits_proforma_id
    ON customer_deposits(proforma_id) WHERE proforma_id IS NOT NULL;

-- Sequence table (pola quote_sequences)
CREATE TABLE IF NOT EXISTS proforma_sequences (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   text NOT NULL,
    year_month  character varying(7) NOT NULL,
    last_number integer DEFAULT 0,
    prefix      character varying(10) DEFAULT 'PRO',
    created_at  timestamp with time zone DEFAULT now(),
    updated_at  timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_proforma_seq_tenant_month UNIQUE (tenant_id, year_month)
);

CREATE OR REPLACE FUNCTION public.generate_proforma_number(
    p_tenant_id text,
    p_prefix character varying DEFAULT 'PRO'::character varying
)
RETURNS character varying
LANGUAGE plpgsql
AS $function$
DECLARE
    v_year_month VARCHAR(7);
    v_next_number INT;
    v_proforma_number VARCHAR(50);
BEGIN
    v_year_month := TO_CHAR(CURRENT_DATE, 'YYYY-MM');

    INSERT INTO proforma_sequences (tenant_id, year_month, last_number, prefix)
    VALUES (p_tenant_id, v_year_month, 1, p_prefix)
    ON CONFLICT (tenant_id, year_month)
    DO UPDATE SET last_number = proforma_sequences.last_number + 1, updated_at = NOW()
    RETURNING last_number INTO v_next_number;

    -- Format: PRO-YYMM-0001
    v_proforma_number := p_prefix || '-' || TO_CHAR(CURRENT_DATE, 'YYMM') || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_proforma_number;
END;
$function$;

-- updated_at trigger (pola quotes)
CREATE OR REPLACE FUNCTION public.update_proformas_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_proformas_updated_at ON proformas;
CREATE TRIGGER trg_proformas_updated_at BEFORE UPDATE ON proformas
    FOR EACH ROW EXECUTE FUNCTION update_proformas_updated_at();

-- RLS (pola rls_quotes / rls_quote_sequences). Catatan: gateway konek BYPASSRLS,
-- jadi penjaga SEBENARNYA adalah WHERE tenant_id = $1 di setiap query router.
ALTER TABLE proformas ENABLE ROW LEVEL SECURITY;
ALTER TABLE proformas FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_proformas ON proformas;
CREATE POLICY rls_proformas ON proformas
    USING (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE proforma_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE proforma_sequences FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_proforma_sequences ON proforma_sequences;
CREATE POLICY rls_proforma_sequences ON proforma_sequences
    USING (tenant_id = current_setting('app.tenant_id', true));
