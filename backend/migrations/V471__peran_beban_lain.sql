-- V471 (2 Nov 2026, CONVERSATIONAL; nomor dipesan MASTER): peran EXPENSE_OTHER = akun beban bawaan "lain-lain" untuk catat biaya
-- Milkybook v2 (app/milkybook/v2/biaya.py: biaya serupa -> EXPENSE_OTHER -> kosong). Putusan pemilik LANGSUNG 2 Nov "Belajar dari
-- riwayat": belum pernah ada biaya serupa -> masuk Beban Lain-lain. MASTER: generik lewat peran, nol nama akun di kode.
-- Akun bawaan = 5-20900 (kode semai bawaan, sudah dipakai BANK_ADJUSTMENT_LOSS sejak V444 di SEMUA tenant) -- TIDAK ada akun baru.
-- Pola V444/V452 (JALANKAN dengan psql -v ON_ERROR_STOP=1 -1 lewat apply_mig.sh; tanpa BEGIN/COMMIT di berkas):
--   1. CHECK account_roles.role_key += EXPENSE_OTHER (dibangun dari definisi SEKARANG; nilai lama tak terbuang);
--   2. seed_default_account_roles ditambal lewat ANCHOR baris BANK_ADJUSTMENT_LOSS (gagal-keras bila anchor hilang; lewati bila ada);
--   3. backfill: seed_default_account_roles(id) untuk SEMUA tenant (fungsi melewati tenant tanpa akun / akun header -- tak membuat
--      akun); baris baru WAJIB == celah terukur (tenant ber-5-20900 EXPENSE bukan header yang belum punya EXPENSE_OTHER);
--      MASTER: tenant tanpa 5-20900 bukan header DILEWATI + DILAPORKAN (NOTICE), TIDAK dibuatkan akun. Diukur 2 Nov: 6/6 punya;
--   4. penjaga akhir: tiap tenant ber-akun itu EXPENSE_OTHER -> 5-20900 EXPENSE bukan header; chart_of_accounts SEMUA tenant md5 SAMA;
--      account_roles berubah HANYA baris EXPENSE_OTHER; jumlah jurnal SAMA.
-- Nol jurnal, nol akun disentuh. Pembalik: V471__peran_beban_lain_ROLLBACK.sql.

CREATE TEMP TABLE v471_sebelum AS
SELECT (SELECT md5(string_agg(tenant_id || ':' || id::text || ':' || account_code || ':' || name || ':' || account_type || ':'
                              || is_header::text, '|' ORDER BY tenant_id, account_code, id))
          FROM chart_of_accounts) AS coa_semua,
       (SELECT md5(string_agg(tenant_id || ':' || role_key || ':' || account_id::text || ':' || coalesce(notes, ''), '|'
                              ORDER BY tenant_id, role_key))
          FROM account_roles WHERE role_key <> 'EXPENSE_OTHER') AS peran_lain,
       (SELECT count(*) FROM "Tenant" t
          WHERE NOT EXISTS (SELECT 1 FROM account_roles a WHERE a.tenant_id = t.id AND a.role_key = 'EXPENSE_OTHER')
            AND EXISTS (SELECT 1 FROM chart_of_accounts c WHERE c.tenant_id = t.id AND c.account_code = '5-20900'
                          AND c.account_type = 'EXPENSE' AND NOT c.is_header)) AS celah,
       (SELECT md5(string_agg(tenant_id || ':' || account_id::text || ':' || coalesce(notes, ''), '|' ORDER BY tenant_id))
          FROM account_roles WHERE role_key = 'BANK_ADJUSTMENT_LOSS') AS peran_bal,
       (SELECT count(*) FROM journal_entries) AS n_jurnal;

-- 0. laporan: tenant tanpa 5-20900 EXPENSE bukan header DILEWATI (tak dibuatkan akun; pengguna memilih akun di kartu)
DO $$
DECLARE t record;
BEGIN
    FOR t IN SELECT id FROM "Tenant" x
             WHERE NOT EXISTS (SELECT 1 FROM chart_of_accounts c WHERE c.tenant_id = x.id AND c.account_code = '5-20900'
                                 AND c.account_type = 'EXPENSE' AND NOT c.is_header) ORDER BY id LOOP
        RAISE NOTICE 'V471: tenant % DILEWATI -- tanpa 5-20900 EXPENSE bukan header', t.id;
    END LOOP;
    -- kode sama berjenis lain = 5-20900 dipakai akun berbeda: jangan petakan beban ke sana (fungsi semai memetakan berdasar kode)
    IF EXISTS (SELECT 1 FROM chart_of_accounts WHERE account_code = '5-20900' AND (account_type <> 'EXPENSE' OR is_header)) THEN
        RAISE EXCEPTION 'V471: 5-20900 dipakai akun berjenis lain / header -- hentikan';
    END IF;
END $$;

