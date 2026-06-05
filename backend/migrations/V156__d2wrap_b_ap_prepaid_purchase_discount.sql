-- =============================================================================
-- V156 — D2-wrap B: AP_PREPAID + PURCHASE_DISCOUNT Roles
-- =============================================================================
-- Purpose:
--   1. CREATE new ASSET account 1-10550 "Uang Muka Pembelian" (Advance to
--      Vendor) — semantically correct asset for advance payments to vendors.
--      Currently bill_payments.py uses 1-10500 (AR_OTHER = Piutang Lain-lain)
--      which is semantically WRONG (AR_OTHER = piutang non-trade, not vendor
--      advance / aset uang muka pembelian).
--   2. Add AP_PREPAID + PURCHASE_DISCOUNT to account_roles CHECK constraint.
--   3. CREATE OR REPLACE seed_default_account_roles() with 2 new TIER 1 roles:
--        AP_PREPAID          -> 1-10550 (NEW)
--        PURCHASE_DISCOUNT   -> 5-10200 (already exists in 5/5 tenants)
--   4. Patch seed_default_coa() so NEW tenants get 1-10550 in initial seed.
--      (Function early-returns if CoA exists — safe for existing tenants.)
--   5. Backfill 1-10550 into existing tenants via WHERE-NOT-EXISTS INSERT.
--   6. Backfill role mappings 5/5 tenants via seed_default_account_roles().
--
-- Iron Laws:
--   Law 18 — both targets are leaves (is_header=false)
--   Law 27 — runtime resolution via role mapping
--   Idempotent — re-runnable safely (ON CONFLICT DO NOTHING)
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Patch seed_default_coa() — add 1-10550 for NEW tenants
--    (Function body mirrors V154 exactly + one new line for 1-10550.)
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

    -- ASSETS (V154: added 1-10800 PPN Masukan, 1-10820 PPh Dibayar Dimuka)
    -- (V156: added 1-10550 Uang Muka Pembelian)
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10400', 'Piutang Usaha', 'RECEIVABLE', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10550', 'Uang Muka Pembelian', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10600', 'Persediaan Barang Dagangan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10700', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10800', 'PPN Masukan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10820', 'PPh Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-20100', 'Tanah', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20200', 'Bangunan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20300', 'Kendaraan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20400', 'Peralatan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', '1-20000', 2, false);
    v_count := v_count + 17;

    -- LIABILITIES (V154: added 2-10320 Hutang PPh Transaksi, 2-10600 PPN Keluaran)
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '2-10000', 'Liabilitas Jangka Pendek', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-10100', 'Hutang Usaha', 'PAYABLE', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10200', 'Hutang Lain-lain', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10300', 'Hutang Pajak', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10320', 'Hutang PPh Transaksi', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10400', 'Hutang Gaji', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10500', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10600', 'PPN Keluaran', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10750', 'Pendapatan Diterima Dimuka', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false);
    v_count := v_count + 11;

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

    -- COGS + EXPENSE
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'COGS', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '5-10100', 'HPP - Pembelian Barang', 'COGS', 'DEBIT', '5-10000', 2, false),
        (p_tenant_id, '5-10200', 'Diskon Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false),
        (p_tenant_id, '5-10300', 'Retur Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false),
        (p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20200', 'Beban Sewa', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20300', 'Beban Listrik & Air', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20400', 'Beban Telepon & Internet', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20500', 'Beban Transportasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20600', 'Beban Perlengkapan', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20700', 'Beban Pemeliharaan', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20800', 'Beban Administrasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-20900', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', '5-20000', 2, false),
        (p_tenant_id, '5-30000', 'Beban Penyusutan', 'EXPENSE', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '5-30100', 'Beban Penyusutan Bangunan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (p_tenant_id, '5-30200', 'Beban Penyusutan Kendaraan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (p_tenant_id, '5-30300', 'Beban Penyusutan Peralatan', 'EXPENSE', 'DEBIT', '5-30000', 2, false),
        (p_tenant_id, '5-80000', 'Beban Pajak', 'EXPENSE', 'DEBIT', NULL, 1, false),
        (p_tenant_id, '5-90000', 'Beban Non-Operasional', 'OTHER_EXPENSE', 'DEBIT', NULL, 1, false);
    v_count := v_count + 20;

    RETURN v_count;
END;
$function$;

COMMENT ON FUNCTION seed_default_coa(text) IS
    'Seed default Chart of Accounts. V150: 2-10500. V151: 2-10750. V154: 1-10800 + 1-10820 + 2-10320 + 2-10600 (tax split D1). V156: 1-10550 Uang Muka Pembelian (D2-wrap B).';

-- -----------------------------------------------------------------------------
-- 2. Backfill 1-10550 into existing tenants (idempotent)
-- -----------------------------------------------------------------------------
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system
)
SELECT
    t.id, '1-10550', 'Uang Muka Pembelian', 'ASSET', 'DEBIT',
    '1-10000', 2, false, true, false
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '1-10550'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Extend CHECK constraint — add AP_PREPAID + PURCHASE_DISCOUNT
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
        'VAT_OUTPUT',
        'VAT_INPUT',
        'WHT_PPH_PAYABLE',
        'WHT_PPH_PREPAID',
        -- V156 D2-wrap B promoted to TIER 1
        'AP_PREPAID',
        'PURCHASE_DISCOUNT',
        -- TIER 2
        'CASH_PETTY',
        -- TIER 3 (reserved, NOT seeded)
        'VAT_INPUT_NONCREDITABLE',
        'VAT_PAYABLE_NET',
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
-- 4. CREATE OR REPLACE seed_default_account_roles() — add AP_PREPAID + PURCHASE_DISCOUNT
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
            -- V156 D2-wrap B (AP-side advance + purchase discount)
            ('AP_PREPAID',                 '1-10550', false, 'V156 D2-wrap B: Uang Muka Pembelian (ASSET) — advance payment to vendor before bill received. Previously mis-mapped to 1-10500 AR_OTHER literal in bill_payments.py.'),
            ('PURCHASE_DISCOUNT',          '5-10200', false, 'V156 D2-wrap B: Diskon Pembelian (COGS contra) — vendor cash/early-pay discount on bill payment.')
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
    'Idempotently seed TIER 1 + TIER 2 account_roles mappings. V152: REVENUE_DEFERRED. V155 Fase D1: VAT_OUTPUT repoint to 2-10600 + VAT_INPUT + WHT_PPH_PAYABLE + WHT_PPH_PREPAID. V156 D2-wrap B: AP_PREPAID (1-10550) + PURCHASE_DISCOUNT (5-10200).';

-- -----------------------------------------------------------------------------
-- 5. Backfill 5/5 — re-run seed_default_account_roles for all tenants
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_n      INTEGER;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        v_n := seed_default_account_roles(v_tenant.id);
        RAISE NOTICE 'V156 backfill tenant=%: % new role mappings', v_tenant.id, v_n;
    END LOOP;
END $$;

COMMIT;
