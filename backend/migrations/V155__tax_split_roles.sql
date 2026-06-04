-- =============================================================================
-- V155 — Tax Split: Promote Roles + Repoint VAT_OUTPUT (Fase D1)
-- =============================================================================
-- Purpose:
--   1. Add WHT_PPH_PAYABLE + WHT_PPH_PREPAID to account_roles CHECK constraint.
--   2. CREATE OR REPLACE seed_default_account_roles() with 4 new tax roles:
--        VAT_OUTPUT          -> 2-10600 (repointed from interim 2-10300)
--        VAT_INPUT           -> 1-10800
--        WHT_PPH_PAYABLE     -> 2-10320 (NEW)
--        WHT_PPH_PREPAID     -> 1-10820 (NEW)
--   3. Repoint EXISTING VAT_OUTPUT mappings from 2-10300 -> 2-10600,
--      set is_interim=false, clear notes.
--   4. Backfill 5/5 tenants via seed_default_account_roles().
--
--   Granular WHT_PPH21/23/4_2/22 reservation RETAINED in CHECK (forward-compat).
--   Only unified WHT_PPH_PAYABLE / WHT_PPH_PREPAID mapped in this phase.
--
--   2-10310 "Utang PPh 21" is PAYROLL-EXCLUSIVE — NEVER mapped to
--   WHT_PPH_PAYABLE. Payroll module retains own account mapping (out of D1).
--
-- Iron Laws:
--   Law 18 — all targets are leaves (is_header=false)
--   Law 27 — runtime resolution
--   Idempotent — re-runnable safely
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Extend CHECK constraint — add WHT_PPH_PAYABLE + WHT_PPH_PREPAID
-- -----------------------------------------------------------------------------
ALTER TABLE account_roles DROP CONSTRAINT IF EXISTS account_roles_role_key_check;

ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check
    CHECK (role_key IN (
        -- TIER 1
        'CASH_GENERAL',
        'BANK_OPERATIONAL',
        'AR_TRADE',
        'AR_OTHER',
        'INVENTORY_MERCHANDISE',
        'AP_TRADE',
        'CUSTOMER_DEPOSIT_LIABILITY',
        'EQUITY_OPENING_BALANCE',
        'REVENUE_SALES_GOODS',
        'REVENUE_SALES_RETURN',
        'COGS_SALES',
        'COGS_PURCHASE_RETURN',
        'REVENUE_DEFERRED',
        -- V155 promoted to TIER 1
        'VAT_OUTPUT',
        'VAT_INPUT',
        'WHT_PPH_PAYABLE',
        'WHT_PPH_PREPAID',
        -- TIER 2
        'CASH_PETTY',
        -- TIER 3 (reserved, NOT seeded)
        'VAT_INPUT_NONCREDITABLE',
        'VAT_PAYABLE_NET',
        -- WHT granular reservation (forward-compat per Q2, NOT mapped in D1)
        'WHT_PPH21',
        'WHT_PPH23',
        'WHT_PPH4_2',
        'WHT_PPH22',
        'ACCUMULATED_DEPRECIATION',
        'IC_SALES',
        'BRANCH_AR',
        'BRANCH_AP',
        -- FUTURE
        'AR_ALLOWANCE',
        'AP_ACCRUED',
        'INVENTORY_RAW',
        'INVENTORY_WIP',
        'INVENTORY_FINISHED',
        'INVENTORY_PACKAGING',
        'INVENTORY_WRITEOFF_DAMAGE',
        'INVENTORY_WRITEOFF_EXPIRED',
        'INVENTORY_WRITEOFF_SHRINKAGE',
        'INVENTORY_RECALL_LOSS',
        'MFG_DIRECT_LABOR',
        'MFG_OVERHEAD_INDIRECT_MATERIAL',
        'MFG_OVERHEAD_INDIRECT_LABOR',
        'MFG_OVERHEAD_UTILITIES',
        'MFG_OVERHEAD_DEPRECIATION',
        'MFG_OVERHEAD_APPLIED',
        'REVENUE_SALES_SERVICE',
        'REVENUE_SALES_DISCOUNT',
        'REVENUE_UNBILLED',
        'COGS_PRODUCTION',
        'COGS_SERVICE',
        'COGS_VARIANCE_MATERIAL',
        'COGS_VARIANCE_LABOR',
        'COGS_VARIANCE_OVERHEAD',
        'CURRENCY_GAIN',
        'CURRENCY_LOSS',
        'CURRENCY_UNREALIZED_FX'
    ));

-- -----------------------------------------------------------------------------
-- 2. CREATE OR REPLACE seed_default_account_roles() — 4 new tax roles
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION seed_default_account_roles(p_tenant_id TEXT)
RETURNS INTEGER AS $$
DECLARE
    v_inserted INTEGER := 0;
    v_account_id UUID;
    v_is_header  BOOLEAN;
    v_map RECORD;
