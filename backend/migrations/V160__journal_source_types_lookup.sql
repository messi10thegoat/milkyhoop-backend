-- =============================================================================
-- V160 — journal_source_types lookup table + FK + typo fix
-- =============================================================================
-- Fase F5 (post-Track 2 hardening).
--
-- Context:
--   skill milkyhoop-ironlaws Law 6 promises an "Extensible journal_source_types
--   reference table". Section F audit (2026-06-05) confirmed it does NOT exist.
--   Production data has 28 distinct source_type values, including 1 typo
--   `expense` (lowercase, 7 rows) that should be `EXPENSE`.
--
-- This migration:
--   1. Creates `journal_source_types` lookup (PK source_type TEXT).
--   2. Seeds it with the 28 canonical UPPERCASE values observed in prod,
--      EXCLUDING the lowercase typo (which we normalize first).
--   3. Normalizes typo: UPDATE journal_entries SET source_type='EXPENSE' WHERE
--      source_type='expense'.
--   4. Adds FK journal_entries.source_type -> journal_source_types.source_type
--      with ON DELETE RESTRICT, ON UPDATE CASCADE.
--
-- Trade-off chosen vs CHECK constraint:
--   - Lookup table is extensible — new source_type added by INSERT, no
--     migration required.
--   - Application code continues to INSERT literal strings; FK catches typos
--     at DB level (the original P2 hygiene goal).
--   - is_active flag allows soft-deprecate without dropping FK.
--
-- Iron Law alignment:
--   Law 6 — Extensible reference table (now materialized).
--   Law 1 — No application logic changes required.
--
-- Reversal (if needed):
--   ALTER TABLE journal_entries DROP CONSTRAINT fk_je_source_type;
--   DROP TABLE journal_source_types;
-- =============================================================================

-- NOTE: Migration uses two transactions because step 3 (UPDATE) creates
-- pending FK/trigger events that block step 4 (ALTER TABLE ADD CONSTRAINT)
-- in the same transaction.

BEGIN;

-- 1. Create lookup table
CREATE TABLE IF NOT EXISTS journal_source_types (
    source_type TEXT PRIMARY KEY,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE journal_source_types IS
    'Reference table for journal_entries.source_type. '
    'Extends via INSERT — no migration required for new types. '
    'See milkyhoop-ironlaws Law 6.';

-- 2. Seed with 28 canonical values from prod (UPPERCASE).
--    `expense` (lowercase) intentionally excluded — normalized in step 3.
INSERT INTO journal_source_types (source_type, description) VALUES
    ('BANK_TRANSACTION',         'Manual bank transaction (uang masuk/keluar)'),
    ('BANK_TRANSFER',            'Bank-to-bank transfer'),
    ('BILL',                     'Faktur pembelian (vendor bill)'),
    ('BILL_PAYMENT',             'Pembayaran faktur pembelian'),
    ('CLOSING',                  'Period-close journal'),
    ('CREDIT_NOTE',              'Nota kredit penjualan'),
    ('CUSTOMER_DEPOSIT',         'Uang muka pelanggan (deposit)'),
    ('EXPENSE',                  'Beban / expense entry'),
    ('INVOICE',                  'Faktur penjualan billing event'),
    ('INVOICE_FULFILLMENT',      'Surat jalan / fulfillment (PSAK 72 3-event)'),
    ('INVOICE_REVENUE',          'Pengakuan pendapatan (PSAK 72 3-event)'),
    ('INVOICE_REVERSAL',         'Reversal faktur penjualan (void)'),
    ('MANUAL',                   'Manual journal entry'),
    ('MATERIAL_ISSUE',           'Pengeluaran bahan baku (manufaktur)'),
    ('OPENING',                  'Opening balance'),
    ('PAYMENT_BILL',             'Legacy: pembayaran bill (use BILL_PAYMENT)'),
    ('PAYMENT_RECEIVED',         'Legacy: penerimaan kas (use RECEIVE_PAYMENT)'),
    ('PRODUCTION_OUTPUT',        'Hasil produksi (FG receipt)'),
    ('PRODUCTION_OUTPUT_VOID',   'Reversal hasil produksi'),
    ('PRODUCTION_VARIANCE',      'Selisih varian produksi'),
    ('RECEIVE_PAYMENT',          'Penerimaan kas dari pelanggan'),
    ('RECLASSIFY_TAX_D1_DRIFT',  'Reklasifikasi pajak (Fase D1 drift fix)'),
    ('REVERSAL',                 'Generic reversal journal'),
    ('SALES_INVOICE_COGS',       'COGS journal untuk faktur penjualan'),
    ('STOCK_ADJUSTMENT',         'Stock adjustment (gain/loss)'),
    ('VENDOR_CREDIT',            'Nota kredit pembelian (vendor)'),
    ('VENDOR_CREDIT_COGS',       'COGS reversal nota kredit pembelian'),
    ('VENDOR_DEPOSIT',           'Uang muka ke vendor (deposit out)')
ON CONFLICT (source_type) DO NOTHING;

-- 3. Normalize typo: lowercase `expense` -> `EXPENSE`
--    Snapshot count first for audit log.
DO $$
DECLARE
    typo_count INT;
BEGIN
    SELECT COUNT(*) INTO typo_count
    FROM journal_entries
    WHERE source_type = 'expense';

    RAISE NOTICE 'V160 normalizing % rows: source_type=expense -> EXPENSE', typo_count;

    UPDATE journal_entries
    SET source_type = 'EXPENSE'
    WHERE source_type = 'expense';
END $$;

COMMIT;

BEGIN;

-- 4. Add FK constraint.
--    NOT VALID first to avoid full-table lock, then VALIDATE.
ALTER TABLE journal_entries
    ADD CONSTRAINT fk_je_source_type
    FOREIGN KEY (source_type)
    REFERENCES journal_source_types(source_type)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
    NOT VALID;

ALTER TABLE journal_entries
    VALIDATE CONSTRAINT fk_je_source_type;

COMMIT;
