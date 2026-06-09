-- =============================================================================
-- V173 — Deep-val 2.5 — Production Costing Accounts (Labor/OH Applied)
-- =============================================================================
-- Purpose:
--   Standard-cost labor + overhead application for manufacturing.
--   Owner directive (deep-val 2.5, recon 2026-06-09):
--     (1) standard cost = actual_hours × work_centers.{labor,overhead}_rate_per_hour
--     (2) labor + OH auto-applied at labor-time (record_labor handler)
--     (3) liability clearing accounts:
--           2-10430 Hutang TKL Applied   <- MFG_LABOR_APPLIED   (NEW catalog key)
--           2-10440 Hutang Overhead Applied <- MFG_OVERHEAD_APPLIED (existing role,
--                                              never seeded before this migration)
--     (4) variance roll-up at complete_order via existing
--           COGS_VARIANCE_PRODUCTION -> 5-90200 Selisih Produksi (no change)
--     (5) MFG_DIRECT_LABOR reuses 5-20100 Beban Gaji (no new CoA; settle path)
--
-- Iron Laws:
--   Law 4  — 2-10300 Hutang Pajak untouched (sanity asserted in test scenario).
--   Law 16 — journal-derived costing (Σ Dr WIP = Σ Cr WIP at order completion).
--   Law 18 — all new accounts are leaves (is_header=false).
--   Law 20 — DRAFT → POSTED transition done by handler (this migration only seeds).
--   Law 27 — runtime resolution via role mapping; no UUID literals introduced.
--   Iron Law CHECK-CONSTRAINT — MFG_LABOR_APPLIED added to
--     account_roles_role_key_check verbatim-preserving all 76 existing keys.
--
-- Scope:
--   1. ALTER account_roles CHECK constraint — append 'MFG_LABOR_APPLIED'.
--   2. Add journal_source_types 'PRODUCTION_LABOR' + 'PRODUCTION_OVERHEAD'.
--   3. Patch seed_default_coa() — add 2-10430 + 2-10440 for NEW tenants.
--   4. Patch seed_default_account_roles() — add MFG_LABOR_APPLIED +
--        MFG_OVERHEAD_APPLIED + MFG_DIRECT_LABOR mappings for NEW tenants.
--   5. Backfill 2-10430 + 2-10440 into all 7 existing tenants.
--   6. Backfill 3 role mappings into all 7 tenants.
--   7. Verify-gate (DO block) — fail-loud if any tenant lacks the 3 mappings.
--
-- Idempotent: re-runnable via ON CONFLICT DO NOTHING + NOT EXISTS.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Catalog: extend account_roles_role_key_check (preserve all 76 keys verbatim)
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
    -- V173 deep-val 2.5 (NEW):
    'MFG_LABOR_APPLIED'
  ]));

-- -----------------------------------------------------------------------------
-- 2. journal_source_types — add PRODUCTION_LABOR + PRODUCTION_OVERHEAD
-- -----------------------------------------------------------------------------
INSERT INTO journal_source_types (source_type) VALUES
  ('PRODUCTION_LABOR'),
  ('PRODUCTION_OVERHEAD')
