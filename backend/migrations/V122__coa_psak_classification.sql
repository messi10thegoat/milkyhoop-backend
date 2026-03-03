-- V122: Add PSAK reporting classification columns to chart_of_accounts
-- Adds is_cash, cash_flow_category, psak_sub_category for financial statement generation.
-- Does NOT modify existing columns or constraints.

-- 1. Add new columns
ALTER TABLE chart_of_accounts
  ADD COLUMN IF NOT EXISTS is_cash BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS cash_flow_category VARCHAR(16) DEFAULT 'NONE',
  ADD COLUMN IF NOT EXISTS psak_sub_category VARCHAR(30);

-- 2. Add constraint for cash_flow_category values
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_cash_flow_cat') THEN
    ALTER TABLE chart_of_accounts
      ADD CONSTRAINT chk_cash_flow_cat
        CHECK (cash_flow_category IN ('OPERATING','INVESTING','FINANCING','NONE'));
  END IF;
END $$;

-- 3. SEED: Mark cash/bank accounts (idempotent)
UPDATE chart_of_accounts SET is_cash = TRUE
WHERE account_type = 'ASSET'
  AND (
    account_code LIKE '1-101%'
    OR account_code LIKE '1-102%'
    OR account_code = '1-10300'
    OR name ILIKE '%kas%'
    OR name ILIKE '%bank%'
    OR name ILIKE '%cash%'
    OR name ILIKE '%petty%'
  )
  AND is_cash IS NOT TRUE;

-- 4. SEED: psak_sub_category for key accounts
UPDATE chart_of_accounts SET psak_sub_category = 'TRADE_RECEIVABLE'
WHERE account_type = 'RECEIVABLE'
  AND psak_sub_category IS NULL;

UPDATE chart_of_accounts SET psak_sub_category = 'INVENTORY'
WHERE account_type = 'ASSET'
  AND (name ILIKE '%persediaan%' OR name ILIKE '%inventory%')
  AND psak_sub_category IS NULL;

UPDATE chart_of_accounts SET psak_sub_category = 'TRADE_PAYABLE'
WHERE account_type = 'PAYABLE'
  AND psak_sub_category IS NULL;

UPDATE chart_of_accounts SET psak_sub_category = 'PAID_IN_CAPITAL'
WHERE account_type = 'EQUITY'
  AND (name ILIKE '%modal%' OR name ILIKE '%capital%')
  AND psak_sub_category IS NULL;

UPDATE chart_of_accounts SET psak_sub_category = 'RETAINED_EARNINGS'
WHERE account_type = 'EQUITY'
  AND (name ILIKE '%laba ditahan%' OR name ILIKE '%saldo laba%' OR name ILIKE '%retained%')
  AND psak_sub_category IS NULL;

UPDATE chart_of_accounts SET psak_sub_category = 'FIXED_ASSET'
WHERE account_type = 'ASSET'
  AND (
    name ILIKE '%peralatan%'
    OR name ILIKE '%kendaraan%'
    OR name ILIKE '%bangunan%'
    OR name ILIKE '%tanah%'
    OR name ILIKE '%mesin%'
    OR name ILIKE '%fixed asset%'
    OR name ILIKE '%aset tetap%'
  )
  AND is_header = FALSE
  AND psak_sub_category IS NULL;

-- Mark accumulated depreciation
UPDATE chart_of_accounts SET psak_sub_category = 'ACCUM_DEPRECIATION'
WHERE account_type = 'ASSET'
  AND (name ILIKE '%akumulasi%' OR name ILIKE '%accumulated%' OR name ILIKE '%penyusutan%')
  AND is_header = FALSE
  AND psak_sub_category IS NULL;

-- 5. Cash flow category for investing accounts (fixed assets)
UPDATE chart_of_accounts SET cash_flow_category = 'INVESTING'
WHERE psak_sub_category IN ('FIXED_ASSET', 'ACCUM_DEPRECIATION')
  AND cash_flow_category = 'NONE';

-- 6. Cash flow category for financing accounts (equity, long-term debt)
UPDATE chart_of_accounts SET cash_flow_category = 'FINANCING'
WHERE account_type = 'EQUITY'
  AND cash_flow_category = 'NONE';

UPDATE chart_of_accounts SET cash_flow_category = 'FINANCING'
WHERE account_type = 'LIABILITY'
  AND (name ILIKE '%hutang bank%' OR name ILIKE '%pinjaman%' OR name ILIKE '%loan%')
  AND cash_flow_category = 'NONE';
