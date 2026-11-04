-- V479 (5 Nov 2026, BACKEND; nomor dipesan MASTER): PEMBULATAN dokumen PEMBELIAN (c-2). Putusan pemilik LANGSUNG 5 Nov (pola Accurate):
-- baris tagihan / nota debit tetap bersen, TOTAL dibulatkan ke rupiah penuh SAAT POSTING, selisih dijurnal ke akun khusus
-- "Pembulatan Pembelian" (peran PURCHASE_ROUNDING, OTHER_EXPENSE, kode 5-90100 di bawah 5-90000). Diukur 5 Nov: 7/7 tenant punya
-- 5-90000 OTHER_EXPENSE bukan header, nol tenant memakai 5-901xx, nol peran ROUND.
-- Pola V452 (JALANKAN dengan psql -v ON_ERROR_STOP=1 -1 lewat apply_mig.sh; tanpa BEGIN/COMMIT di berkas):
--   1. kolom bills.pembulatan + vendor_credits.pembulatan NUMERIC(18,2) NOT NULL DEFAULT 0 (dokumen lama = 0 = tanpa pembulatan);
--      trg_law19_bekukan_nominal += pembulatan (beku sesudah posting seperti grand_total);
--   2. CHECK account_roles.role_key += PURCHASE_ROUNDING;
--   3. akun 5-90100 per tenant (atribut disalin dari 5-90200 sejenis) + peran; tenant tanpa 5-90000 DILEWATI + NOTICE;
--   4. semai tenant BARU: seed_default_coa (anchor 5-90200, v_count +1) + seed_default_account_roles (anchor EXPENSE_OTHER);
--   5. penjaga akhir: akun LAIN tak berubah, peran LAIN tak berubah, jumlah jurnal SAMA, dokumen lama pembulatan 0.
-- Nol jurnal, nol data dokumen diubah. Pembalik: V479__pembulatan_pembelian_ROLLBACK.sql.

CREATE TEMP TABLE v479_sebelum AS
SELECT (SELECT md5(string_agg(tenant_id || ':' || id::text || ':' || account_code || ':' || name || ':' || account_type || ':'
                              || is_header::text, '|' ORDER BY tenant_id, account_code, id))
          FROM chart_of_accounts WHERE account_code <> '5-90100') AS coa_lain,
       (SELECT md5(string_agg(tenant_id || ':' || role_key || ':' || account_id::text || ':' || coalesce(notes, ''), '|'
                              ORDER BY tenant_id, role_key))
          FROM account_roles WHERE role_key <> 'PURCHASE_ROUNDING') AS peran_lain,
       (SELECT count(*) FROM journal_entries) AS n_jurnal;

-- 0. penjaga tabrakan kode + laporan tenant yang dilewati
DO $$
DECLARE t record;
BEGIN
    IF EXISTS (SELECT 1 FROM chart_of_accounts WHERE account_code = '5-90100' AND name <> 'Pembulatan Pembelian') THEN
        RAISE EXCEPTION 'V479: kode 5-90100 dipakai akun lain -- hentikan';
    END IF;
    FOR t IN SELECT id FROM "Tenant" x
             WHERE NOT EXISTS (SELECT 1 FROM chart_of_accounts c WHERE c.tenant_id = x.id AND c.account_code = '5-90000'
                                 AND c.account_type = 'OTHER_EXPENSE' AND NOT c.is_header) ORDER BY id LOOP
        RAISE NOTICE 'V479: tenant % DILEWATI -- tanpa 5-90000 OTHER_EXPENSE bukan header', t.id;
    END LOOP;
END $$;

