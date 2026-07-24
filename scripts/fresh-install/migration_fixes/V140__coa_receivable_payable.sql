-- ============================================================================
-- V120: Add RECEIVABLE/PAYABLE account types (Law 29 compliance)
-- Date: 2026-02-27
-- Description: Expand CHECK constraint, reclassify AR/AP accounts,
--              update Law 18 trigger for subtipe-awareness.
-- ============================================================================

-- 1. Pre-migration safety check
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '1-10400' AND normal_balance != 'DEBIT'
  ) THEN
    RAISE EXCEPTION 'ABORT: Piutang Usaha (1-10400) has non-DEBIT normal_balance';
  END IF;

  IF EXISTS (
    SELECT 1 FROM chart_of_accounts
    WHERE account_code = '2-10100' AND normal_balance != 'CREDIT'
  ) THEN
    RAISE EXCEPTION 'ABORT: Hutang Usaha (2-10100) has non-CREDIT normal_balance';
  END IF;
END $$;

-- 2. Expand CHECK constraint
ALTER TABLE chart_of_accounts DROP CONSTRAINT chart_of_accounts_account_type_check;
ALTER TABLE chart_of_accounts ADD CONSTRAINT chart_of_accounts_account_type_check
  CHECK (account_type = ANY (ARRAY[
    'ASSET','RECEIVABLE','LIABILITY','PAYABLE','EQUITY',
    'REVENUE','COGS','EXPENSE','OTHER_INCOME','OTHER_EXPENSE'
  ]));

-- 3. Temporarily disable Law 18 trigger for reclassification
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_prevent_coa_structural_mutation') THEN
    ALTER TABLE chart_of_accounts DISABLE TRIGGER trg_prevent_coa_structural_mutation;
  END IF;
END $$;

-- 4. Reclassify AR/AP accounts (all tenants)
UPDATE chart_of_accounts SET account_type = 'RECEIVABLE'
WHERE account_code = '1-10400';

UPDATE chart_of_accounts SET account_type = 'PAYABLE'
WHERE account_code = '2-10100';

-- 5. Re-enable trigger
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_prevent_coa_structural_mutation') THEN
    ALTER TABLE chart_of_accounts ENABLE TRIGGER trg_prevent_coa_structural_mutation;
  END IF;
END $$;

-- 6. Update Law 18 trigger to be subtipe-aware
CREATE OR REPLACE FUNCTION prevent_coa_structural_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE has_journal_lines BOOLEAN;
BEGIN
  SELECT EXISTS(
    SELECT 1 FROM journal_lines WHERE account_id = OLD.id LIMIT 1
  ) INTO has_journal_lines;

  IF has_journal_lines THEN
    IF OLD.account_type IS DISTINCT FROM NEW.account_type THEN
      -- Allow refinement between parent and subtipe (same normal_balance family)
      IF NOT (
        (OLD.account_type IN ('ASSET','RECEIVABLE') AND NEW.account_type IN ('ASSET','RECEIVABLE'))
        OR
        (OLD.account_type IN ('LIABILITY','PAYABLE') AND NEW.account_type IN ('LIABILITY','PAYABLE'))
      ) THEN
        RAISE EXCEPTION 'Law 18: Cannot change account_type from % to % after journal lines exist (account_id: %)',
          OLD.account_type, NEW.account_type, OLD.id;
      END IF;
    END IF;

    IF OLD.normal_balance IS DISTINCT FROM NEW.normal_balance THEN
      RAISE EXCEPTION 'Law 18: Cannot change normal_balance after journal lines exist (account_id: %)', OLD.id;
    END IF;

    IF OLD.is_header IS DISTINCT FROM NEW.is_header AND NEW.is_header = true THEN
      RAISE EXCEPTION 'Law 18: Cannot convert to header account after journal lines exist (account_id: %)', OLD.id;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;