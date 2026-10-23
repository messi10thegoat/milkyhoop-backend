-- V444 (23 Okt 2026, BACKEND; desain disetujui MASTER, KONTRAK-v444-peran-semai-20261023.md): peran akun yang di-resolve kode tetapi TIDAK
-- disemai seed_default_account_roles (onboarding tenant BARU): EMPLOYEE_ADVANCE (V281), BANK_ADJUSTMENT_GAIN/LOSS (V274), REVENUE_SALES_SERVICE
-- (V260) -- dulu hanya diisi per tenant lama -> setiap tenant baru lahir tanpa keempatnya (kasbon/penyesuaian saldo bank 400/422).
-- Akun bawaan = pilihan pemilik untuk grapgrap (4-90000 / 5-20900 / 4-10150 / 1-10450), dijadikan bawaan generik (pola NetSuite/QBO).
-- 1) akun 1-10450 Piutang Karyawan (Kasbon) untuk tenant yang belum punya (atribut disalin dari 1-10500 tenant itu; description 'V444');
-- 2) seed_default_coa + seed_default_account_roles ditambal lewat ANCHOR (pola V452; gagal-keras bila anchor hilang; dilewati bila sudah ada)
--    -> urutan migrasi tak berpengaruh;
-- 3) backfill: seed_default_account_roles(id) untuk SEMUA tenant (ON CONFLICT DO NOTHING) -- diukur 23 Okt: celah hanya keempat peran ini;
-- 4) penjaga gagal-keras: setiap tenant punya 4 peran dgn kode yang diharapkan & bukan header; jumlah baris baru == celah terukur;
--    grapgrap-manado (sudah lengkap) md5 account_roles + chart_of_accounts SAMA sebelum/sesudah.
-- Nol jurnal disentuh.

CREATE TEMP TABLE v444_sebelum AS
SELECT (SELECT md5(string_agg(role_key || ':' || account_id::text || ':' || coalesce(notes, ''), '|' ORDER BY role_key))
          FROM account_roles WHERE tenant_id = 'grapgrap-manado') AS peran_gg,
       (SELECT md5(string_agg(id::text || ':' || account_code || ':' || name || ':' || coalesce(description, ''), '|' ORDER BY account_code, id))
          FROM chart_of_accounts WHERE tenant_id = 'grapgrap-manado') AS coa_gg,
       (SELECT count(*) FROM "Tenant" t CROSS JOIN (VALUES ('EMPLOYEE_ADVANCE'), ('BANK_ADJUSTMENT_GAIN'), ('BANK_ADJUSTMENT_LOSS'),
                                                        ('REVENUE_SALES_SERVICE')) p(r)
          WHERE NOT EXISTS (SELECT 1 FROM account_roles a WHERE a.tenant_id = t.id AND a.role_key = p.r)) AS celah,
       (SELECT count(*) FROM account_roles) AS n_peran,
       (SELECT count(*) FROM journal_entries) AS n_jurnal;

-- 0. penjaga kode akun: bila ada, harus akun yang sama jenisnya (tak menimpa akun lain berkode sama)
DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM chart_of_accounts
    WHERE (account_code = '1-10450' AND (account_type <> 'ASSET' OR is_header))
       OR (account_code = '4-90000' AND (account_type NOT IN ('OTHER_INCOME', 'REVENUE') OR is_header))
       OR (account_code = '5-20900' AND (account_type <> 'EXPENSE' OR is_header))
       OR (account_code = '4-10150' AND (account_type <> 'REVENUE' OR is_header));
    IF n > 0 THEN RAISE EXCEPTION 'V444: kode 1-10450/4-90000/5-20900/4-10150 dipakai akun berjenis lain / header (% baris)', n; END IF;
END $$;

-- 1. akun 1-10450 per tenant yang belum punya
INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header,
                               is_detail, is_system, is_cash, is_bank_account, cash_flow_category, psak_sub_category, category, is_active,
                               description)
SELECT c.tenant_id, '1-10450', 'Piutang Karyawan (Kasbon)', 'ASSET', 'DEBIT', c.parent_code, c.level, false,
       c.is_detail, false, false, false, c.cash_flow_category, c.psak_sub_category, c.category, true,
       'V444 Piutang Karyawan (Kasbon) -- bawaan generik (pola V281 grapgrap).'
FROM chart_of_accounts c
WHERE c.account_code = '1-10500'
  AND NOT EXISTS (SELECT 1 FROM chart_of_accounts x WHERE x.tenant_id = c.tenant_id AND x.account_code = '1-10450');