-- 1. CHECK role_key
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint WHERE conname = 'account_roles_role_key_check';
    IF v_def IS NULL THEN RAISE EXCEPTION 'V471: account_roles_role_key_check tidak ditemukan'; END IF;
    IF v_def NOT LIKE '%ARRAY[%' THEN RAISE EXCEPTION 'V471: bentuk CHECK tak dikenal: %', left(v_def, 120); END IF;
    IF v_def NOT LIKE '%''EXPENSE_OTHER''%' THEN
        v_baru := replace(v_def, 'ARRAY[', 'ARRAY[''EXPENSE_OTHER''::text, ');
        EXECUTE 'ALTER TABLE account_roles DROP CONSTRAINT account_roles_role_key_check';
        EXECUTE 'ALTER TABLE account_roles ADD CONSTRAINT account_roles_role_key_check ' || v_baru;
    END IF;
    v_def := (SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'account_roles_role_key_check');
    IF v_def NOT LIKE '%''EXPENSE_OTHER''%' OR v_def NOT LIKE '%''BANK_ADJUSTMENT_LOSS''%' OR v_def NOT LIKE '%''AP_TRADE''%'
       OR v_def NOT LIKE '%''GOODS_RECEIVED_NOT_INVOICED''%' THEN
        RAISE EXCEPTION 'V471: CHECK role_key tidak memuat nilai lama + baru';
    END IF;
END $$;

-- 2. semai tenant BARU (anchor; gagal-keras bila anchor hilang; lewati bila sudah ada)
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def NOT LIKE '%EXPENSE_OTHER%' THEN
        v_baru := replace(v_def, E'(''BANK_ADJUSTMENT_LOSS'',       ''5-20900'', false, ''V444 Selisih kurang penyesuaian saldo bank.''),',
                          E'(''BANK_ADJUSTMENT_LOSS'',       ''5-20900'', false, ''V444 Selisih kurang penyesuaian saldo bank.''),\n'
                          '            (''EXPENSE_OTHER'',              ''5-20900'', false, ''V471 Beban lain-lain bawaan catat biaya.''),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V471: anchor BANK_ADJUSTMENT_LOSS tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
    END IF;
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def NOT LIKE '%(''EXPENSE_OTHER'',              ''5-20900'', false,%'
       OR v_def NOT LIKE '%(''BANK_ADJUSTMENT_LOSS'',       ''5-20900'', false,%' OR v_def NOT LIKE '%(''AP_TRADE'',%' THEN
        RAISE EXCEPTION 'V471: seed_default_account_roles tidak memuat baris lama + baru';
    END IF;
END $$;

-- 3. backfill tenant lama (hanya celah; ON CONFLICT DO NOTHING di dalam fungsi)
DO $$
DECLARE t record; n_tambah int := 0; n int;
BEGIN
    FOR t IN SELECT id FROM "Tenant" ORDER BY id LOOP
        SELECT seed_default_account_roles(t.id) INTO n;
        n_tambah := n_tambah + n;
    END LOOP;
    IF n_tambah <> (SELECT celah FROM v471_sebelum) THEN
        RAISE EXCEPTION 'V471: baris peran baru % <> celah EXPENSE_OTHER % (peran LAIN ikut terisi -- hentikan)', n_tambah,
            (SELECT celah FROM v471_sebelum);
    END IF;
END $$;

-- 4. penjaga akhir
DO $$
DECLARE n int; s record;
BEGIN
    SELECT * INTO s FROM v471_sebelum;
    SELECT count(*) INTO n FROM "Tenant" t
    WHERE EXISTS (SELECT 1 FROM chart_of_accounts c0 WHERE c0.tenant_id = t.id AND c0.account_code = '5-20900'
                    AND c0.account_type = 'EXPENSE' AND NOT c0.is_header)
      AND NOT EXISTS (SELECT 1 FROM account_roles a JOIN chart_of_accounts c ON c.id = a.account_id AND c.tenant_id = a.tenant_id
                      WHERE a.tenant_id = t.id AND a.role_key = 'EXPENSE_OTHER' AND c.account_type = 'EXPENSE' AND NOT c.is_header
                        AND (COALESCE(a.notes, '') NOT LIKE 'V471%' OR c.account_code = '5-20900'));
    IF n > 0 THEN RAISE EXCEPTION 'V471: % tenant ber-5-20900 tanpa EXPENSE_OTHER yang sah', n; END IF;
    IF s.coa_semua IS DISTINCT FROM (SELECT md5(string_agg(tenant_id || ':' || id::text || ':' || account_code || ':' || name || ':'
                                                           || account_type || ':' || is_header::text, '|' ORDER BY tenant_id, account_code, id))
                                       FROM chart_of_accounts) THEN
        RAISE EXCEPTION 'V471: chart_of_accounts BERUBAH';
    END IF;
    IF s.peran_lain IS DISTINCT FROM (SELECT md5(string_agg(tenant_id || ':' || role_key || ':' || account_id::text || ':'
                                                            || coalesce(notes, ''), '|' ORDER BY tenant_id, role_key))
                                        FROM account_roles WHERE role_key <> 'EXPENSE_OTHER') THEN
        RAISE EXCEPTION 'V471: account_roles selain EXPENSE_OTHER BERUBAH';
    END IF;
    IF s.peran_bal IS DISTINCT FROM (SELECT md5(string_agg(tenant_id || ':' || account_id::text || ':' || coalesce(notes, ''), '|'
                                                           ORDER BY tenant_id))
                                       FROM account_roles WHERE role_key = 'BANK_ADJUSTMENT_LOSS') THEN
        RAISE EXCEPTION 'V471: pemetaan BANK_ADJUSTMENT_LOSS BERUBAH';
    END IF;
    IF (SELECT count(*) FROM journal_entries) <> s.n_jurnal THEN RAISE EXCEPTION 'V471: jumlah jurnal berubah'; END IF;
END $$;

DROP TABLE v471_sebelum;
