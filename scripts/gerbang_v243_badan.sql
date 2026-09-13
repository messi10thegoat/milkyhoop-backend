-- BADAN gerbang V243 (R9). Dijalankan DI DALAM transaksi pemanggil yang di-ROLLBACK.
-- Hasil tiap sisi ditangkap \gset SEBELUM rollback savepoint (pelajaran V242),
-- cacah hasil di-assert.
\set ON_ERROR_STOP 1
\set T 'kaos-biru-konveksi'
\set G 'grapgrap-manado'

CREATE TEMP TABLE hasil (sisi text, pemeriksaan text, verdict text, harap text);

-- R9 LAMA, bentuk PERSIS check_3 sebelum V243: cacah rekening ber-gap.
CREATE OR REPLACE FUNCTION pg_temp.r9_lama(p_tenant text) RETURNS bigint LANGUAGE sql AS $$
    WITH bank_coa AS (
        SELECT ba.id AS bank_account_id, ba.account_name, ba.coa_id
        FROM bank_accounts ba WHERE ba.is_active = true AND ba.tenant_id = p_tenant),
    journal_balance AS (
        SELECT bc.bank_account_id, COALESCE(SUM(jl.debit) - SUM(jl.credit), 0) AS ledger_balance
        FROM bank_coa bc
        LEFT JOIN journal_lines jl ON jl.account_id = bc.coa_id
        LEFT JOIN journal_entries je ON je.id = jl.journal_id AND je.status = 'POSTED'
        GROUP BY bc.bank_account_id),
    bank_txn_bal AS (
        SELECT bank_account_id, COALESCE(SUM(amount), 0) AS txn_balance
        FROM bank_transactions WHERE tenant_id = p_tenant GROUP BY bank_account_id)
    SELECT COUNT(*) FROM bank_coa bc
    LEFT JOIN journal_balance jb ON jb.bank_account_id = bc.bank_account_id
    LEFT JOIN bank_txn_bal btb ON btb.bank_account_id = bc.bank_account_id
    WHERE ABS(COALESCE(jb.ledger_balance, 0) - COALESCE(btb.txn_balance, 0)) > 0.01;
$$;

-- Suntik: jurnal DRAFT ber-baris di CoA bank + btx terikat. Tanpa POSTED -> tanpa
-- saldo buku, tapi btx ikut terhitung -> gap NYATA. R9 lama menjumlahkan baris DRAFT
-- -> gap tersembunyi.
CREATE OR REPLACE FUNCTION pg_temp.suntik_draft(p_tenant text, p_amount numeric) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE ba record; kontra uuid; jid uuid := gen_random_uuid();
BEGIN
    SELECT b.id, b.coa_id INTO ba FROM bank_accounts b
     WHERE b.tenant_id = p_tenant AND b.is_active ORDER BY b.account_name LIMIT 1;
    SELECT id INTO kontra FROM chart_of_accounts
     WHERE tenant_id = p_tenant AND account_type = 'EXPENSE' AND COALESCE(is_header,false) = false
     ORDER BY account_code LIMIT 1;
    INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description,
        source_type, source_id, status, total_debit, total_credit)
    VALUES (jid, p_tenant, 'GERBANG-' || substr(jid::text,1,8), CURRENT_DATE, 'gerbang V243',
        'BANK_TRANSACTION', jid, 'DRAFT', p_amount, p_amount);  -- source_id bertipe uuid
    INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
    VALUES (gen_random_uuid(), jid, 1, kontra, p_amount, 0, 'gerbang'),
           (gen_random_uuid(), jid, 2, ba.coa_id, 0, p_amount, 'gerbang');
    INSERT INTO bank_transactions (id, tenant_id, bank_account_id, transaction_date, transaction_type,
        amount, running_balance, description, origin_type, status, journal_id, created_by)
    VALUES (gen_random_uuid(), p_tenant, ba.id, CURRENT_DATE, 'withdrawal',
        -p_amount, 0, 'gerbang V243', 'MANUAL', 'POSTED', jid,
        (SELECT created_by FROM bank_transactions WHERE tenant_id = p_tenant AND created_by IS NOT NULL LIMIT 1));
END; $$;

\echo '== patok bank_sync'
SELECT check_name, tenant_id, baseline_count, baseline_amount, ticket FROM health_check_exemptions WHERE check_name='bank_sync';

-- 1. HIJAU berpatok + tenant sehat; R9 lama pun 0 hari ini (pembanding)
INSERT INTO hasil VALUES
  ('1 hijau', 'bank_sync@kaos-biru', (SELECT verdict FROM hc_verdict('bank_sync', :'T')), 'PASS_EXEMPT'),
  ('1 sehat', 'bank_sync@grapgrap',  (SELECT verdict FROM hc_verdict('bank_sync', :'G')), 'PASS'),
  ('1 pembanding', 'R9 LAMA kaos-biru hari ini (cacah rekening ber-gap)', pg_temp.r9_lama(:'T')::text, '0');