-- 2. semai tenant BARU (anchor; gagal-keras bila anchor hilang; lewati bila sudah ada)
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('seed_default_coa(text)'::regprocedure);
    IF v_def NOT LIKE '%1-10450%' THEN
        v_baru := replace(v_def, E'(p_tenant_id, ''1-10400'', ''Piutang Usaha'', ''RECEIVABLE'', ''DEBIT'', ''1-10000'', 2, false),',
                          E'(p_tenant_id, ''1-10400'', ''Piutang Usaha'', ''RECEIVABLE'', ''DEBIT'', ''1-10000'', 2, false),\n        (p_tenant_id, ''1-10450'', ''Piutang Karyawan (Kasbon)'', ''ASSET'', ''DEBIT'', ''1-10000'', 2, false),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V444: anchor 1-10400 tak ditemukan di seed_default_coa'; END IF;
        IF (length(v_baru) - length(replace(v_baru, 'v_count := v_count + 19;', ''))) / length('v_count := v_count + 19;') <> 1 THEN
            RAISE EXCEPTION 'V444: anchor hitungan blok aset (+19) tidak tunggal';
        END IF;
        v_baru := replace(v_baru, 'v_count := v_count + 19;', 'v_count := v_count + 20;');
        EXECUTE v_baru;
    END IF;
    v_def := pg_get_functiondef('seed_default_account_roles(text)'::regprocedure);
    IF v_def NOT LIKE '%BANK_ADJUSTMENT_GAIN%' THEN
        v_baru := replace(v_def, E'(''AR_TRADE'',                   ''1-10400'', false, NULL),',
                          E'(''AR_TRADE'',                   ''1-10400'', false, NULL),\n'
                          '            (''EMPLOYEE_ADVANCE'',           ''1-10450'', false, ''V444 Piutang Karyawan (Kasbon).''),\n'
                          '            (''BANK_ADJUSTMENT_GAIN'',       ''4-90000'', false, ''V444 Selisih lebih penyesuaian saldo bank.''),\n'
                          '            (''BANK_ADJUSTMENT_LOSS'',       ''5-20900'', false, ''V444 Selisih kurang penyesuaian saldo bank.''),\n'
                          '            (''REVENUE_SALES_SERVICE'',      ''4-10150'', false, ''V444 Pendapatan Jasa.''),');
        IF v_baru = v_def THEN RAISE EXCEPTION 'V444: anchor AR_TRADE tak ditemukan di seed_default_account_roles'; END IF;
        EXECUTE v_baru;
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
    IF n_tambah <> (SELECT celah FROM v444_sebelum) THEN
        RAISE EXCEPTION 'V444: baris peran baru % <> celah terukur % (ada peran LAIN yang ikut terisi -- hentikan)', n_tambah, (SELECT celah FROM v444_sebelum);
    END IF;
END $$;

-- 4. penjaga akhir
DO $$
DECLARE n int; s record;
BEGIN
    SELECT count(*) INTO n
    FROM "Tenant" t CROSS JOIN (VALUES ('EMPLOYEE_ADVANCE', '1-10450'), ('BANK_ADJUSTMENT_GAIN', '4-90000'), ('BANK_ADJUSTMENT_LOSS', '5-20900'),
                                      ('REVENUE_SALES_SERVICE', '4-10150')) p(r, kode)
    WHERE NOT EXISTS (SELECT 1 FROM account_roles a JOIN chart_of_accounts c ON c.id = a.account_id AND c.tenant_id = a.tenant_id
                      WHERE a.tenant_id = t.id AND a.role_key = p.r AND NOT c.is_header
                        AND (COALESCE(a.notes, '') NOT LIKE 'V444%' OR c.account_code = p.kode));
    IF n > 0 THEN RAISE EXCEPTION 'V444: % pasangan tenant x peran masih tak terpetakan / salah kode', n; END IF;
    SELECT * INTO s FROM v444_sebelum;
    IF s.peran_gg IS DISTINCT FROM (SELECT md5(string_agg(role_key || ':' || account_id::text || ':' || coalesce(notes, ''), '|' ORDER BY role_key))
                                      FROM account_roles WHERE tenant_id = 'grapgrap-manado') THEN
        RAISE EXCEPTION 'V444: account_roles grapgrap-manado BERUBAH';
    END IF;
    IF s.coa_gg IS DISTINCT FROM (SELECT md5(string_agg(id::text || ':' || account_code || ':' || name || ':' || coalesce(description, ''), '|' ORDER BY account_code, id))
                                    FROM chart_of_accounts WHERE tenant_id = 'grapgrap-manado') THEN
        RAISE EXCEPTION 'V444: chart_of_accounts grapgrap-manado BERUBAH';
    END IF;
    IF (SELECT count(*) FROM journal_entries) <> s.n_jurnal THEN RAISE EXCEPTION 'V444: jumlah jurnal berubah'; END IF;
    IF (SELECT count(*) FROM account_roles) <> s.n_peran + s.celah THEN RAISE EXCEPTION 'V444: jumlah peran <> sebelum + celah'; END IF;
END $$;

DROP TABLE v444_sebelum;
