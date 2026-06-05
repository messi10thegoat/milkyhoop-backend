-- =============================================================================
-- V162 — D4.2 Payroll Backfill (CoA + Role Seed) — FIX LIVE BUG
-- =============================================================================
-- Purpose:
--   Fix production live bug: golden-apparel HTTP error on payroll POST because
--   literal `SALARY_EXPENSE_ACCOUNT = "6-10100"` does not exist in any tenant
--   CoA, and the 5 payroll-specific accounts (PPh 21 / BPJS EE / BPJS ER /
--   Beban BPJS / Beban PPh 21 ER) are missing in 3 tenants.
--
--   D4.1 (V161) promoted 7 roles to TIER 1 catalog. D4.2 (this) seeds the
--   underlying CoA accounts + role mappings so resolve_account_id_by_role()
--   returns valid IDs for the payroll posting path.
--
--   Reference rows replicated from grapgrap (only tenant with all five
--   accounts) — pure replication, NO new code / type / name.
--
-- Iron Laws:
--   Law 18 — all five targets are leaves (is_header=false)
--   Law 27 — runtime resolution via role mapping
--   Idempotent — re-runnable safely (ON CONFLICT DO NOTHING + NOT EXISTS)
--
-- Tenant coverage: 7 ACTIVE tenants per recon
--   anthonius-iwan, golden-apparel, golden-verify, grapgrap, milkytest,
--   ponte-publishing, potus-id.
--   3 missing all 5 accounts: golden-apparel, golden-verify, potus-id.
--   4 already have all 5: anthonius-iwan, grapgrap, milkytest, ponte-publishing.
--   Base accounts (5-20100, 2-10400) verified 7/7.
--
-- Scope of THIS migration:
--   1. seed_default_coa(): add 2-10310, 2-10410, 2-10420, 5-20150, 5-80100 to
--      function body so NEW tenants get them at onboarding.
--   2. Backfill 5 accounts into 3 missing tenants (golden-apparel,
--      golden-verify, potus-id).
--   3. seed_default_account_roles(): add the 7 TIER 1 D4.2 mappings so NEW
--      tenants get them at onboarding.
--   4. Backfill 7 role mappings 7/7 tenants:
--        SALARY_EXPENSE    -> 5-20100
--        SALARY_PAYABLE    -> 2-10400
--        PPH21_PAYABLE     -> 2-10310
--        BPJS_EE_PAYABLE   -> 2-10410
--        BPJS_ER_PAYABLE   -> 2-10420
--        BPJS_ER_EXPENSE   -> 5-20150
--        PPH21_ER_EXPENSE  -> 5-80100
--
-- Out of scope (deferred):
--   - D4.3: refactor payroll.py + payroll_calc.py literals to consume the
--     roles. Done in separate commit, same wave.
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- Recon Step 1-3 (owner-approved 2026-06-05); handover D4 spec.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Patch seed_default_coa() — add the 5 payroll-specific accounts for NEW
--    tenants. Mirrors V159 exactly + 5 new INSERT rows. Function early-returns
--    if CoA exists — safe for existing tenants.
--    Grapgrap baseline (replicated): all 5 = parent_code NULL, level 1,
--    is_header=false, is_active=true, is_system=false, is_cash=false,
--    cash_flow_category=NONE.
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

    -- ASSETS (V154: 1-10800, 1-10820; V156: 1-10550; V159: 1-10650 WIP)
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

    -- LIABILITIES (V154: 2-10320, 2-10600; V162: 2-10310, 2-10410, 2-10420 payroll)
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
        (p_tenant_id, '2-10500', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10600', 'PPN Keluaran', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10750', 'Pendapatan Diterima Dimuka', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false);
    v_count := v_count + 14;

    -- EQUITY
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '3-10000', 'Modal', 'EQUITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '3-10100', 'Modal Pemilik', 'EQUITY', 'CREDIT', '3-10000', 2, false),
        (p_tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-30000', 'Laba Tahun Berjalan', 'EQUITY', 'CREDIT', NULL, 1, false),
        (p_tenant_id, '3-40000', 'Prive', 'EQUITY', 'DEBIT', NULL, 1, false),
        (p_tenant_id, '3-50000', 'Modal Saldo Awal', 'EQUITY', 'CREDIT', '3-10000', 2, false);
    v_count := v_count + 6;

    -- REVENUE
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '4-10000', 'Pendapatan Usaha', 'REVENUE', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '4-10100', 'Penjualan', 'REVENUE', 'CREDIT', '4-10000', 2, false),
        (p_tenant_id, '4-10200', 'Diskon Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-10300', 'Retur Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false),
        (p_tenant_id, '4-90000', 'Pendapatan Lain-lain', 'OTHER_INCOME', 'CREDIT', NULL, 1, false);
    v_count := v_count + 5;

    -- COGS + EXPENSE (V159: 5-50100 adjustment is_system=true; 5-90200 production variance;
    --                  V162: 5-20150 BPJS ER expense, 5-80100 PPh 21 ER expense)
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
    'Seed default Chart of Accounts. V150: 2-10500. V151: 2-10750. V154: 1-10800 + 1-10820 + 2-10320 + 2-10600 (tax split D1). V156: 1-10550 (D2-wrap B). V159: 1-10650 + 5-50100 + 5-90200 (D3.2 manufaktur). V162: 2-10310 + 2-10410 + 2-10420 + 5-20150 + 5-80100 (D4.2 payroll).';

-- -----------------------------------------------------------------------------
-- 2. Backfill 5 payroll accounts into existing tenants (3 missing = golden-apparel,
--    golden-verify, potus-id). Replicates grapgrap baseline rows.
-- -----------------------------------------------------------------------------
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '2-10310', 'Utang PPh 21', 'LIABILITY', 'CREDIT',
    NULL, 1, false, true, false, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10310'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '2-10410', 'Utang BPJS Karyawan', 'LIABILITY', 'CREDIT',
    NULL, 1, false, true, false, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10410'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '2-10420', 'Utang BPJS Perusahaan', 'LIABILITY', 'CREDIT',
    NULL, 1, false, true, false, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10420'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '5-20150', 'Beban BPJS Perusahaan', 'EXPENSE', 'DEBIT',
    NULL, 1, false, true, false, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '5-20150'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '5-80100', 'Beban PPh 21 Perusahaan', 'EXPENSE', 'DEBIT',
    NULL, 1, false, true, false, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '5-80100'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. CREATE OR REPLACE seed_default_account_roles() — add 7 D4.2 mappings
--    Mirrors V159 + 7 new TIER 1 rows.
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
            ('WHT_PPH_PREPAID',            '1-10820', false, 'V155 Fase D1: PPh Dibayar Dimuka (ASSET) — customer withholding from our income = tax credit.'),
            -- V156 TIER 1 promote (Fase D2-wrap B)
            ('AP_PREPAID',                 '1-10550', false, 'V156 Fase D2-wrap B: Uang Muka Pembelian (ASSET).'),
            ('PURCHASE_DISCOUNT',          '5-10200', false, 'V156 Fase D2-wrap B: Diskon Pembelian (contra-COGS).'),
            -- V157 TIER 1 promote (Fase D2-wrap D)
            ('REVENUE_SALES_DISCOUNT',     '4-10200', false, 'V157 Fase D2-wrap D: Diskon Penjualan (contra-revenue).'),
            -- V159 TIER 1 promote (Fase D3.2 — manufaktur)
            ('WIP_GENERIC',                  '1-10650', false, 'V159 Fase D3.2: WIP unified bucket.'),
            ('COGS_VARIANCE_PRODUCTION',     '5-90200', false, 'V159 Fase D3.2: Selisih Produksi lumped variance.'),
            ('WIP_SUBCONTRACT',              '1-10650', false, 'V159 Fase D3.2: subcontract routes to WIP_GENERIC code.'),
            ('INVENTORY_ADJUSTMENT_EXPENSE', '5-50100', false, 'V159 Fase D3.2: Biaya Penyesuaian Persediaan.'),
            -- V162 TIER 1 promote (Fase D4.2 — payroll; fixes live POST bug on golden-apparel)
            ('SALARY_EXPENSE',               '5-20100', false, 'V162 Fase D4.2: Beban Gaji. Flips payroll.py literal SALARY_EXPENSE_ACCOUNT = "6-10100" (non-existent code).'),
            ('SALARY_PAYABLE',               '2-10400', false, 'V162 Fase D4.2: Utang Gaji. Flips payroll.py literal SALARY_PAYABLE_ACCOUNT = "2-10500" (semantically wrong — 2-10500 is customer deposit liability).'),
            ('PPH21_PAYABLE',                '2-10310', false, 'V162 Fase D4.2: Utang PPh 21 (PAYROLL-EXCLUSIVE BOUNDARY). NEVER via WHT_PPH_PAYABLE which points to 2-10320 = AP transaction withholding only.'),
            ('BPJS_EE_PAYABLE',              '2-10410', false, 'V162 Fase D4.2: Utang BPJS Karyawan (employee portion withheld from gross salary).'),
            ('BPJS_ER_PAYABLE',              '2-10420', false, 'V162 Fase D4.2: Utang BPJS Perusahaan (employer portion).'),
            ('BPJS_ER_EXPENSE',              '5-20150', false, 'V162 Fase D4.2: Beban BPJS Perusahaan (employer-side expense matching BPJS_ER_PAYABLE).'),
            ('PPH21_ER_EXPENSE',             '5-80100', false, 'V162 Fase D4.2: Beban PPh 21 Perusahaan (nett method only — when employer bears the tax).')
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
    'Idempotently seed TIER 1 + TIER 2 account_roles mappings. V152: REVENUE_DEFERRED. V155 D1: VAT_OUTPUT/VAT_INPUT/WHT_PPH_PAYABLE/WHT_PPH_PREPAID. V156 D2-wrap B: AP_PREPAID + PURCHASE_DISCOUNT. V157 D2-wrap D: REVENUE_SALES_DISCOUNT. V159 D3.2: WIP_GENERIC + COGS_VARIANCE_PRODUCTION + WIP_SUBCONTRACT + INVENTORY_ADJUSTMENT_EXPENSE. V162 D4.2: SALARY_EXPENSE + SALARY_PAYABLE + PPH21_PAYABLE + BPJS_EE_PAYABLE + BPJS_ER_PAYABLE + BPJS_ER_EXPENSE + PPH21_ER_EXPENSE (payroll).';

-- -----------------------------------------------------------------------------
-- 4. Backfill 7 D4.2 role mappings 7/7 tenants
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
                ('SALARY_EXPENSE',   '5-20100', 'V162 Fase D4.2: Beban Gaji.'),
                ('SALARY_PAYABLE',   '2-10400', 'V162 Fase D4.2: Utang Gaji.'),
                ('PPH21_PAYABLE',    '2-10310', 'V162 Fase D4.2: Utang PPh 21 (payroll-exclusive).'),
                ('BPJS_EE_PAYABLE',  '2-10410', 'V162 Fase D4.2: Utang BPJS Karyawan.'),
                ('BPJS_ER_PAYABLE',  '2-10420', 'V162 Fase D4.2: Utang BPJS Perusahaan.'),
                ('BPJS_ER_EXPENSE',  '5-20150', 'V162 Fase D4.2: Beban BPJS Perusahaan.'),
                ('PPH21_ER_EXPENSE', '5-80100', 'V162 Fase D4.2: Beban PPh 21 Perusahaan (nett method).')
            ) AS t(role_key, account_code, notes)
        LOOP
            SELECT id, is_header INTO v_account_id, v_is_header
              FROM chart_of_accounts
             WHERE tenant_id = v_tenant.id
               AND account_code = v_map.account_code;

            IF v_account_id IS NULL THEN
                RAISE NOTICE 'V162 backfill tenant=% role=%: account_code % not found — skipping',
                    v_tenant.id, v_map.role_key, v_map.account_code;
                CONTINUE;
            END IF;

            IF v_is_header THEN
                RAISE NOTICE 'V162 backfill tenant=% role=%: account_code % is_header=true — skipping (Law 18)',
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

    RAISE NOTICE 'V162: % new D4.2 payroll role mappings inserted', v_inserted;
END $$;

COMMIT;