-- 1. kolom + beku Law 19
ALTER TABLE bills ADD COLUMN IF NOT EXISTS pembulatan NUMERIC(18,2) NOT NULL DEFAULT 0;
ALTER TABLE vendor_credits ADD COLUMN IF NOT EXISTS pembulatan NUMERIC(18,2) NOT NULL DEFAULT 0;
DO $$
DECLARE v_def text;
BEGIN
    SELECT pg_get_triggerdef(t.oid) INTO v_def FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE c.relname = 'bills' AND t.tgname = 'trg_law19_bekukan_nominal';
    IF v_def IS NULL THEN RAISE EXCEPTION 'V479: trg_law19_bekukan_nominal tidak ditemukan'; END IF;
    IF v_def NOT LIKE '%''pembulatan''%' THEN
        IF v_def NOT LIKE '%''grand_total''%' THEN RAISE EXCEPTION 'V479: bentuk trigger beku tak dikenal'; END IF;
        EXECUTE 'DROP TRIGGER trg_law19_bekukan_nominal ON bills';
        EXECUTE replace(v_def, '''grand_total''', '''grand_total'', ''pembulatan''');
    END IF;
    SELECT pg_get_triggerdef(t.oid) INTO v_def FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE c.relname = 'bills' AND t.tgname = 'trg_law19_bekukan_nominal';
    IF v_def IS NULL OR v_def NOT LIKE '%''pembulatan''%' OR v_def NOT LIKE '%''grand_total''%' OR v_def NOT LIKE '%''pph_dpp''%'
       OR v_def NOT LIKE '%old.journal_id IS NOT NULL%' THEN
        RAISE EXCEPTION 'V479: trigger beku tidak memuat kolom lama + pembulatan';
    END IF;
END $$;

-- 2. CHECK role_key
DO $$
DECLARE v_def text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint WHERE conname = 'account_roles_role_key_check';
    IF v_def IS NULL THEN RAISE EXCEPTION 'V479: account_roles_role_key_check tidak ditemukan'; END IF;
    IF v_def NOT LIKE '%ARRAY[%' THEN RAISE EXCEPTION 'V479: bentuk CHECK tak dikenal: %', left(v_def, 120); END IF;
    IF v_def NOT LIKE '%''PURCHASE_ROUNDING''%' THEN
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check '
                || replace(v_def, 'ARRAY[', 'ARRAY[''PURCHASE_ROUNDING''::text, ');
    END IF;
    v_def := (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'account_roles_role_key_check');
    IF v_def NOT LIKE '%''PURCHASE_ROUNDING''%' OR v_def NOT LIKE '%''EXPENSE_OTHER''%' OR v_def NOT LIKE '%''AP_TRADE''%' THEN
        RAISE EXCEPTION 'V479: CHECK role_key tidak memuat nilai lama + baru';
    END IF;
END $$;

-- 3. akun + peran tenant lama (atribut dari 5-90200 sejenis; induk 5-90000)
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header,
                               is_detail, is_system, is_cash, is_bank_account, cash_flow_category, psak_sub_category, category, is_active)
SELECT p.tenant_id, '5-90100', 'Pembulatan Pembelian', 'OTHER_EXPENSE', 'DEBIT', '5-90000', p.level + 1, false,
       COALESCE(s.is_detail, false), false, false, false, COALESCE(s.cash_flow_category, 'NONE'), s.psak_sub_category, s.category, true
FROM chart_of_accounts p
LEFT JOIN chart_of_accounts s ON s.tenant_id = p.tenant_id AND s.account_code = '5-90200'
WHERE p.account_code = '5-90000' AND p.account_type = 'OTHER_EXPENSE' AND NOT p.is_header
  AND NOT EXISTS (SELECT 1 FROM chart_of_accounts x WHERE x.tenant_id = p.tenant_id AND x.account_code = '5-90100');

INSERT INTO account_roles (tenant_id, role_key, account_id, is_interim, notes)
SELECT a.tenant_id, 'PURCHASE_ROUNDING', a.id, false, 'V479 Pembulatan total dokumen pembelian ke rupiah penuh.'
FROM chart_of_accounts a WHERE a.account_code = '5-90100' AND a.name = 'Pembulatan Pembelian'
ON CONFLICT (tenant_id, role_key) DO NOTHING;

