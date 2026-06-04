-- =============================================================================
-- V150 — Seed CUSTOMER_DEPOSIT_LIABILITY (2-10500 Uang Muka Pelanggan)
-- =============================================================================
-- Purpose (Fase C0):
--   Fase B's seed_default_account_roles() skipped CUSTOMER_DEPOSIT_LIABILITY
--   for anthonius-iwan, ponte-publishing, potus-id because their CoA never
--   contained account_code='2-10500'. Live seed_default_coa() also omitted it.
--
--   This migration:
--     1. Patches seed_default_coa() to include 2-10500 'Uang Muka Pelanggan'
--        so NEW tenants get the account automatically.
--     2. Idempotently INSERTs 2-10500 into existing tenants that lack it.
--     3. Calls seed_default_account_roles() per tenant to backfill mappings.
--     4. (Best-effort) Backfills BANK_OPERATIONAL for tenants where 1-10200 is
--        a header (Law 18 blocked seed) but a leaf bank account already exists
--        via bank_accounts.coa_id. Only maps when exactly ONE candidate leaf
--        exists for that tenant; otherwise leaves unmapped (no silent guess).
--
-- Scope:
--   - NO posting code touched.
--   - Idempotent: safe to re-apply.
--   - Law 18 honored (we never unflag is_header; we ADD leaf or skip).
--   - Law 24 honored (seed_default_account_roles is SECURITY DEFINER-equivalent
--     via direct INSERT; account_roles RLS already enforced by V149 triggers).
--   - Law 27 honored (lookups by tenant_id + account_code, no UUID hardcoding).
--
-- Source of truth: /root/milkyhoop-dev/docs/MAPPING-ROLE-AKUN-LOCKED.md
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Patch seed_default_coa() — add 2-10500 'Uang Muka Pelanggan'
-- -----------------------------------------------------------------------------
-- The live function (production) inserts liabilities 2-10100..2-10400, 2-20000+
-- but omits 2-10500. We CREATE OR REPLACE to add the row. New tenants only
-- (function early-exits when CoA already exists for tenant), so existing
-- tenants are unaffected by this redefinition — backfill handled in step 2.
-- =============================================================================

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
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '1-10000', 'Aset Lancar', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-10100', 'Kas', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10200', 'Bank', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10300', 'Kas Kecil', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10400', 'Piutang Usaha', 'RECEIVABLE', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10500', 'Piutang Lain-lain', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10600', 'Persediaan Barang Dagangan', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-10700', 'Biaya Dibayar Dimuka', 'ASSET', 'DEBIT', '1-10000', 2, false),
        (p_tenant_id, '1-20000', 'Aset Tetap', 'ASSET', 'DEBIT', NULL, 1, true),
        (p_tenant_id, '1-20100', 'Tanah', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20200', 'Bangunan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20300', 'Kendaraan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20400', 'Peralatan', 'ASSET', 'DEBIT', '1-20000', 2, false),
        (p_tenant_id, '1-20900', 'Akumulasi Penyusutan', 'ASSET', 'CREDIT', '1-20000', 2, false);
    v_count := v_count + 14;

    -- LIABILITIES (V150: added 2-10500 'Uang Muka Pelanggan')
    INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header) VALUES
        (p_tenant_id, '2-10000', 'Liabilitas Jangka Pendek', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-10100', 'Hutang Usaha', 'PAYABLE', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10200', 'Hutang Lain-lain', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10300', 'Hutang Pajak', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10400', 'Hutang Gaji', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-10500', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT', '2-10000', 2, false),
        (p_tenant_id, '2-20000', 'Liabilitas Jangka Panjang', 'LIABILITY', 'CREDIT', NULL, 1, true),
        (p_tenant_id, '2-20100', 'Hutang Bank', 'LIABILITY', 'CREDIT', '2-20000', 2, false);
    v_count := v_count + 8;

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
    'Seed default Chart of Accounts for a new tenant. V150: added 2-10500 Uang Muka Pelanggan.';

-- -----------------------------------------------------------------------------
-- 2. Backfill 2-10500 into existing tenants that lack it (idempotent)
-- -----------------------------------------------------------------------------
INSERT INTO chart_of_accounts (
    tenant_id, account_code, name, account_type, normal_balance,
    parent_code, level, is_header, is_active, is_system
)
SELECT
    t.id, '2-10500', 'Uang Muka Pelanggan', 'LIABILITY', 'CREDIT',
    '2-10000', 2, false, true, false
FROM "Tenant" t
WHERE NOT EXISTS (
    SELECT 1 FROM chart_of_accounts c
    WHERE c.tenant_id = t.id AND c.account_code = '2-10500'
)
ON CONFLICT (tenant_id, account_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Backfill CUSTOMER_DEPOSIT_LIABILITY mappings (and any other newly-eligible
--    TIER 1/2 mappings) by re-running seed_default_account_roles per tenant.
--    Idempotent — ON CONFLICT DO NOTHING in V149's seed function.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_tenant RECORD;
    v_n      INTEGER;
BEGIN
    FOR v_tenant IN SELECT id FROM "Tenant" LOOP
        v_n := seed_default_account_roles(v_tenant.id);
        RAISE NOTICE 'V150 backfill tenant=%: % new role mappings', v_tenant.id, v_n;
    END LOOP;
END $$;

-- -----------------------------------------------------------------------------
-- 4. BANK_OPERATIONAL best-effort backfill (Law 18-safe)
-- -----------------------------------------------------------------------------
-- For tenants where the BANK_OPERATIONAL mapping is still missing AND 1-10200
-- is a header (correctly blocked by V149), try to map to the tenant's bank
-- leaf account ONLY when exactly one leaf candidate exists via bank_accounts.
-- Owner directive: role = default fallback, do NOT force-pick when ambiguous.
-- =============================================================================
INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
SELECT
    t.id,
    'BANK_OPERATIONAL',
    bank.coa_id,
    false,
    'V150 backfill: auto-mapped to sole leaf bank account (1-10200 was header).'
FROM "Tenant" t
JOIN LATERAL (
    SELECT ba.coa_id
      FROM bank_accounts ba
      JOIN chart_of_accounts ca ON ca.id = ba.coa_id
     WHERE ba.tenant_id = t.id
       AND ca.is_header = false
       AND ca.is_active = true
     GROUP BY ba.coa_id
) AS _leafs ON true
JOIN LATERAL (
    SELECT ba.coa_id, COUNT(*) OVER () AS cnt
      FROM bank_accounts ba
      JOIN chart_of_accounts ca ON ca.id = ba.coa_id
     WHERE ba.tenant_id = t.id
       AND ca.is_header = false
       AND ca.is_active = true
     LIMIT 1
) AS bank ON true
WHERE NOT EXISTS (
    SELECT 1 FROM account_roles ar
    WHERE ar.tenant_id = t.id AND ar.role_key = 'BANK_OPERATIONAL'
)
AND (
    SELECT COUNT(DISTINCT ba.coa_id)
      FROM bank_accounts ba
      JOIN chart_of_accounts ca ON ca.id = ba.coa_id
     WHERE ba.tenant_id = t.id
       AND ca.is_header = false
       AND ca.is_active = true
) = 1
ON CONFLICT (tenant_id, role_key) DO NOTHING;

COMMIT;
