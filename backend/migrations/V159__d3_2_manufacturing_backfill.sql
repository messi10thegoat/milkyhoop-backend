-- =============================================================================
-- V159 — D3.2 Manufaktur Backfill (CoA + Role Seed) — FIX LIVE BUG
-- =============================================================================
-- Purpose:
--   Fix production live bug: 4 tenants (anthonius-iwan, milkytest,
--   ponte-publishing, potus-id) hit HTTP 500 on Work Order release because
--   account 1-10650 "Barang Dalam Proses (WIP)" does not exist in their CoA
--   and WIP_GENERIC role is not seeded.
--
--   D3.1 (V158) only promoted 4 roles to TIER 1 catalog. D3.2 (this) seeds the
--   underlying CoA accounts + role mappings so resolve_account_id_by_role()
--   returns valid IDs for the production / subcontract / variance / stock
--   adjustment paths.
--
--   Reference rows replicated from grapgrap (only tenant that had all three
--   accounts pre-D3) — pure replication, NO new code / type / name.
--
-- Iron Laws:
--   Law 18 — all three targets are leaves (is_header=false)
--   Law 27 — runtime resolution via role mapping
--   Idempotent — re-runnable safely (ON CONFLICT DO NOTHING + NOT EXISTS)
--
-- Scope of THIS migration:
--   1. seed_default_coa(): add 1-10650, 5-50100, 5-90200 to function body so
--      NEW tenants get them at onboarding.
--   2. Backfill 1-10650 into 4 missing tenants (anthonius-iwan, milkytest,
--      ponte-publishing, potus-id).
--   3. Backfill 5-90200 into 4 missing tenants (same set).
--   4. Backfill 5-50100 into 3 missing tenants (anthonius-iwan,
--      ponte-publishing, potus-id — milkytest already has it).
--   5. seed_default_account_roles(): add the 4 TIER 1 D3.2 mappings so NEW
--      tenants get them at onboarding.
--   6. Backfill 4 role mappings 5/5 tenants:
--        WIP_GENERIC                  -> 1-10650
--        COGS_VARIANCE_PRODUCTION     -> 5-90200
--        WIP_SUBCONTRACT              -> 1-10650  (reuses WIP_GENERIC code)
--        INVENTORY_ADJUSTMENT_EXPENSE -> 5-50100
--
-- Out of scope (deferred):
--   - D3.3: refactor production.py / stock_adjustments.py / bills_service.py
--     to consume the roles. NOT touched here.
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- Recon Step 1 (owner-approved 2026-06-05); handover
--   DOCS/plans/2026-06-05-coa-role-migration-handover.md §5.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Patch seed_default_coa() — add 1-10650 (WIP), 5-50100 (adjustment),
--    5-90200 (production variance) for NEW tenants.
--    Mirrors V156 exactly + three new lines. Function early-returns if CoA
--    exists — safe for existing tenants.
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

    -- LIABILITIES (V154: 2-10320, 2-10600)
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

    -- COGS + EXPENSE (V159: 5-50100 adjustment is_system=true; 5-90200 production variance)
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header, is_system) VALUES
        (p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'COGS', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-10100', 'HPP - Pembelian Barang', 'COGS', 'DEBIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-10200', 'Diskon Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-10300', 'Retur Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, false),
        (p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', NULL, 1, true, false),
        (p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', '5-20000', 2, false, false),
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
        (p_tenant_id, '5-90000', 'Beban Non-Operasional', 'OTHER_EXPENSE', 'DEBIT', NULL, 1, false, false),
        (p_tenant_id, '5-90200', 'Selisih Produksi', 'OTHER_EXPENSE', 'DEBIT', '5-90000', 3, false, false);
    v_count := v_count + 22;

    RETURN v_count;
END;
$function$;

COMMENT ON FUNCTION seed_default_coa(text) IS
    'Seed default Chart of Accounts. V150: 2-10500. V151: 2-10750. V154: 1-10800 + 1-10820 + 2-10320 + 2-10600 (tax split D1). V156: 1-10550 Uang Muka Pembelian (D2-wrap B). V159: 1-10650 WIP + 5-50100 adjustment + 5-90200 production variance (D3.2 manufaktur).';

-- -----------------------------------------------------------------------------
-- 2. Backfill 1-10650 "Barang Dalam Proses (WIP)" into existing tenants
--    Replicates grapgrap row: ASSET / DEBIT / parent NULL / level 1 /
--                              is_header=false / is_active=true / is_system=false / is_cash=false / cash_flow_category=NONE
-- -----------------------------------------------------------------------------
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '1-10650', 'Barang Dalam Proses (WIP)', 'ASSET', 'DEBIT',
    NULL, 1, false, true, false, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '1-10650'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Backfill 5-90200 "Selisih Produksi" (OTHER_EXPENSE, parent 5-90000, level 3)
--    Replicates grapgrap row.
-- -----------------------------------------------------------------------------
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '5-90200', 'Selisih Produksi', 'OTHER_EXPENSE', 'DEBIT',
    '5-90000', 3, false, true, false, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '5-90200'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 4. Backfill 5-50100 "Biaya Penyesuaian Persediaan" (EXPENSE, parent NULL, level 2)
--    Replicates grapgrap row (is_system=true).
-- -----------------------------------------------------------------------------
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system, is_cash, cash_flow_category
)
SELECT
    t.id, '5-50100', 'Biaya Penyesuaian Persediaan', 'EXPENSE', 'DEBIT',
    NULL, 2, false, true, true, false, 'NONE'
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '5-50100'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 5. CREATE OR REPLACE seed_default_account_roles() — add 4 D3.2 mappings
--    Mirrors V157 + 4 new TIER 1 rows.
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
            ('AP_PREPAID',                 '1-10550', false, 'V156 Fase D2-wrap B: Uang Muka Pembelian (ASSET). Flips bill_payments.py 254,381 hardcoded 1-10500 (AR_OTHER mis-semantic).'),
            ('PURCHASE_DISCOUNT',          '5-10200', false, 'V156 Fase D2-wrap B: Diskon Pembelian (contra-COGS).'),
            -- V157 TIER 1 promote (Fase D2-wrap D)
            ('REVENUE_SALES_DISCOUNT',     '4-10200', false, 'V157 Fase D2-wrap D: Diskon Penjualan (contra-revenue). Flips receive_payments.py:1602 hardcoded fallback (was non-existent literal 6-10100).'),
            -- V159 TIER 1 promote (Fase D3.2 — manufaktur backfill, fixes live 500 on WO release)
            ('WIP_GENERIC',                  '1-10650', false, 'V159 Fase D3.2: Barang Dalam Proses (WIP) unified bucket. Single WIP account for all production cost flows (raw+labor+overhead). Will replace literals in production.py at D3.3.'),
            ('COGS_VARIANCE_PRODUCTION',     '5-90200', false, 'V159 Fase D3.2: Selisih Produksi (lumped variance, OTHER_EXPENSE).'),
            ('WIP_SUBCONTRACT',              '1-10650', false, 'V159 Fase D3.2: subcontract/maklon biaya routes into the same WIP_GENERIC account (1-10650). Per recon Step 1: subcontract path -> WIP, no separate code.'),
            ('INVENTORY_ADJUSTMENT_EXPENSE', '5-50100', false, 'V159 Fase D3.2: Biaya Penyesuaian Persediaan (EXPENSE). Will replace literals in stock_adjustments.py at D3.3.')
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
    'Idempotently seed TIER 1 + TIER 2 account_roles mappings. V152: REVENUE_DEFERRED. V155 D1: VAT_OUTPUT repoint + VAT_INPUT + WHT_PPH_PAYABLE + WHT_PPH_PREPAID. V156 D2-wrap B: AP_PREPAID + PURCHASE_DISCOUNT. V157 D2-wrap D: REVENUE_SALES_DISCOUNT. V159 D3.2: WIP_GENERIC + COGS_VARIANCE_PRODUCTION + WIP_SUBCONTRACT + INVENTORY_ADJUSTMENT_EXPENSE (manufaktur).';

-- -----------------------------------------------------------------------------
-- 6. Backfill 4 D3.2 role mappings 5/5 tenants
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
                ('WIP_GENERIC',                  '1-10650', 'V159 Fase D3.2: WIP unified bucket (fixes live 500 on WO release).'),
                ('COGS_VARIANCE_PRODUCTION',     '5-90200', 'V159 Fase D3.2: lumped production variance.'),
                ('WIP_SUBCONTRACT',              '1-10650', 'V159 Fase D3.2: subcontract routes to WIP_GENERIC code.'),
                ('INVENTORY_ADJUSTMENT_EXPENSE', '5-50100', 'V159 Fase D3.2: generic stock adjustment loss.')
            ) AS t(role_key, account_code, notes)
        LOOP
            SELECT id, is_header INTO v_account_id, v_is_header
              FROM chart_of_accounts
             WHERE tenant_id = v_tenant.id
               AND account_code = v_map.account_code;

            IF v_account_id IS NULL THEN
                RAISE NOTICE 'V159 backfill tenant=% role=%: account_code % not found — skipping',
                    v_tenant.id, v_map.role_key, v_map.account_code;
                CONTINUE;
            END IF;

            IF v_is_header THEN
                RAISE NOTICE 'V159 backfill tenant=% role=%: account_code % is_header=true — skipping (Law 18)',
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

    RAISE NOTICE 'V159: % new D3.2 manufaktur role mappings inserted', v_inserted;
END $$;

COMMIT;