BEGIN
    FOR v_map IN
        SELECT * FROM (VALUES
            -- TIER 1
            ('CASH_GENERAL',               '1-10100', false, NULL::TEXT),
            ('BANK_OPERATIONAL',           '1-10200', false, NULL),
            ('AR_TRADE',                   '1-10400', false, NULL),
            ('AR_OTHER',                   '1-10500', false, NULL),
            ('INVENTORY_MERCHANDISE',      '1-10600', false, NULL),
            ('AP_TRADE',                   '2-10100', false, NULL),
            ('CUSTOMER_DEPOSIT_LIABILITY', '2-10500', false, NULL),
            ('REVENUE_DEFERRED',           '2-10750', false, 'V152 promote: PSAK 72 contract liability — billing event credits this; revenue event debits this.'),
            ('EQUITY_OPENING_BALANCE',     '3-50000', false, NULL),
            ('REVENUE_SALES_GOODS',        '4-10100', false, NULL),
            ('REVENUE_SALES_RETURN',       '4-10300', false, NULL),
            ('COGS_SALES',                 '5-10100', false, NULL),
            ('COGS_PURCHASE_RETURN',       '5-10300', false, NULL),
            -- TIER 2
            ('CASH_PETTY',                 '1-10300', false, 'Kas Kecil (corrected from agen inventaris error)'),
            -- V155 TIER 1 promote (tax split Fase D1)
            ('VAT_OUTPUT',                 '2-10600', false, 'V155 Fase D1: repointed from interim 2-10300 to dedicated PPN Keluaran (LIABILITY).'),
            ('VAT_INPUT',                  '1-10800', false, 'V155 Fase D1: dedicated PPN Masukan (ASSET).'),
            ('WHT_PPH_PAYABLE',            '2-10320', false, 'V155 Fase D1: PPh 23/22/4(2) AP-transaction withholding (LIABILITY). NOT payroll — 2-10310 stays payroll-exclusive.'),
            ('WHT_PPH_PREPAID',            '1-10820', false, 'V155 Fase D1: PPh Dibayar Dimuka (ASSET) — customer withholding from our income = tax credit.')
        ) AS t(role_key, account_code, is_interim, notes)
    LOOP
        SELECT id, is_header INTO v_account_id, v_is_header
          FROM chart_of_accounts
         WHERE tenant_id = p_tenant_id
           AND account_code = v_map.account_code;

        IF v_account_id IS NULL THEN
            RAISE NOTICE 'seed_default_account_roles[%]: skipping role % — account_code % not found',
                p_tenant_id, v_map.role_key, v_map.account_code;
            CONTINUE;
        END IF;

        IF v_is_header THEN
            RAISE NOTICE 'seed_default_account_roles[%]: skipping role % — account_code % is_header=true (Law 18)',
                p_tenant_id, v_map.role_key, v_map.account_code;
            CONTINUE;
        END IF;

        INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
        VALUES (p_tenant_id, v_map.role_key, v_account_id, v_map.is_interim, v_map.notes)
        ON CONFLICT (tenant_id, role_key) DO NOTHING;

        IF FOUND THEN
            v_inserted := v_inserted + 1;
        END IF;
    END LOOP;

    RETURN v_inserted;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION seed_default_account_roles(text) IS
    'Idempotently seed TIER 1 + TIER 2 account_roles mappings. V152: REVENUE_DEFERRED. V155 Fase D1: VAT_OUTPUT repoint to 2-10600 + VAT_INPUT + WHT_PPH_PAYABLE + WHT_PPH_PREPAID.';

-- -----------------------------------------------------------------------------
-- 3. Repoint EXISTING VAT_OUTPUT mappings (interim 2-10300 -> dedicated 2-10600)
--    Set is_interim=false, clear notes.
-- -----------------------------------------------------------------------------
UPDATE account_roles ar
SET account_id = (
        SELECT ca.id FROM chart_of_accounts ca
        WHERE ca.tenant_id = ar.tenant_id AND ca.account_code = '2-10600'
    ),
    is_interim = false,
    notes = 'V155 Fase D1: repointed from interim 2-10300 to dedicated PPN Keluaran (LIABILITY).',
    updated_at = NOW()
WHERE ar.role_key = 'VAT_OUTPUT'
  AND EXISTS (
      SELECT 1 FROM chart_of_accounts ca
      WHERE ca.tenant_id = ar.tenant_id AND ca.account_code = '2-10600'
  );

-- -----------------------------------------------------------------------------
-- 4. Backfill 5/5 — re-run seed_default_account_roles for all tenants
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_n      INTEGER;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        v_n := seed_default_account_roles(v_tenant.id);
        RAISE NOTICE 'V155 backfill tenant=%: % new role mappings', v_tenant.id, v_n;
    END LOOP;
END $$;

COMMIT;
