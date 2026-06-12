-- =============================================================================
-- V174 — Actual Production Overhead role + PRODUCTION_RECONCILE source_type
-- =============================================================================
-- Purpose:
--   Enable month-end manufacturing labor/OH reconciliation (applied-vs-actual,
--   absorption costing). V173 seeded the APPLIED (standard-cost) clearing
--   accounts (2-10430 / 2-10440). This migration adds the *actual* overhead
--   basis role + the reconcile source_type so the period-close journal can post.
--
--   Owner pilot decision (recon 2026-06-12):
--     Actual production overhead basis = 5-30300 "Beban Penyusutan Peralatan"
--     (penyusutan peralatan jahit). Sewa (5-20200) and Listrik (5-20300) are
--     EXCLUDED because the ledger cannot split factory-vs-office portions.
--
-- Iron Laws:
--   Law 18 — 5-30300 is a leaf (is_header=false) — asserted via guard.
--   Law 27 — runtime role resolution; account_id resolved by account_code at
--            migration time; NO UUID literals introduced.
--   Law 6  — new source_type registered in extensible journal_source_types
--            lookup (no CHECK-enum to mutate; FK + UPPER-check govern it).
--
-- Scope:
--   1. ALTER account_roles_role_key_check — append 'MFG_ACTUAL_OVERHEAD'
--        (preserve all existing keys verbatim; V173 re-added this CHECK).
--   2. Register source_type PRODUCTION_RECONCILE in journal_source_types.
--   3. Patch seed_default_account_roles() — append MFG_ACTUAL_OVERHEAD -> 5-30300
--        for NEW tenants (no-op where 5-30300 absent; header-guard).
--   4. Backfill MFG_ACTUAL_OVERHEAD -> 5-30300 into all existing tenants that
--        HAVE 5-30300 (idempotent, ON CONFLICT DO NOTHING, header-guard).
--   5. Verify-gate (DO block) — fail-loud if invariants break.
--
-- Idempotent: re-runnable via DROP/ADD CONSTRAINT + ON CONFLICT DO NOTHING +
--   NOT EXISTS guards.
--
-- NOTE: role_resolver.py _CATALOG updated in same change to add
--       'MFG_ACTUAL_OVERHEAD' (is_valid_role gate). The account_roles
--       role_key CHECK constraint IS present (V173 re-added it) and MUST be
--       extended here or the INSERT violates it.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Catalog: extend account_roles_role_key_check (preserve all keys verbatim,
--    append 'MFG_ACTUAL_OVERHEAD'). Snapshot taken from live DB 2026-06-12.
-- -----------------------------------------------------------------------------
ALTER TABLE account_roles DROP CONSTRAINT IF EXISTS account_roles_role_key_check;
ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check
  CHECK (role_key = ANY (ARRAY[
    'CASH_GENERAL', 'BANK_OPERATIONAL', 'AR_TRADE', 'AR_OTHER',
    'INVENTORY_MERCHANDISE', 'AP_TRADE', 'CUSTOMER_DEPOSIT_LIABILITY',
    'EQUITY_OPENING_BALANCE', 'REVENUE_SALES_GOODS', 'REVENUE_SALES_RETURN',
    'COGS_SALES', 'COGS_PURCHASE_RETURN', 'REVENUE_DEFERRED',
    'VAT_OUTPUT', 'VAT_INPUT', 'WHT_PPH_PAYABLE', 'WHT_PPH_PREPAID',
    'AP_PREPAID', 'PURCHASE_DISCOUNT', 'REVENUE_SALES_DISCOUNT',
    'WIP_GENERIC', 'COGS_VARIANCE_PRODUCTION', 'WIP_SUBCONTRACT',
    'INVENTORY_ADJUSTMENT_EXPENSE',
    'SALARY_EXPENSE', 'SALARY_PAYABLE', 'PPH21_PAYABLE', 'BPJS_EE_PAYABLE',
    'BPJS_ER_PAYABLE', 'BPJS_ER_EXPENSE', 'PPH21_ER_EXPENSE',
    'BANK_FEE', 'CASH_PETTY',
    'VAT_INPUT_NONCREDITABLE', 'VAT_PAYABLE_NET',
    'WHT_PPH21', 'WHT_PPH23', 'WHT_PPH4_2', 'WHT_PPH22',
    'ACCUMULATED_DEPRECIATION', 'IC_SALES', 'BRANCH_AR', 'BRANCH_AP',
    'AR_ALLOWANCE', 'AP_ACCRUED',
    'INVENTORY_RAW', 'INVENTORY_WIP', 'INVENTORY_FINISHED',
    'INVENTORY_PACKAGING',
    'INVENTORY_WRITEOFF_DAMAGE', 'INVENTORY_WRITEOFF_EXPIRED',
    'INVENTORY_WRITEOFF_SHRINKAGE', 'INVENTORY_RECALL_LOSS',
    'MFG_DIRECT_LABOR',
    'MFG_OVERHEAD_INDIRECT_MATERIAL', 'MFG_OVERHEAD_INDIRECT_LABOR',
    'MFG_OVERHEAD_UTILITIES', 'MFG_OVERHEAD_DEPRECIATION',
    'MFG_OVERHEAD_APPLIED',
    'REVENUE_SALES_SERVICE', 'REVENUE_UNBILLED',
    'COGS_PRODUCTION', 'COGS_SERVICE',
    'COGS_VARIANCE_MATERIAL', 'COGS_VARIANCE_LABOR', 'COGS_VARIANCE_OVERHEAD',
    'CURRENCY_GAIN', 'CURRENCY_LOSS', 'CURRENCY_UNREALIZED_FX',
    'WIP_RAW', 'WIP_LABOR', 'WIP_OVERHEAD', 'FG_FINISHED',
    'WRITEOFF_DAMAGE', 'WRITEOFF_EXPIRED', 'WRITEOFF_SHRINKAGE',
    'MFG_LABOR_APPLIED',
    -- V174 (NEW):
    'MFG_ACTUAL_OVERHEAD'
  ]));