ON CONFLICT (source_type) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Patch seed_default_coa() — add 2-10430 + 2-10440 for NEW tenants
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION seed_default_coa(p_tenant_id text)
RETURNS integer
LANGUAGE plpgsql
AS $function$
DECLARE
    v_count INTEGER := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM "Tenant" WHERE id = p_tenant_id) THEN
        RAISE EXCEPTION 'Tenant not found: %', p_tenant_id;
    END IF;

    IF EXISTS (SELECT 1 FROM chart_of_accounts WHERE tenant_id = p_tenant_id LIMIT 1) THEN
        RAISE NOTICE 'CoA already exists for tenant %, skipping', p_tenant_id;
        RETURN 0;
    END IF;

    PERFORM set_config('app.tenant_id', p_tenant_id, true);

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10400', 'Piutang Usaha', 'RECEIVABLE', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10550', 'Uang Muka Pembelian', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10600', 'Persediaan Barang Dagangan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10650', 'Barang Dalam Proses (WIP)', 'ASSET', 'DEBIT', NULL, 1, false),
        (p_tenant_id, '1-10700', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10800', 'PPN Masukan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10820', 'PPh Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-20100', 'Tanah', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20200', 'Bangunan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20300', 'Kendaraan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20400', 'Peralatan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', '1-20000', 2, false);
    v_count := v_count + 18;

    -- LIABILITIES (V173 adds 2-10430 Hutang TKL Applied + 2-10440 Hutang Overhead Applied)
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '2-10000', 'Liabilitas Jangka Pendek', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-10100', 'Hutang Usaha', 'PAYABLE', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10200', 'Hutang Lain-lain', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10300', 'Hutang Pajak', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10310', 'Utang PPh 21', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10320', 'Hutang PPh Transaksi', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10400', 'Hutang Gaji', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10410', 'Utang BPJS Karyawan', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10420', 'Utang BPJS Perusahaan', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10430', 'Hutang TKL Applied', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10440', 'Hutang Overhead Applied', 'LIABILITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '2-10500', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10600', 'PPN Keluaran', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10750', 'Pendapatan Diterima Dimuka', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false);
    v_count := v_count + 16;

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '3-10000', 'Modal', 'EQUITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '3-10100', 'Modal Pemilik', 'EQUITY', 'CREDIT', '3-10000', 2, false),
        (p_tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-30000', 'Laba Tahun Berjalan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-40000', 'Prive', 'EQUITY', 'DEBIT', NULL, 1, false),
        (p_tenant_id, '3-50000', 'Modal Saldo Awal', 'EQUITY', 'CREDIT', '3-10000', 2, false);
    v_count := v_count + 6;

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '4-10000', 'Pendapatan Usaha', 'REVENUE', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '4-10100', 'Penjualan', 'REVENUE', 'CREDIT', '4-10000', 2, false),
        (p_tenant_id, '4-10200', 'Diskon Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-10300', 'Retur Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-90000', 'Pendapatan Lain-lain', 'OTHER_INCOME', 'CREDIT', NULL, 1, false);
    v_count := v_count + 5;

    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header, is_system) VALUES
        (p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'COGS', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-10100', 'HPP - Pembelian Barang', 'COGS', 'DEBIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-10200', 'Diskon Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-10300', 'Retur Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20150', 'Beban BPJS Perusahaan', 'EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-20200', 'Beban Sewa', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20300', 'Beban Listrik & Air', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20400', 'Beban Telepon & Internet', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20500', 'Beban Transportasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20600', 'Beban Perlengkapan', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20700', 'Beban Pemeliharaan', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20800', 'Beban Administrasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-20900', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
        (p_tenant_id, '5-30000', 'Beban Penyusutan', 'EXPENSE', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-30100', 'Beban Penyusutan Bangunan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, false),
        (p_tenant_id, '5-30200', 'Beban Penyusutan Kendaraan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, false),
        (p_tenant_id, '5-30300', 'Beban Penyusutan Peralatan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, false),
        (p_tenant_id, '5-50100', 'Biaya Penyesuaian Persediaan', 'EXPENSE', 'DEBIT', NULL, 2, false, true),
        (p_tenant_id, '5-80000', 'Beban Pajak', 'EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-80100', 'Beban PPh 21 Perusahaan', 'EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-90000', 'Beban Non-Operasional', 'OTHER_EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-90200', 'Selisih Produksi', 'OTHER_EXPENSE', 'DEBIT', '5-90000', 3, false, false);
    v_count := v_count + 24;

    RETURN v_count;
END;
$function$;

COMMENT ON FUNCTION seed_default_coa(text) IS
    'Seed default Chart of Accounts. V150-V162: prior phases. V173 deep-val 2.5: + 2-10430 Hutang TKL Applied + 2-10440 Hutang Overhead Applied.';

-- -----------------------------------------------------------------------------
-- 4. Backfill 2-10430 + 2-10440 into all existing tenants (idempotent)
-- -----------------------------------------------------------------------------
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category, category
)
SELECT
    t.id, '2-10430', 'Hutang TKL Applied', 'LIABILITY', 'CREDIT',
    NULL, 1, false, true, false, false, 'NONE', 'hutang_gaji'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10430'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category, category
)
SELECT
    t.id, '2-10440', 'Hutang Overhead Applied', 'LIABILITY', 'CREDIT',
    NULL, 1, false, true, false, false, 'NONE', 'hutang_gaji'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10440'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 5. Patch seed_default_account_roles() — append V173 mappings (idempotent overlay)
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
            ('MFG_DIRECT_LABOR',           '5-20100', false, 'V173 deep-val 2.5: reuse Beban Gaji (settle path — payroll posting clears MFG_LABOR_APPLIED).')
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
    'Idempotently seed account_roles mappings. V152-V162: prior phases. V173 deep-val 2.5: + MFG_LABOR_APPLIED + MFG_OVERHEAD_APPLIED + MFG_DIRECT_LABOR.';

-- -----------------------------------------------------------------------------
-- 6. Backfill 3 V173 role mappings into all 7 existing tenants
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_map RECORD;
    v_account_id UUID;
    v_is_header  BOOLEAN;
    v_inserted   INTEGER := 0;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        FOR v_map IN
            SELECT * FROM (VALUES
                ('MFG_LABOR_APPLIED',    '2-10430', 'V173: Hutang TKL Applied (standard-cost labor absorption).'),
                ('MFG_OVERHEAD_APPLIED', '2-10440', 'V173: Hutang Overhead Applied (standard-cost OH absorption).'),
                ('MFG_DIRECT_LABOR',     '5-20100', 'V173: reuse Beban Gaji (settle path).')
            ) AS t(role_key, account_code, notes)
        LOOP
            SELECT id, is_header INTO v_account_id, v_is_header
              FROM chart_of_accounts
             WHERE tenant_id = v_tenant.id
               AND account_code = v_map.account_code;

            IF v_account_id IS NULL THEN
                RAISE NOTICE 'V173 backfill tenant=% role=%: account_code % not found — skipping',
                    v_tenant.id, v_map.role_key, v_map.account_code;
                CONTINUE;
            END IF;

            IF v_is_header THEN
                RAISE NOTICE 'V173 backfill tenant=% role=%: account_code % is_header=true — skipping (Law 18)',
                    v_tenant.id, v_map.role_key, v_map.account_code;
                CONTINUE;
            END IF;

            INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
            VALUES (v_tenant.id, v_map.role_key, v_account_id, false, v_map.notes)
            ON CONFLICT (tenant_id, role_key) DO NOTHING;

            IF FOUND THEN
                v_inserted := v_inserted + 1;
            END IF;
        END LOOP;
    END LOOP;

    RAISE NOTICE 'V173: % new role mappings inserted', v_inserted;
END $$;

-- -----------------------------------------------------------------------------
-- 7. Verify-gate — fail-loud if any tenant lacks the 3 mappings
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_missing INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_missing
    FROM "Tenant" t
    CROSS JOIN (VALUES
        ('MFG_LABOR_APPLIED'),
        ('MFG_OVERHEAD_APPLIED'),
        ('MFG_DIRECT_LABOR')
    ) AS r(role_key)
    WHERE NOT EXISTS (
        SELECT 1 FROM account_roles ar
        WHERE ar.tenant_id = t.id AND ar.role_key = r.role_key
    );

    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V173 verify-gate FAIL: % (tenant × role) mappings missing', v_missing;
    END IF;

    RAISE NOTICE 'V173 verify-gate OK: all tenants have MFG_LABOR_APPLIED + MFG_OVERHEAD_APPLIED + MFG_DIRECT_LABOR';
END $$;

COMMIT;
