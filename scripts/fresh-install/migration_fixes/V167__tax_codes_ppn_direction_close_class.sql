-- V167: Close class Surprise #11 — PPN direction asymmetric guard
--
-- Background: tax_codes.direction was nullable. PPN rows could be inserted
-- without direction, breaking tax_reports filter
-- `(direction = $1 OR (direction IS NULL AND tax_type='none'))` on the input side
-- and effectively allowing silent misclassification.
--
-- This migration is ASYMMETRIC by design (Option A, owner-acknowledged spec):
--   - PPN rows: direction REQUIRED ('input' for Masukan, 'output' for Keluaran).
--   - PPh and 'none' rows: direction stays NULL (load-bearing for filter logic
--     in routers/tax_codes.py — PPh has no direction concept).
--
-- Steps:
--   1) Backfill existing PPN rows with NULL direction by name heuristic.
--   2) Verify all PPN rows now have non-NULL direction.
--   3) Add CHECK constraint enforcing PPN ⇒ direction IN ('input','output').
--   4) Replace seed_default_tax_codes() to populate direction for PPN inserts.

BEGIN;
-- Add direction column if it doesn't exist (fresh install)
ALTER TABLE tax_codes ADD COLUMN IF NOT EXISTS direction VARCHAR(10);
ALTER TABLE tax_codes ADD COLUMN IF NOT EXISTS ppn_type VARCHAR(20);
ALTER TABLE tax_codes ADD COLUMN IF NOT EXISTS close_class TEXT;
-- Add existing constraint if not present
ALTER TABLE tax_codes DROP CONSTRAINT IF EXISTS tax_codes_direction_check;
ALTER TABLE tax_codes ADD CONSTRAINT tax_codes_direction_check 
    CHECK (direction IS NULL OR direction IN ('input', 'output'));


-- Step 1: Backfill PPN direction by name (Keluaran = output, Masukan = input)
UPDATE tax_codes
SET direction = CASE
    WHEN name ILIKE '%Keluaran%' THEN 'output'
    WHEN name ILIKE '%Masukan%'  THEN 'input'
END
WHERE tax_type = 'ppn'
  AND direction IS NULL
  AND (name ILIKE '%Keluaran%' OR name ILIKE '%Masukan%');

-- Step 2: Verify no PPN rows with NULL direction remain.
-- If any survive, this migration ABORTS (RAISE EXCEPTION).
DO $$
DECLARE
    v_null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_null_count
    FROM tax_codes
    WHERE tax_type = 'ppn' AND direction IS NULL;

    IF v_null_count > 0 THEN
        RAISE EXCEPTION 'V167 ABORT: % PPN row(s) still have NULL direction after backfill', v_null_count;
    END IF;
END $$;

-- Step 3: Add asymmetric CHECK constraint.
-- Coexists with existing tax_codes_direction_check (which restricts the legal
-- values to 'input'/'output' but allows NULL via PG three-valued logic).
ALTER TABLE tax_codes
    ADD CONSTRAINT tax_codes_ppn_direction_check
    CHECK (tax_type != 'ppn' OR (direction IS NOT NULL AND direction IN ('input', 'output')));

-- Step 4: Replace seed_default_tax_codes to set direction for PPN inserts.
CREATE OR REPLACE FUNCTION seed_default_tax_codes(p_tenant_id VARCHAR)
RETURNS void AS $$
DECLARE
    v_ppn_masukan_id  UUID;
    v_ppn_keluaran_id UUID;
    v_utang_pajak_id  UUID;
BEGIN
    SELECT id INTO v_ppn_masukan_id
    FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND account_code = '1-10800';

    SELECT id INTO v_ppn_keluaran_id
    FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND account_code = '2-10600';

    SELECT id INTO v_utang_pajak_id
    FROM chart_of_accounts WHERE tenant_id = p_tenant_id AND account_code = '2-10300';

    IF v_ppn_masukan_id IS NULL OR v_ppn_keluaran_id IS NULL OR v_utang_pajak_id IS NULL THEN
        RAISE NOTICE 'Skipping tax seed for tenant % — missing CoA accounts', p_tenant_id;
        RETURN;
    END IF;

    -- PPN rows: direction REQUIRED. PPh + 'none': direction NULL (load-bearing).
    INSERT INTO tax_codes (tenant_id, code, name, rate, tax_type, direction, is_withholding, coa_id, is_default, coretax_tax_code) VALUES
        (p_tenant_id, 'PPN-11-OUT', 'PPN 11% Keluaran',  11.00, 'ppn',    'output', FALSE, v_ppn_keluaran_id, FALSE, '04'),
        (p_tenant_id, 'PPN-12-OUT', 'PPN 12% Keluaran',  12.00, 'ppn',    'output', FALSE, v_ppn_keluaran_id, TRUE,  '04'),
        (p_tenant_id, 'PPN-11-IN',  'PPN 11% Masukan',   11.00, 'ppn',    'input',  FALSE, v_ppn_masukan_id,  FALSE, '04'),
        (p_tenant_id, 'PPN-12-IN',  'PPN 12% Masukan',   12.00, 'ppn',    'input',  FALSE, v_ppn_masukan_id,  FALSE, '04'),
        (p_tenant_id, 'PPH21',      'PPh 21',             5.00, 'pph21',  NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL),
        (p_tenant_id, 'PPH23-2',    'PPh 23 - Jasa',      2.00, 'pph23',  NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL),
        (p_tenant_id, 'PPH23-15',   'PPh 23 - Dividen',  15.00, 'pph23',  NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL),
        (p_tenant_id, 'PPH4_2',     'PPh 4(2) Final',     0.50, 'pph4_2', NULL,     TRUE,  v_utang_pajak_id,  FALSE, NULL),
        (p_tenant_id, 'NONE',       'Tanpa Pajak',        0.00, 'none',   NULL,     FALSE, NULL,              FALSE, NULL)
    ON CONFLICT (tenant_id, code) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

COMMIT;
