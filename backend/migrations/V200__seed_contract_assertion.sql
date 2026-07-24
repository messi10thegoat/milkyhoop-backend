-- ============================================================================
-- V200__seed_contract_assertion.sql
--
-- MENUTUP KELAS BUG, bukan satu instance.
--
-- Riwayat: V165 menambahkan CoA 5-20850 + role BANK_FEE ke fungsi seed.
-- V168, V173, dan V183 lalu mendefinisikan ULANG seed_default_coa() dengan
-- menyalin versi LAMA + menambah bagiannya sendiri, sehingga menghapus
-- kontribusi V165 secara diam-diam. Tidak ada yang gagal, tidak ada yang
-- memberi peringatan. Ketahuan hanya karena transfer bank kebetulan memakai
-- BANK_FEE saat E2E. Kontribusi lain bisa saja sudah hilang tanpa terdeteksi.
--
-- Penutupnya: KONTRAK SEED yang fail-loud. Setiap redefinisi berikutnya yang
-- menjatuhkan akun/role wajib akan MELEDAK saat migrasi, bukan diam-diam
-- lolos sampai produksi.
--
-- assert_seed_contract() punya dua lapis:
--   Lapis 1 (selalu): sumber seed_default_coa() dan seed_default_account_roles()
--                     WAJIB memuat setiap akun/role wajib. Menangkap clobber
--                     pada saat migrasi, tanpa perlu tenant apa pun.
--   Lapis 2 (opsional, kalau p_tenant_id diisi): tenant tsb WAJIB benar-benar
--                     punya baris-baris itu. Menangkap seed yang gagal separuh.
--
-- Pemakaian: dipanggil di akhir migrasi ini, dan oleh gap_patch.sh (Gap 12)
-- setelah fresh install.
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.assert_seed_contract(p_tenant_id TEXT DEFAULT NULL)
RETURNS TEXT
LANGUAGE plpgsql
AS $fn$
DECLARE
    -- Role minimum. Menambah baris di sini = memperkuat kontrak.
    v_roles TEXT[] := ARRAY[
        'AR_TRADE','AP_TRADE','AP_PREPAID',
        'CASH_GENERAL','CASH_PETTY','BANK_OPERATIONAL','BANK_FEE',
        'INVENTORY_MERCHANDISE','WIP_GENERIC',
        'COGS_SALES','COGS_SERVICE',
        'REVENUE_SALES_GOODS','REVENUE_DEFERRED',
        'VAT_INPUT','VAT_OUTPUT','WHT_PPH_PAYABLE',
        'SALARY_EXPENSE','SALARY_PAYABLE','PPH21_PAYABLE',
        'MFG_DIRECT_LABOR','MFG_ACTUAL_OVERHEAD',
        'MFG_LABOR_APPLIED','MFG_OVERHEAD_APPLIED',
        'CUSTOMER_DEPOSIT_LIABILITY','EQUITY_OPENING_BALANCE'
    ];
    -- Akun kritis yang WAJIB dihasilkan seed_default_coa().
    v_accounts TEXT[] := ARRAY[
        '1-10100','1-10200','1-10400','1-10600','1-10650','1-10800',
        '2-10100','2-10300','2-10430','2-10440','2-10500','2-10600','2-10750',
        '3-50000','5-10100','5-10110','5-20850'
    ];
    v_coa_def   TEXT;
    v_roles_def TEXT;
    v_missing   TEXT := '';
    k           TEXT;
BEGIN
    SELECT pg_get_functiondef(oid) INTO v_coa_def
      FROM pg_proc WHERE proname = 'seed_default_coa';
    SELECT pg_get_functiondef(oid) INTO v_roles_def
      FROM pg_proc WHERE proname = 'seed_default_account_roles';

    IF v_coa_def IS NULL THEN
        RAISE EXCEPTION 'KONTRAK SEED: fungsi seed_default_coa tidak ditemukan';
    END IF;
    IF v_roles_def IS NULL THEN
        RAISE EXCEPTION 'KONTRAK SEED: fungsi seed_default_account_roles tidak ditemukan';
    END IF;

    -- Lapis 1: sumber fungsi
    FOREACH k IN ARRAY v_accounts LOOP
        IF position(k IN v_coa_def) = 0 THEN
            v_missing := v_missing || ' coa:' || k;
        END IF;
    END LOOP;
    FOREACH k IN ARRAY v_roles LOOP
        IF position(k IN v_roles_def) = 0 THEN
            v_missing := v_missing || ' role:' || k;
        END IF;
    END LOOP;

    IF v_missing <> '' THEN
        RAISE EXCEPTION 'KONTRAK SEED DILANGGAR (sumber fungsi). Hilang:%'
                        '  -- Kemungkinan besar sebuah migrasi mendefinisikan ulang'
                        ' fungsi seed dengan menyalin versi LAMA. Bangun ulang dari'
                        ' pg_get_functiondef, jangan dari file migrasi lama.', v_missing;
    END IF;

    -- Lapis 2: realisasi per tenant
    IF p_tenant_id IS NOT NULL THEN
        FOREACH k IN ARRAY v_accounts LOOP
            IF NOT EXISTS (SELECT 1 FROM chart_of_accounts
                            WHERE tenant_id = p_tenant_id AND account_code = k) THEN
                v_missing := v_missing || ' coa:' || k;
            END IF;
        END LOOP;
        FOREACH k IN ARRAY v_roles LOOP
            IF NOT EXISTS (SELECT 1 FROM account_roles
                            WHERE tenant_id = p_tenant_id AND role_key = k) THEN
                v_missing := v_missing || ' role:' || k;
            END IF;
        END LOOP;

        IF v_missing <> '' THEN
            RAISE EXCEPTION 'KONTRAK SEED DILANGGAR untuk tenant %. Hilang:%',
                            p_tenant_id, v_missing;
        END IF;
    END IF;

    RETURN 'KONTRAK SEED OK ('
           || array_length(v_accounts,1) || ' akun, '
           || array_length(v_roles,1) || ' role'
           || COALESCE(', tenant ' || p_tenant_id, ', sumber-fungsi saja') || ')';
END;
$fn$;

COMMENT ON FUNCTION public.assert_seed_contract(TEXT) IS
    'V200: kontrak fail-loud agar redefinisi seed_default_coa/seed_default_account_roles tidak bisa menghapus akun/role wajib secara diam-diam.';

-- Validasi segera (lapis 1).
DO $v200$
DECLARE r TEXT;
BEGIN
    SELECT assert_seed_contract() INTO r;
    RAISE NOTICE '%', r;
END $v200$;

COMMIT;
