-- =============================================================================
-- V154 — Tax Split: Seed Dedicated Tax Accounts (Fase D1)
-- =============================================================================
-- Purpose:
--   Split tax accounts from generic 2-10300 "Hutang Pajak" interim mapping
--   into dedicated accounts per tax type (hybrid Opsi c).
--
--   4 accounts seeded (idempotent ON CONFLICT DO NOTHING):
--     - 2-10600  PPN Keluaran           (LIABILITY) — backfill 3 tenants
--     - 1-10800  PPN Masukan            (ASSET)    — backfill 3 tenants
--     - 2-10320  Hutang PPh Transaksi   (LIABILITY, NEW) — seed 5/5 tenants
--     - 1-10820  PPh Dibayar Dimuka     (ASSET, NEW)     — seed 5/5 tenants
--
--   Existing 2-10310 "Utang PPh 21" is PAYROLL-EXCLUSIVE — left untouched.
--   WHT_PPH_PAYABLE (2-10320) is for PPh 23/22/4(2) AP-transaction ONLY.
--
--   Also CREATE OR REPLACE seed_default_coa() so NEW tenants automatically
--   get all 4 tax accounts (pola V151).
--
-- Tenants currently missing 2-10600 + 1-10800: anthonius-iwan, ponte-publishing, potus-id
-- Tenants currently with 2-10600 + 1-10800: grapgrap, milkytest (untouched)
--
-- Iron Laws:
--   Law 18 — all 4 accounts are leaves (is_header=false)
--   Law 27 — runtime resolution via role mapping (V155)
--   Idempotent — re-runnable safely (ON CONFLICT DO NOTHING)
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Patch seed_default_coa() — add 4 tax accounts for NEW tenants
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
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10400', 'Piutang Usaha', 'RECEIVABLE', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false),
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
    v_count := v_count + 16;

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
    'Seed default Chart of Accounts. V150: 2-10500. V151: 2-10750. V154: 1-10800 PPN Masukan + 1-10820 PPh Dibayar Dimuka + 2-10320 Hutang PPh Transaksi + 2-10600 PPN Keluaran (tax split Fase D1).';

-- -----------------------------------------------------------------------------
-- 2. Backfill 4 tax accounts into existing tenants (idempotent)
-- -----------------------------------------------------------------------------
-- 2-10600 PPN Keluaran (LIABILITY) — missing for 3 tenants
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system
)
SELECT
    t.id, '2-10600', 'PPN Keluaran', 'LIABILITY', 'CREDIT',
    '2-10000', 2, false, true, false
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10600'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- 1-10800 PPN Masukan (ASSET) — missing for 3 tenants
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system
)
SELECT
    t.id, '1-10800', 'PPN Masukan', 'ASSET', 'DEBIT',
    '1-10000', 2, false, true, false
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '1-10800'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- 2-10320 Hutang PPh Transaksi (LIABILITY, NEW) — seed 5/5
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system
)
SELECT
    t.id, '2-10320', 'Hutang PPh Transaksi', 'LIABILITY', 'CREDIT',
    '2-10000', 2, false, true, false
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10320'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- 1-10820 PPh Dibayar Dimuka (ASSET, NEW) — seed 5/5
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system
)
SELECT
    t.id, '1-10820', 'PPh Dibayar Dimuka', 'ASSET', 'DEBIT',
    '1-10000', 2, false, true, false
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '1-10820'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Tenant.is_pkp toggle (default true for backward compatibility)
-- -----------------------------------------------------------------------------
ALTER TABLE "Tenant" ADD COLUMN IF NOT EXISTS is_pkp BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN "Tenant".is_pkp IS
    'PKP (Pengusaha Kena Pajak) status. When false, VAT_OUTPUT/VAT_INPUT roles return None via resolve_account_id_by_role_if_pkp() — sales/purchase post without VAT line. WHT roles NOT affected. Default true for backward compat.';

COMMIT;
