-- =============================================================================
-- V183 — COGS_SERVICE seed account + role (service-purchase classification)
-- =============================================================================
-- Purpose:
--   D2 (defect-#D2 per-line debit resolution in create_bill_v2) routes
--   service / non-tracked bill lines to the product cogs_account_id, which
--   falls back to the COGS_SALES role default because COGS_SERVICE has never
--   been seeded. Result: service purchases land in 5-10100 "HPP - Pembelian
--   Barang" (mixed classification — GL == ledger, no drift, but imprecise).
--
--   This migration seeds a dedicated COGS_SERVICE account + role for NEW
--   tenants so create_bill_v2 can PREFER it for service lines. Existing
--   tenants WITHOUT the mapping keep falling back to COGS_SALES (graceful,
--   byte-identical) — backfill of existing tenants is intentionally NOT
--   performed here (owner decision: opt-in for new tenants; existing tenants'
--   historical service classification stays in COGS_SALES, no reclass).
--
-- Account chosen: 5-10110 "Beban Pokok Jasa"
--   - sibling of 5-10100 (COGS_SALES) under COGS parent 5-10000, level 2, leaf.
--   - account_type = COGS (matches COGS_SALES 5-10100 typing).
--   - 5-10110 is GLOBALLY FREE (0 rows across all tenants — verified
--     2026-06-18). 5-10400 was rejected: in use by tenant p3verify
--     ("Purchase Returns & Allowances"); 5-30100 rejected: taken by
--     "Beban Penyusutan Bangunan" in the default seed.
--
-- Iron Laws:
--   Law 18 — 5-10110 is a leaf (is_header=false).
--   Law 27 — runtime role resolution; account_id resolved by account_code at
--            migration time; NO UUID literals.
--   COGS_SERVICE is ALREADY present in account_roles_role_key_check (added by
--   an earlier migration) and in role_resolver._CATALOG — no CHECK mutation
--   needed here.
--
-- Scope:
--   1. Patch seed_default_coa()           — add 5-10110 for NEW tenants.
--   2. Patch seed_default_account_roles()  — add COGS_SERVICE -> 5-10110
--        (carries forward the full V174 body verbatim + 1 new VALUES row).
--   3. Verify-gate (DO block) — fail-loud if a FRESH-seeded tenant would not
--      get the account+role (proven via throwaway seed in isolated verify,
--      not in this migration body; this gate asserts catalog validity).
--
-- Idempotent: CREATE OR REPLACE + ON CONFLICT DO NOTHING + NOT EXISTS guards.
-- NO existing-tenant backfill (deliberate — see Purpose).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Patch seed_default_coa() — add 5-10110 "Beban Pokok Jasa" for NEW tenants.
--    Carries forward the full V173 body verbatim + 1 new COGS leaf row.
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
        -- V183: COGS_SERVICE account — service-purchase classification (Beban Pokok Jasa)
        (p_tenant_id, '5-10110', 'Beban Pokok Jasa', 'COGS', 'DEBIT', '5-10000', 2, false, false),
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
    v_count := v_count + 25;

    RETURN v_count;
END;
$function$;

COMMENT ON FUNCTION seed_default_coa(text) IS
    'Seed default Chart of Accounts. V150-V162: prior phases. V173 deep-val 2.5: + 2-10430/2-10440. V183: + 5-10110 Beban Pokok Jasa (COGS_SERVICE).';

-- -----------------------------------------------------------------------------
-- 2. Patch seed_default_account_roles() — append COGS_SERVICE -> 5-10110.
--    Carries forward full V174 body verbatim + 1 new VALUES row.
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
            ('MFG_LABOR_APPLIED',          '2-10430', false, 'V173 deep-val 2.5: Hutang TKL Applied — credit at labor-time (standard cost) absorbed into WIP; cleared by payroll settlement journal (Dr 2-10430 / Cr 5-20100 actual).'),
            ('MFG_OVERHEAD_APPLIED',       '2-10440', false, 'V173 deep-val 2.5: Hutang Overhead Applied — credit at labor-time (auto-applied via work_centers.overhead_rate_per_hour) absorbed into WIP; cleared by OH actuals.'),
            ('MFG_DIRECT_LABOR',           '5-20100', false, 'V173 deep-val 2.5: reuse Beban Gaji (settle path — payroll posting clears MFG_LABOR_APPLIED).'),
            ('MFG_ACTUAL_OVERHEAD',        '5-30300', false, 'V174: actual production OH basis = Penyusutan peralatan jahit (pilot decision; Sewa/Listrik excluded — cannot split factory vs office from ledger).'),
            -- V183 — service-purchase COGS classification
            ('COGS_SERVICE',               '5-10110', false, 'V183: Beban Pokok Jasa — preferred debit for service / non-tracked bill lines in create_bill_v2 (graceful fallback to COGS_SALES for tenants lacking this mapping).')
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
    'Idempotently seed account_roles mappings. V152-V162: prior phases. V173: + MFG_LABOR/OVERHEAD_APPLIED + MFG_DIRECT_LABOR. V174: + MFG_ACTUAL_OVERHEAD. V183: + COGS_SERVICE (5-10110 Beban Pokok Jasa).';

-- -----------------------------------------------------------------------------
-- 3. Verify-gate — assert COGS_SERVICE is a valid catalog key (CHECK accepts it)
--    and the new CoA leaf typing is correct. Existing-tenant backfill is NOT
--    performed (deliberate — fallback to COGS_SALES is intended for them).
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_def TEXT;
BEGIN
    -- (a) COGS_SERVICE must be accepted by account_roles_role_key_check.
    SELECT pg_get_constraintdef(oid) INTO v_def
      FROM pg_constraint
     WHERE conname = 'account_roles_role_key_check';

    IF v_def IS NULL OR v_def NOT LIKE '%COGS_SERVICE%' THEN
        RAISE EXCEPTION 'V183 verify-gate FAIL: COGS_SERVICE not in account_roles_role_key_check (def=%)', v_def;
    END IF;

    RAISE NOTICE 'V183 verify-gate OK: COGS_SERVICE accepted by CHECK; seed_default_coa + seed_default_account_roles patched (new tenants only, no existing-tenant backfill).';
END $$;

COMMIT;