-- 2. MERAH tanpa patok
SAVEPOINT s2;
DELETE FROM health_check_exemptions WHERE check_name = 'bank_sync';
SELECT (hc_verdict('bank_sync', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s2;
INSERT INTO hasil VALUES ('2 tanpa patok', 'bank_sync@kaos-biru', :'v', 'FAIL_NON_EXEMPT');

-- 3. KEBUTAAN LAMA LEWAT EKSEKUSI: jurnal DRAFT + btx terikat
SAVEPOINT s3;
SELECT pg_temp.suntik_draft(:'T', 777);
SELECT pg_temp.r9_lama(:'T')::text AS lama_t, (hc_verdict('bank_sync', :'T')).verdict AS baru_t \gset
ROLLBACK TO SAVEPOINT s3;
SAVEPOINT s3g;
SELECT pg_temp.suntik_draft(:'G', 777);
SELECT pg_temp.r9_lama(:'G')::text AS lama_g, (hc_verdict('bank_sync', :'G')).verdict AS baru_g \gset
ROLLBACK TO SAVEPOINT s3g;
INSERT INTO hasil VALUES
  ('3 buta-lama', 'R9 LAMA kaos-biru sesudah suntik DRAFT (BUTA = 0)', :'lama_t', '0'),
  ('3 buta-lama', 'R9 BARU kaos-biru sesudah suntik DRAFT', :'baru_t', 'FAIL_DRIFT_CHANGED'),
  ('3 buta-lama', 'R9 LAMA grapgrap sesudah suntik DRAFT (BUTA = 0)', :'lama_g', '0'),
  ('3 buta-lama', 'R9 BARU grapgrap sesudah suntik DRAFT', :'baru_g', 'FAIL_NON_EXEMPT');

-- 4. TAMBAH: btx tanpa jurnal -> sisa tak terjelaskan
SAVEPOINT s4;
INSERT INTO bank_transactions (id, tenant_id, bank_account_id, transaction_date, transaction_type,
    amount, running_balance, description, origin_type, status, created_by)
SELECT gen_random_uuid(), :'T', b.id, CURRENT_DATE, 'deposit', 5, 0, 'gerbang V243', 'MANUAL', 'POSTED',
       (SELECT created_by FROM bank_transactions WHERE tenant_id = :'T' AND created_by IS NOT NULL LIMIT 1)
FROM bank_accounts b WHERE b.tenant_id = :'T' AND b.account_name = 'BCA Pengeluaran';
SELECT (hc_verdict('bank_sync', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s4;
INSERT INTO hasil VALUES ('4 tambah', 'btx tanpa jurnal -> sisa', :'v', 'FAIL_DRIFT_CHANGED');

-- 5. GANTI MURNI: btx satu jurnal VOID anggota dipindah ke jurnal DRAFT salinan
--    (baris sama persis) -> anggota lama keluar, anggota baru bernilai sama masuk.
SAVEPOINT s5;
SELECT hc_bank_sync_drift(:'T') AS d0 \gset
SELECT count(*) AS n0, sum(amount) AS s0 FROM hc_bank_sync_members(:'T') \gset
CREATE TEMP TABLE sasaran5 AS
SELECT bt.id AS btx_id, je.id AS jid FROM bank_transactions bt
JOIN journal_entries je ON je.id = bt.journal_id
WHERE je.status = 'VOID' AND bt.tenant_id = :'T' ORDER BY je.id LIMIT 1;
CREATE TEMP TABLE salinan5 AS SELECT gen_random_uuid() AS jid_baru;
INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description,
    source_type, source_id, status, total_debit, total_credit)
SELECT s.jid_baru, je.tenant_id, 'GERBANG-GANTI-' || substr(s.jid_baru::text,1,8), je.journal_date,
       'gerbang V243 ganti', je.source_type, je.source_id, 'DRAFT', je.total_debit, je.total_credit
FROM journal_entries je, salinan5 s WHERE je.id = (SELECT jid FROM sasaran5);
INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo)
SELECT gen_random_uuid(), s.jid_baru, jl.line_number, jl.account_id, jl.debit, jl.credit, jl.memo
FROM journal_lines jl, salinan5 s WHERE jl.journal_id = (SELECT jid FROM sasaran5);
UPDATE bank_transactions SET journal_id = (SELECT jid_baru FROM salinan5)
 WHERE id = (SELECT btx_id FROM sasaran5);
SELECT (hc_bank_sync_drift(:'T') = :d0 AND count(*) = :n0 AND sum(amount) = :s0)::text AS murni
FROM hc_bank_sync_members(:'T') \gset
SELECT (hc_verdict('bank_sync', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s5;
INSERT INTO hasil VALUES ('5 ganti', 'bank_sync jurnal diganti', :'v', 'FAIL_DRIFT_CHANGED'),
                         ('5 kontrol', 'drift+cacah+jumlah sama (penggantian murni)', :'murni', 'true');

-- 6. RUSAK: tenant kosong mengangkat galat
DO $$
BEGIN
    PERFORM hc_verdict('bank_sync', '');
    INSERT INTO hasil VALUES ('6 rusak', 'tenant kosong', 'TAK MENGANGKAT', 'GALAT');
EXCEPTION WHEN OTHERS THEN
    INSERT INTO hasil VALUES ('6 rusak', 'tenant kosong', 'GALAT', 'GALAT');
END $$;

-- 7. V242 tak terganggu
INSERT INTO hasil SELECT '7 V242', c, (hc_verdict(c, :'T')).verdict, 'PASS_EXEMPT'
FROM unnest(ARRAY['ap_invariant','inventory_value','status_desync']) c;

\echo ''
\echo '════════ HASIL ════════'
SELECT CASE WHEN verdict = harap THEN '[H]' ELSE '[X]' END AS ok, sisi, pemeriksaan, verdict, harap
FROM hasil ORDER BY sisi, pemeriksaan;
SELECT count(*) FILTER (WHERE verdict IS DISTINCT FROM harap) AS gagal, count(*) AS total,
       CASE WHEN count(*) = 15 THEN 'cacah hasil LENGKAP (15)'
            ELSE 'GERBANG TAK SAH: cacah=' || count(*) END AS kelengkapan
FROM hasil;
