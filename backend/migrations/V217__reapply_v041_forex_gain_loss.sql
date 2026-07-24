-- ============================================================================
-- V217 — re-apply forex_gain_loss (V041 partial-abort, same class as V216/V057)
-- ----------------------------------------------------------------------------
-- V041__multi_currency.sql declares 3 tables: currencies, exchange_rates,
-- forex_gain_loss. currencies + exchange_rates exist in milkydb; forex_gain_loss
-- does NOT, and no later migration DROPs it → V041 aborted after exchange_rates
-- while the runner still recorded it "OK" (the systemic half-abort class this
-- session uncovered: the migration runner continues past a failed statement
-- inside a file and does not fail the file).
--
-- DDL below is COPIED VERBATIM from V041 (arbiter = the migration, source of
-- truth — NOT a grep reconstruction), incl. its FK to currencies(id), made
-- idempotent. Found by the authoritative detector for this class: diff of every
-- CREATE TABLE in migrations/ vs pg_class (relkind r,p). After this the diff is
-- empty except superseded journal_entries_* partitions (journal_entries is a
-- plain relkind='r' table, never partitioned in the live schema).
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

DO $$
BEGIN
    IF to_regclass('public.forex_gain_loss') IS NULL THEN
        RAISE EXCEPTION 'V217: forex_gain_loss belum terbentuk';
    END IF;
    RAISE NOTICE 'V217 OK: forex_gain_loss (V041 tail)';
END $$;