-- -----------------------------------------------------------------------------
-- 2. journal_source_types — register PRODUCTION_RECONCILE
-- -----------------------------------------------------------------------------
INSERT INTO journal_source_types (source_type, description) VALUES
  ('PRODUCTION_RECONCILE',
   'Month-end manufacturing labor/OH reconcile (applied-vs-actual, absorption costing)')
ON CONFLICT (source_type) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Patch seed_default_account_roles() — append MFG_ACTUAL_OVERHEAD mapping
--    (idempotent overlay; mirrors V173 structure verbatim + 1 new VALUES row)
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
            ('CASH_GENERAL',               '1-10100', false, NULL::TEXT),
            ('BANK_OPERATIONAL',           '1-10200', false, NULL),
            ('AR_TRADE',                   '1-10400', false, NULL),
            ('AR_OTHER',                   '1-10500', false, NULL),
            ('INVENTORY_MERCHANDISE',      '1-10600', false, NULL),
            ('AP_TRADE',                   '2-10100', false, NULL),
            ('CUSTOMER_DEPOSIT_LIABILITY', '2-10500', false, NULL),
            ('REVENUE_DEFERRED',           '2-10750', false, 'V152 promote.'),
            ('EQUITY_OPENING_BALANCE',     '3-50000', false, NULL),
            ('REVENUE_SALES_GOODS',        '4-10100', false, NULL),
            ('REVENUE_SALES_RETURN',       '4-10300', false, NULL),
            ('COGS_SALES',                 '5-10100', false, NULL),
            ('COGS_PURCHASE_RETURN',       '5-10300', false, NULL),
            ('CASH_PETTY',                 '1-10300', false, 'Kas Kecil.'),
            ('VAT_OUTPUT',                 '2-10600', false, 'V155 D1.'),
            ('VAT_INPUT',                  '1-10800', false, 'V155 D1.'),
            ('WHT_PPH_PAYABLE',            '2-10320', false, 'V155 D1.'),
            ('WHT_PPH_PREPAID',            '1-10820', false, 'V155 D1.'),
            ('AP_PREPAID',                 '1-10550', false, 'V156 D2-wrap B.'),
            ('PURCHASE_DISCOUNT',          '5-10200', false, 'V156 D2-wrap B.'),
            ('REVENUE_SALES_DISCOUNT',     '4-10200', false, 'V157 D2-wrap D.'),
            ('WIP_GENERIC',                '1-10650', false, 'V159 D3.2.'),
            ('COGS_VARIANCE_PRODUCTION',   '5-90200', false, 'V159 D3.2.'),
            ('WIP_SUBCONTRACT',            '1-10650', false, 'V159 D3.2.'),
            ('INVENTORY_ADJUSTMENT_EXPENSE','5-50100', false, 'V159 D3.2.'),
            ('SALARY_EXPENSE',             '5-20100', false, 'V162 D4.2.'),
            ('SALARY_PAYABLE',             '2-10400', false, 'V162 D4.2.'),
            ('PPH21_PAYABLE',              '2-10310', false, 'V162 D4.2.'),
            ('BPJS_EE_PAYABLE',            '2-10410', false, 'V162 D4.2.'),
            ('BPJS_ER_PAYABLE',            '2-10420', false, 'V162 D4.2.'),
            ('BPJS_ER_EXPENSE',            '5-20150', false, 'V162 D4.2.'),
            ('PPH21_ER_EXPENSE',           '5-80100', false, 'V162 D4.2.'),
            -- V173 deep-val 2.5 — manufacturing labor/OH applied (standard cost)
            ('MFG_LABOR_APPLIED',          '2-10430', false, 'V173 deep-val 2.5: Hutang TKL Applied — credit at labor-time (standard cost) absorbed into WIP; cleared by payroll settlement journal (Dr 2-10430 / Cr 5-20100 actual).'),
            ('MFG_OVERHEAD_APPLIED',       '2-10440', false, 'V173 deep-val 2.5: Hutang Overhead Applied — credit at labor-time (auto-applied via work_centers.overhead_rate_per_hour) absorbed into WIP; cleared by OH actuals.'),
            ('MFG_DIRECT_LABOR',           '5-20100', false, 'V173 deep-val 2.5: reuse Beban Gaji (settle path — payroll posting clears MFG_LABOR_APPLIED).'),
            -- V174 — actual production overhead basis (absorption costing reconcile)
            -- ASSUMPTION: actual OH basis = 5-30300 Penyusutan peralatan jahit only.
            -- Sewa (5-20200) / Listrik (5-20300) excluded: ledger cannot split
            -- factory vs office portions (pilot decision 2026-06-12).
            ('MFG_ACTUAL_OVERHEAD',        '5-30300', false, 'V174: actual production OH basis = Penyusutan peralatan jahit (pilot decision; Sewa/Listrik excluded — cannot split factory vs office from ledger).')
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
    'Idempotently seed account_roles mappings. V152-V162: prior phases. V173 deep-val 2.5: + MFG_LABOR_APPLIED + MFG_OVERHEAD_APPLIED + MFG_DIRECT_LABOR. V174: + MFG_ACTUAL_OVERHEAD (5-30300 Penyusutan peralatan; Sewa/Listrik excluded).';