-- 4. semai tenant BARU (anchor gagal-keras; lewati bila sudah ada)
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('seed_default_coa(text)'::regprocedure);
    IF v_def NOT LIKE '%5-90100%' THEN
        v_baru := replace(v_def, E'(p_tenant_id, ''5-90200'', ''Selisih Produksi'', ''OTHER_EXPENSE'', ''DEBIT'', ''5-90000'', 3, false, false);',
                          E'(p_tenant_id, ''5-90100'', ''Pembulatan Pembelian'', ''OTHER_EXPENSE'', ''DEBIT'', ''5-90000'', 2, false, false),\n'
                          '        (p_tenant_id, ''5-90200'', ''Selisih Produksi'', ''OTHER_EXPENSE'', ''DEBIT'', ''5-90000'', 3, false, false);');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V479: anchor 5-90200 tak ditemukan di seed_default_coa'; END IF;
        IF (length(v_baru) - length(replace(v_baru, 'v_count := v_count + 26;', ''))) / length('v_count := v_count + 26;') <> 1 THEN
            RAISE EXCEPTION 'V479: penghitung blok 5-xxxx (v_count + 26) tak tunggal';
        END IF;
        v_baru := replace(v_baru, 'v_count := v_count + 26;', 'v_count := v_count + 27;');
        EXECUTE v_baru;
    END IF;
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def NOT LIKE '%PURCHASE_ROUNDING%' THEN
        v_baru := replace(v_def, E'(''EXPENSE_OTHER'',              ''5-20900'', false, ''V471 Beban lain-lain bawaan catat biaya.''),',
                          E'(''EXPENSE_OTHER'',              ''5-20900'', false, ''V471 Beban lain-lain bawaan catat biaya.''),\n'
                          '            (''PURCHASE_ROUNDING'',          ''5-90100'', false, ''V479 Pembulatan total dokumen pembelian ke rupiah penuh.''),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V479: anchor EXPENSE_OTHER tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
    END IF;
    IF pg_get_functiondef('seed_default_coa(text)'::regprocedure) NOT LIKE '%''5-90100'', ''Pembulatan Pembelian''%'
       OR pg_get_functiondef('seed_default_coa(text)'::regprocedure) NOT LIKE '%v_count := v_count + 27;%'
       OR pg_get_functiondef('seed_default_coa(text)'::regprocedure) NOT LIKE '%''5-90200'', ''Selisih Produksi''%'
       OR pg_get_functiondef('seed_default_account_roles(text)'::regprocedure) NOT LIKE '%(''PURCHASE_ROUNDING'',          ''5-90100''%'
       OR pg_get_functiondef('seed_default_account_roles(text)'::regprocedure) NOT LIKE '%(''EXPENSE_OTHER'',%' THEN
        RAISE EXCEPTION 'V479: fungsi semai tidak memuat baris lama + baru';
    END IF;
END $$;

-- 5. penjaga akhir
DO $$
DECLARE n int; s record;
BEGIN
    SELECT * INTO s FROM v479_sebelum;
    SELECT count(*) INTO n FROM "Tenant" t
    WHERE EXISTS (SELECT 1 FROM chart_of_accounts c0 WHERE c0.tenant_id = t.id AND c0.account_code = '5-90000'
                    AND c0.account_type = 'OTHER_EXPENSE' AND NOT c0.is_header)
      AND NOT EXISTS (SELECT 1 FROM account_roles a JOIN chart_of_accounts c ON c.id = a.account_id AND c.tenant_id = a.tenant_id
                      WHERE a.tenant_id = t.id AND a.role_key = 'PURCHASE_ROUNDING' AND c.account_code = '5-90100'
                        AND c.account_type = 'OTHER_EXPENSE' AND NOT c.is_header);
    IF n > 0 THEN RAISE EXCEPTION 'V479: % tenant ber-5-90000 tanpa PURCHASE_ROUNDING -> 5-90100', n; END IF;
    IF s.coa_lain IS DISTINCT FROM (SELECT md5(string_agg(tenant_id || ':' || id::text || ':' || account_code || ':' || name || ':'
                                                          || account_type || ':' || is_header::text, '|' ORDER BY tenant_id, account_code, id))
                                      FROM chart_of_accounts WHERE account_code <> '5-90100') THEN
        RAISE EXCEPTION 'V479: chart_of_accounts selain 5-90100 BERUBAH';
    END IF;
    IF s.peran_lain IS DISTINCT FROM (SELECT md5(string_agg(tenant_id || ':' || role_key || ':' || account_id::text || ':'
                                                            || coalesce(notes, ''), '|' ORDER BY tenant_id, role_key))
                                        FROM account_roles WHERE role_key <> 'PURCHASE_ROUNDING') THEN
        RAISE EXCEPTION 'V479: account_roles selain PURCHASE_ROUNDING BERUBAH';
    END IF;
    IF (SELECT count(*) FROM journal_entries) <> s.n_jurnal THEN RAISE EXCEPTION 'V479: jumlah jurnal berubah'; END IF;
    IF EXISTS (SELECT 1 FROM bills WHERE pembulatan <> 0) OR EXISTS (SELECT 1 FROM vendor_credits WHERE pembulatan <> 0) THEN
        RAISE EXCEPTION 'V479: dokumen lama ber-pembulatan <> 0';
    END IF;
END $$;

DROP TABLE v479_sebelum;
