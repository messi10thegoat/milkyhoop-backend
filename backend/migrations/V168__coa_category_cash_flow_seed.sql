-- =============================================================================
-- V168 — CoA Category + Cash Flow Category + is_cash Backfill + Canonical Seed
-- =============================================================================
-- Purpose:
--   Close class "onboarding-completeness" (4th bite: tax_codes #3 → direction #11
--   → category #15 → cash_flow_category #16) via backfill existing tenants AND
--   CREATE OR REPLACE seed_default_coa() so NEW tenants get all three columns
--   populated natively.
--
-- Three columns affected (no NOT NULL/CHECK guard added — V167 lesson #12:
-- non-PPN accounts have load-bearing NULL semantics; partial guard via CHECK
-- considered but rejected due to PPh/equity edge cases).
--
--   1. category (text, Indonesia lowercase, no CHECK)
--      - 15-rule mapping scoped per class-prefix to prevent cross-class bleed
--      - Backfill WHERE NULL/empty; preserves legacy grapgrap/milkytest partial
--
--   2. cash_flow_category (varchar, UPPERCASE English per chk_cash_flow_cat)
--      - INVESTING: aset_tetap/akumulasi_penyusutan
--      - FINANCING: ekuitas
--      - OPERATING: else
--      - Overwrite WHERE NULL/'NONE' literal
--
--   3. is_cash (boolean)
--      - TRUE WHERE category IN ('kas','bank') AND is_header=FALSE
--      - Preserves existing TRUE; never flips parent headers
--
-- Pre-state verified (2026-06-08, mandor recon):
--   - golden-apparel + 4 tenant: 100% NULL category
--   - grapgrap/milkytest: partial legacy (5 cat / 17,11 is_cash already populated)
--   - golden-apparel cash leaves (1-10100/10200/10300) 0 GL movement
--     → saldo_akhir_kas target 20,735,000 NO-SHIFT confirmed
--
-- Iron Laws compliance:
--   Law 18 — V168 touches only category/cash_flow_category/is_cash; trigger
--     prevent_coa_structural_mutation guards account_type/normal_balance/
--     is_header which V168 does NOT touch.
--   Law 27 — runtime resolution unaffected; reports.py refactor (separate commit)
--     reads category column not hardcoded UUIDs.
--
-- Source-of-truth canonical CoA: V154 seed_default_coa(text).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Backfill category (Indonesia lowercase) — scoped per class-prefix
-- -----------------------------------------------------------------------------
-- WHERE clause: only update NULL or empty; preserves legacy partial seeds.

UPDATE chart_of_accounts
SET category = CASE
    -- ASSETS (1-xxxxx)
    WHEN account_code LIKE '1-101%' OR (account_code LIKE '1-%' AND name ILIKE '%kas%')
        THEN 'kas'
    WHEN account_code LIKE '1-102%' OR (account_code LIKE '1-%' AND name ILIKE '%bank%')
        THEN 'bank'
    WHEN account_code LIKE '1-104%' OR account_code LIKE '1-105%'
        OR (account_code LIKE '1-%' AND name ILIKE '%piutang%')
        THEN 'piutang'
    WHEN account_code LIKE '1-106%' OR (account_code LIKE '1-%' AND name ILIKE '%persediaan%')
        THEN 'persediaan'
    WHEN account_code LIKE '1-107%' OR account_code LIKE '1-1082%'
        OR (account_code LIKE '1-%' AND (name ILIKE '%dibayar dimuka%' OR name ILIKE '%prepaid%'))
        THEN 'beban_dibayar_dimuka'
    WHEN account_code LIKE '1-108%'
        OR (account_code LIKE '1-%' AND name ILIKE '%ppn masukan%')
        THEN 'ppn_masukan'
    WHEN account_code LIKE '1-209%'
        OR (account_code LIKE '1-2%' AND name ILIKE '%akumulasi%')
        THEN 'akumulasi_penyusutan'
    WHEN account_code LIKE '1-2%' THEN 'aset_tetap'

    -- LIABILITIES (2-xxxxx)
    WHEN account_code LIKE '2-101%' OR account_code LIKE '2-102%'
        OR (account_code LIKE '2-%' AND name ILIKE '%hutang usaha%')
        THEN 'hutang_usaha'
    WHEN account_code LIKE '2-103%'
        OR (account_code LIKE '2-%' AND (name ILIKE '%hutang pajak%' OR name ILIKE '%pph%'))
        THEN 'hutang_pajak'
    WHEN account_code LIKE '2-104%'
        OR (account_code LIKE '2-%' AND name ILIKE '%hutang gaji%')
        THEN 'hutang_gaji'
    WHEN account_code LIKE '2-105%'
        OR (account_code LIKE '2-%' AND name ILIKE '%uang muka pelanggan%')
        THEN 'uang_muka_pelanggan'
    WHEN account_code LIKE '2-106%'
        OR (account_code LIKE '2-%' AND name ILIKE '%ppn keluaran%')
        THEN 'ppn_keluaran'
    WHEN account_code LIKE '2-1075%' OR account_code = '2-10700'
        OR (account_code LIKE '2-%' AND (name ILIKE '%pendapatan diterima dimuka%' OR name ILIKE '%unearned%'))
        THEN 'unearned_revenue'
    WHEN account_code LIKE '2-20%' OR account_code LIKE '2-21%'
        OR (account_code LIKE '2-%' AND name ILIKE '%hutang bank%')
        THEN 'hutang_bank'
    WHEN account_code LIKE '2-%' THEN 'hutang_usaha'  -- fallback within 2-xxx

    -- EQUITY (3-xxxxx)
    WHEN account_code LIKE '3-%' THEN 'ekuitas'

    -- REVENUE (4-xxxxx)
    WHEN account_code LIKE '4-%' THEN 'pendapatan'

    -- COGS + EXPENSE (5-xxxxx, 6-xxxxx)
    WHEN account_code LIKE '5-%' OR account_code LIKE '6-%' THEN 'beban'

    ELSE NULL
END
WHERE category IS NULL OR category = '';

-- -----------------------------------------------------------------------------
-- 2. Overwrite cash_flow_category (UPPERCASE per chk_cash_flow_cat CHECK)
-- -----------------------------------------------------------------------------
-- 'NONE' literal is the default-from-old-seed sentinel. Overwrite to real value.

UPDATE chart_of_accounts
SET cash_flow_category = CASE
    WHEN category IN ('aset_tetap', 'akumulasi_penyusutan') THEN 'INVESTING'
    WHEN category = 'ekuitas' THEN 'FINANCING'
    WHEN category IN (
        'kas', 'bank', 'piutang', 'persediaan', 'beban_dibayar_dimuka',
        'ppn_masukan', 'hutang_usaha', 'hutang_pajak', 'hutang_gaji',
        'uang_muka_pelanggan', 'ppn_keluaran', 'unearned_revenue', 'hutang_bank',
        'pendapatan', 'beban'
    ) THEN 'OPERATING'
    ELSE cash_flow_category  -- preserve unknown
END
WHERE cash_flow_category IS NULL OR cash_flow_category = 'NONE';

-- -----------------------------------------------------------------------------
-- 3. is_cash flip for leaf kas/bank accounts (preserve existing TRUE)
-- -----------------------------------------------------------------------------
-- Parent headers (is_header=TRUE) explicitly excluded — Stage A surprise lesson.

UPDATE chart_of_accounts
SET is_cash = TRUE
WHERE category IN ('kas', 'bank')
  AND is_header = FALSE
  AND (is_cash IS NULL OR is_cash = FALSE);

-- -----------------------------------------------------------------------------
-- 4. Verify post-backfill (fail-loud if residue)
-- -----------------------------------------------------------------------------
-- Standard accounts (1-/2-/3-/4-/5-/6-) MUST have category populated.

DO $$
DECLARE
    v_null_count INTEGER;
    v_none_count INTEGER;
BEGIN
    -- Verify excludes headers — canonical seed intentionally leaves header
    -- rows (is_header=TRUE) with NULL category / 'NONE' cash_flow_category
    -- since they don't classify (they aggregate children). Only leaf accounts
    -- must be fully populated.
    SELECT COUNT(*) INTO v_null_count
    FROM chart_of_accounts
    WHERE is_header = false
      AND (category IS NULL OR category = '')
      AND (account_code LIKE '1-%' OR account_code LIKE '2-%'
        OR account_code LIKE '3-%' OR account_code LIKE '4-%'
        OR account_code LIKE '5-%' OR account_code LIKE '6-%');

    SELECT COUNT(*) INTO v_none_count
    FROM chart_of_accounts
    WHERE is_header = false
      AND cash_flow_category = 'NONE'
      AND (account_code LIKE '1-%' OR account_code LIKE '2-%'
        OR account_code LIKE '3-%' OR account_code LIKE '4-%'
        OR account_code LIKE '5-%' OR account_code LIKE '6-%');

    IF v_null_count > 0 THEN
        RAISE EXCEPTION 'V168 verify FAIL: % standard accounts have NULL category after backfill', v_null_count;
    END IF;

    IF v_none_count > 0 THEN
        RAISE EXCEPTION 'V168 verify FAIL: % standard accounts retain cash_flow_category=NONE after backfill', v_none_count;
    END IF;

    RAISE NOTICE 'V168 backfill verified: 0 NULL category, 0 NONE cash_flow_category in standard accounts';
END $$;

-- -----------------------------------------------------------------------------
-- 5. CREATE OR REPLACE seed_default_coa() — canonical seed includes 3 columns
-- -----------------------------------------------------------------------------
-- Duplicates V154 catalog with category, cash_flow_category, is_cash per row.
-- Tenant NEW gets all three columns natively (closes onboarding-completeness
-- class permanently — backfill not needed for future tenants).

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

    -- ASSETS
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, normal_balance,
        parent_code, level, is_header, category, cash_flow_category, is_cash
    ) VALUES
        (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false, 'kas', 'OPERATING', true),
        (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false, 'bank', 'OPERATING', false),  -- parent of bank sub-accounts in some tenants
        (p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false, 'kas', 'OPERATING', true),
        (p_tenant_id, '1-10400', 'Piutang Usaha', 'RECEIVABLE', 'DEBIT', '1-10000', 2, false, 'piutang', 'OPERATING', false),
        (p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false, 'piutang', 'OPERATING', false),
        (p_tenant_id, '1-10600', 'Persediaan Barang Dagangan', 'ASSET', 'DEBIT', '1-10000', 2, false, 'persediaan', 'OPERATING', false),
        (p_tenant_id, '1-10700', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false, 'beban_dibayar_dimuka', 'OPERATING', false),
        (p_tenant_id, '1-10800', 'PPN Masukan', 'ASSET', 'DEBIT', '1-10000', 2, false, 'ppn_masukan', 'OPERATING', false),
        (p_tenant_id, '1-10820', 'PPh Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false, 'beban_dibayar_dimuka', 'OPERATING', false),
        (p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '1-20100', 'Tanah', 'ASSET', 'DEBIT', '1-20000', 2, false, 'aset_tetap', 'INVESTING', false),
        (p_tenant_id, '1-20200', 'Bangunan', 'ASSET', 'DEBIT', '1-20000', 2, false, 'aset_tetap', 'INVESTING', false),
        (p_tenant_id, '1-20300', 'Kendaraan', 'ASSET', 'DEBIT', '1-20000', 2, false, 'aset_tetap', 'INVESTING', false),
        (p_tenant_id, '1-20400', 'Peralatan', 'ASSET', 'DEBIT', '1-20000', 2, false, 'aset_tetap', 'INVESTING', false),
        (p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', '1-20000', 2, false, 'akumulasi_penyusutan', 'INVESTING', false);
    v_count := v_count + 16;

    -- LIABILITIES
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, normal_balance,
        parent_code, level, is_header, category, cash_flow_category, is_cash
    ) VALUES
        (p_tenant_id, '2-10000', 'Liabilitas Jangka Pendek', 'LIABILITY', 'CREDIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '2-10100', 'Hutang Usaha', 'PAYABLE', 'CREDIT', '2-10000', 2, false, 'hutang_usaha', 'OPERATING', false),
        (p_tenant_id, '2-10200', 'Hutang Lain-lain', 'LIABILITY', 'CREDIT', '2-10000', 2, false, 'hutang_usaha', 'OPERATING', false),
        (p_tenant_id, '2-10300', 'Hutang Pajak', 'LIABILITY', 'CREDIT', '2-10000', 2, false, 'hutang_pajak', 'OPERATING', false),
        (p_tenant_id, '2-10320', 'Hutang PPh Transaksi', 'LIABILITY', 'CREDIT', '2-10000', 2, false, 'hutang_pajak', 'OPERATING', false),
        (p_tenant_id, '2-10400', 'Hutang Gaji', 'LIABILITY', 'CREDIT', '2-10000', 2, false, 'hutang_gaji', 'OPERATING', false),
        (p_tenant_id, '2-10500', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT', '2-10000', 2, false, 'uang_muka_pelanggan', 'OPERATING', false),
        (p_tenant_id, '2-10600', 'PPN Keluaran', 'LIABILITY', 'CREDIT', '2-10000', 2, false, 'ppn_keluaran', 'OPERATING', false),
        (p_tenant_id, '2-10750', 'Pendapatan Diterima Dimuka', 'LIABILITY', 'CREDIT', '2-10000', 2, false, 'unearned_revenue', 'OPERATING', false),
        (p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false, 'hutang_bank', 'OPERATING', false);
    v_count := v_count + 11;

    -- EQUITY
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, normal_balance,
        parent_code, level, is_header, category, cash_flow_category, is_cash
    ) VALUES
        (p_tenant_id, '3-10000', 'Modal', 'EQUITY', 'CREDIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '3-10100', 'Modal Pemilik', 'EQUITY', 'CREDIT', '3-10000', 2, false, 'ekuitas', 'FINANCING', false),
        (p_tenant_id, '3-20000', 'Laba Ditahan', 'EQUITY', 'CREDIT', NULL, 1, false, 'ekuitas', 'FINANCING', false),
        (p_tenant_id, '3-30000', 'Laba Tahun Berjalan', 'EQUITY', 'CREDIT', NULL, 1, false, 'ekuitas', 'FINANCING', false),
        (p_tenant_id, '3-40000', 'Prive', 'EQUITY', 'DEBIT', NULL, 1, false, 'ekuitas', 'FINANCING', false),
        (p_tenant_id, '3-50000', 'Modal Saldo Awal', 'EQUITY', 'CREDIT', '3-10000', 2, false, 'ekuitas', 'FINANCING', false);
    v_count := v_count + 6;

    -- REVENUE
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, normal_balance,
        parent_code, level, is_header, category, cash_flow_category, is_cash
    ) VALUES
        (p_tenant_id, '4-10000', 'Pendapatan Usaha', 'REVENUE', 'CREDIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '4-10100', 'Penjualan', 'REVENUE', 'CREDIT', '4-10000', 2, false, 'pendapatan', 'OPERATING', false),
        (p_tenant_id, '4-10200', 'Diskon Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false, 'pendapatan', 'OPERATING', false),
        (p_tenant_id, '4-10300', 'Retur Penjualan', 'REVENUE', 'DEBIT', '4-10000', 2, false, 'pendapatan', 'OPERATING', false),
        (p_tenant_id, '4-90000', 'Pendapatan Lain-lain', 'OTHER_INCOME', 'CREDIT', NULL, 1, false, 'pendapatan', 'OPERATING', false);
    v_count := v_count + 5;

    -- COGS + EXPENSE
    INSERT INTO chart_of_accounts (
        tenant_id, account_code, name, account_type, normal_balance,
        parent_code, level, is_header, category, cash_flow_category, is_cash
    ) VALUES
        (p_tenant_id, '5-10000', 'Harga Pokok Penjualan', 'COGS', 'DEBIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '5-10100', 'HPP - Pembelian Barang', 'COGS', 'DEBIT', '5-10000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-10200', 'Diskon Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-10300', 'Retur Pembelian', 'COGS', 'CREDIT', '5-10000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20000', 'Beban Operasional', 'EXPENSE', 'DEBIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '5-20100', 'Beban Gaji', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20200', 'Beban Sewa', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20300', 'Beban Listrik & Air', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20400', 'Beban Telepon & Internet', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20500', 'Beban Transportasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20600', 'Beban Perlengkapan', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20700', 'Beban Pemeliharaan', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20800', 'Beban Administrasi', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-20900', 'Beban Lain-lain', 'EXPENSE', 'DEBIT', '5-20000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-30000', 'Beban Penyusutan', 'EXPENSE', 'DEBIT', NULL, 1, true, NULL, 'NONE', false),
        (p_tenant_id, '5-30100', 'Beban Penyusutan Bangunan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-30200', 'Beban Penyusutan Kendaraan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-30300', 'Beban Penyusutan Peralatan', 'EXPENSE', 'DEBIT', '5-30000', 2, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-80000', 'Beban Pajak', 'EXPENSE', 'DEBIT', NULL, 1, false, 'beban', 'OPERATING', false),
        (p_tenant_id, '5-90000', 'Beban Non-Operasional', 'OTHER_EXPENSE', 'DEBIT', NULL, 1, false, 'beban', 'OPERATING', false);
    v_count := v_count + 20;

    RETURN v_count;
END;
$function$;

COMMENT ON FUNCTION seed_default_coa(text) IS
    'Seed default Chart of Accounts. V154: tax accounts (PPN/PPh). V168: category + cash_flow_category + is_cash columns populated per account (closes onboarding-completeness class).';

COMMIT;