-- -----------------------------------------------------------------------------
-- 4. Backfill MFG_ACTUAL_OVERHEAD -> 5-30300 into existing tenants that HAVE it
--    (idempotent; header-guard; no-op where 5-30300 absent)
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_account_id UUID;
    v_is_header  BOOLEAN;
    v_inserted   INTEGER := 0;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        SELECT id, is_header INTO v_account_id, v_is_header
          FROM chart_of_accounts
         WHERE tenant_id = v_tenant.id
           AND account_code = '5-30300';

        IF v_account_id IS NULL THEN
            RAISE NOTICE 'V174 backfill tenant=%: account_code 5-30300 not found — skipping (no-op)',
                v_tenant.id;
            CONTINUE;
        END IF;

        IF v_is_header THEN
            RAISE NOTICE 'V174 backfill tenant=%: account_code 5-30300 is_header=true — skipping (Law 18)',
                v_tenant.id;
            CONTINUE;
        END IF;

        INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
        VALUES (v_tenant.id, 'MFG_ACTUAL_OVERHEAD', v_account_id, false,
                'V174: actual production OH basis = Penyusutan peralatan jahit (pilot decision; Sewa/Listrik excluded — cannot split factory vs office from ledger).')
        ON CONFLICT (tenant_id, role_key) DO NOTHING;

        IF FOUND THEN
            v_inserted := v_inserted + 1;
        END IF;
    END LOOP;

    RAISE NOTICE 'V174: % new MFG_ACTUAL_OVERHEAD mappings inserted', v_inserted;
END $$;

-- -----------------------------------------------------------------------------
-- 5. Verify-gate — fail-loud if any invariant breaks
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_ga_code   TEXT;
    v_src_ok    INTEGER;
    v_missing   INTEGER;
BEGIN
    -- (a) golden-apparel MFG_ACTUAL_OVERHEAD resolves to a real 5-30300 account
    SELECT c.account_code INTO v_ga_code
      FROM account_roles ar
      JOIN chart_of_accounts c ON c.id = ar.account_id
     WHERE ar.tenant_id = 'golden-apparel'
       AND ar.role_key = 'MFG_ACTUAL_OVERHEAD';

    IF v_ga_code IS NULL THEN
        RAISE EXCEPTION 'V174 verify-gate FAIL: golden-apparel MFG_ACTUAL_OVERHEAD mapping missing';
    END IF;
    IF v_ga_code <> '5-30300' THEN
        RAISE EXCEPTION 'V174 verify-gate FAIL: golden-apparel MFG_ACTUAL_OVERHEAD -> % (expected 5-30300)', v_ga_code;
    END IF;

    -- (b) PRODUCTION_RECONCILE present in lookup
    SELECT COUNT(*) INTO v_src_ok
      FROM journal_source_types
     WHERE source_type = 'PRODUCTION_RECONCILE';
    IF v_src_ok <> 1 THEN
        RAISE EXCEPTION 'V174 verify-gate FAIL: PRODUCTION_RECONCILE not registered in journal_source_types (found %)', v_src_ok;
    END IF;

    -- (c) 0 tenants that HAVE leaf 5-30300 are missing the role mapping
    SELECT COUNT(*) INTO v_missing
      FROM "Tenant" t
      JOIN chart_of_accounts c
        ON c.tenant_id = t.id AND c.account_code = '5-30300' AND c.is_header = false
     WHERE NOT EXISTS (
        SELECT 1 FROM account_roles ar
         WHERE ar.tenant_id = t.id AND ar.role_key = 'MFG_ACTUAL_OVERHEAD'
     );
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V174 verify-gate FAIL: % tenant(s) with 5-30300 missing MFG_ACTUAL_OVERHEAD', v_missing;
    END IF;

    RAISE NOTICE 'V174 verify-gate OK: golden-apparel MFG_ACTUAL_OVERHEAD->5-30300, PRODUCTION_RECONCILE registered, 0 tenants-with-5-30300 unmapped';
END $$;

COMMIT;
