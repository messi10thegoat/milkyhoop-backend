-- BADAN gerbang V242. Dijalankan DI DALAM transaksi pemanggil yang di-ROLLBACK.
-- Sisi: hijau-berpatok · merah-tanpa-patok · sabotase-tambah · sabotase-GANTI
-- (identitas beda, drift+cacah+jumlah SAMA) · kueri rusak mengangkat galat.
--
-- ⚠️ Versi pertama gerbang ini MENIPU: hasil sisi merah di-INSERT ke tabel temp
-- lalu ROLLBACK TO SAVEPOINT ikut membatalkan INSERT-nya -> hanya sisi hijau yang
-- tercetak, "0 gagal dari 8". Sekarang verdict ditangkap ke variabel psql (\gset)
-- SEBELUM rollback savepoint, dan jumlah baris hasil di-assert = 20.
\set ON_ERROR_STOP 1
\set T 'kaos-biru-konveksi'

CREATE TEMP TABLE hasil (sisi text, pemeriksaan text, verdict text, harap text);

\echo '== patok yang terpasang'
SELECT check_name, tenant_id, baseline_count, baseline_amount, ticket FROM health_check_exemptions ORDER BY 1;

-- 1. HIJAU berpatok + tenant sehat (tanpa savepoint)
INSERT INTO hasil SELECT '1 hijau', c || '@' || :'T', (hc_verdict(c, :'T')).verdict, 'PASS_EXEMPT'
FROM unnest(ARRAY['ap_invariant','inventory_value','status_desync']) c;
INSERT INTO hasil SELECT '1 sehat', c || '@grapgrap-manado', (hc_verdict(c, 'grapgrap-manado')).verdict, 'PASS'
FROM unnest(ARRAY['ap_invariant','inventory_value','status_desync']) c;

-- 2. MERAH tanpa patok
SAVEPOINT s2;
DELETE FROM health_check_exemptions;
SELECT (hc_verdict('ap_invariant', :'T')).verdict AS v7,
       (hc_verdict('inventory_value', :'T')).verdict AS v9,
       (hc_verdict('status_desync', :'T')).verdict AS v13 \gset
ROLLBACK TO SAVEPOINT s2;
INSERT INTO hasil VALUES ('2 tanpa patok','ap_invariant',:'v7','FAIL_NON_EXEMPT'),
                         ('2 tanpa patok','inventory_value',:'v9','FAIL_NON_EXEMPT'),
                         ('2 tanpa patok','status_desync',:'v13','FAIL_NON_EXEMPT');

-- 3a. SABOTASE TAMBAH — status_desync: satu tagihan POSTED ber-jurnal-hidup jadi UNPOSTED
SAVEPOINT s3a;
UPDATE bills SET accounting_status = 'UNPOSTED'
 WHERE id = (SELECT b.id FROM bills b WHERE b.tenant_id = :'T' AND b.accounting_status = 'POSTED'
               AND EXISTS (SELECT 1 FROM journal_entries je WHERE je.source_type='BILL'
                           AND je.source_id::text=b.id::text AND je.status='POSTED' AND je.reversed_by_id IS NULL)
             ORDER BY b.id LIMIT 1);
SELECT (hc_verdict('status_desync', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s3a;
INSERT INTO hasil VALUES ('3 tambah','status_desync',:'v','FAIL_DRIFT_CHANGED');

-- 3b. SABOTASE TAMBAH — inventory: satu saldo awal tanpa jurnal
SAVEPOINT s3b;
CREATE TEMP TABLE sisip3b AS SELECT * FROM inventory_ledger
 WHERE tenant_id = :'T' AND movement_type='OPENING_BALANCE' AND journal_id IS NULL LIMIT 1;
UPDATE sisip3b SET id = gen_random_uuid(), quantity_in = 1, quantity_out = 0, unit_cost = 1000, total_cost = 1000;
INSERT INTO inventory_ledger SELECT * FROM sisip3b;
SELECT (hc_verdict('inventory_value', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s3b;
INSERT INTO hasil VALUES ('3 tambah','inventory_value',:'v','FAIL_DRIFT_CHANGED');

-- 3c. SABOTASE TAMBAH — AP: satu alokasi pembayaran tagihan LAIN dipindah ke tagihan draf
--     (void tagihan ber-jurnal-hidup DITOLAK trg_guard_void_bill_jurnal -- stimulus itu
--      tak pernah mencapai pemeriksaan, jadi diganti)
SAVEPOINT s3c;
UPDATE bill_payment_allocations SET bill_id =
       (SELECT b.id FROM bills b WHERE b.tenant_id = :'T' AND b.status_v2 = 'draft' ORDER BY b.id LIMIT 1)
 WHERE id = (SELECT a.id FROM bill_payment_allocations a JOIN bills b ON b.id = a.bill_id
              WHERE b.tenant_id = :'T' AND b.invoice_number <> 'BILL-2609-0002'
              ORDER BY a.id LIMIT 1);
SELECT (hc_verdict('ap_invariant', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s3c;
INSERT INTO hasil VALUES ('3 tambah','ap_invariant',:'v','FAIL_DRIFT_CHANGED');

-- 4a. SABOTASE GANTI — status_desync: 0028 keluar, tagihan lain masuk dgn nominal SAMA
SAVEPOINT s4a;
SELECT count(*) AS n0, sum(amount) AS s0 FROM hc_status_desync_members(:'T') \gset
UPDATE bills SET accounting_status = 'REVERSED'
 WHERE tenant_id = :'T' AND invoice_number = 'BILL-2609-0028';
UPDATE bills SET accounting_status = 'UNPOSTED', amount = 1120000
 WHERE id = (SELECT b.id FROM bills b WHERE b.tenant_id = :'T' AND b.accounting_status = 'POSTED'
               AND EXISTS (SELECT 1 FROM journal_entries je WHERE je.source_type='BILL'
                           AND je.source_id::text=b.id::text AND je.status='POSTED' AND je.reversed_by_id IS NULL)
             ORDER BY b.id LIMIT 1);
SELECT (count(*) = :n0 AND sum(amount) = :s0)::text AS murni FROM hc_status_desync_members(:'T') \gset
SELECT (hc_verdict('status_desync', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s4a;
INSERT INTO hasil VALUES ('4 ganti','status_desync (penggantian murni=' || :'murni' || ')',:'v','FAIL_DRIFT_CHANGED'),
                         ('4 kontrol','status_desync cacah+jumlah sama',:'murni','true');

-- 4b. SABOTASE GANTI — inventory: nilai saldo awal yatim dipindah ke baris BARU
--     (baris lama nilainya dinolkan, baris baru membawa nilai sama -> drift tetap)
SAVEPOINT s4b;
SELECT hc_inventory_drift(:'T') AS d0 \gset
SELECT count(*) AS n0, sum(amount) AS s0 FROM hc_inventory_members(:'T') \gset
CREATE TEMP TABLE sisip4b AS SELECT * FROM inventory_ledger
 WHERE tenant_id = :'T' AND movement_type='OPENING_BALANCE' AND journal_id IS NULL;
UPDATE inventory_ledger SET quantity_in = 0, quantity_out = 0 WHERE id IN (SELECT id FROM sisip4b);
UPDATE sisip4b SET id = gen_random_uuid();
INSERT INTO inventory_ledger SELECT * FROM sisip4b;
SELECT (hc_inventory_drift(:'T') = :d0 AND count(*) = :n0 AND sum(amount) = :s0)::text AS murni
FROM hc_inventory_members(:'T') \gset
SELECT (hc_verdict('inventory_value', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s4b;
INSERT INTO hasil VALUES ('4 ganti','inventory_value',:'v','FAIL_DRIFT_CHANGED'),
                         ('4 kontrol','inventory drift+cacah+jumlah sama',:'murni','true');

-- 4c. SABOTASE GANTI — AP: alokasi pembayaran 100.000 dipindah ke tagihan void LAIN
SAVEPOINT s4c;
SELECT hc_ap_drift(:'T') AS d0 \gset
SELECT count(*) AS n0, sum(amount) AS s0 FROM hc_ap_members(:'T') \gset
UPDATE bill_payment_allocations SET bill_id =
       (SELECT b.id FROM bills b WHERE b.tenant_id = :'T' AND b.status_v2 = 'void'
          AND b.invoice_number <> 'BILL-2609-0002' ORDER BY b.id LIMIT 1)
 WHERE bill_id = (SELECT id FROM bills WHERE tenant_id = :'T' AND invoice_number = 'BILL-2609-0002');
SELECT (hc_ap_drift(:'T') = :d0 AND count(*) = :n0 AND sum(amount) = :s0)::text AS murni
FROM hc_ap_members(:'T') \gset
SELECT (hc_verdict('ap_invariant', :'T')).verdict AS v \gset
ROLLBACK TO SAVEPOINT s4c;
INSERT INTO hasil VALUES ('4 ganti','ap_invariant',:'v','FAIL_DRIFT_CHANGED'),
                         ('4 kontrol','ap drift+cacah+jumlah sama',:'murni','true');

-- 5. KUERI RUSAK harus MENGANGKAT galat (skrip memetakan galat -> __GAGAL__ -> BROKEN)
DO $$
BEGIN
    PERFORM hc_verdict('pemeriksaan_karangan', 'kaos-biru-konveksi');
    INSERT INTO hasil VALUES ('5 rusak', 'karangan', 'TAK MENGANGKAT', 'GALAT');
EXCEPTION WHEN OTHERS THEN
    INSERT INTO hasil VALUES ('5 rusak', 'karangan', 'GALAT', 'GALAT');
END $$;
DO $$
BEGIN
    PERFORM hc_verdict('ap_invariant', '');
    INSERT INTO hasil VALUES ('5 rusak', 'tenant kosong', 'TAK MENGANGKAT', 'GALAT');
EXCEPTION WHEN OTHERS THEN
    INSERT INTO hasil VALUES ('5 rusak', 'tenant kosong', 'GALAT', 'GALAT');
END $$;

\echo ''
\echo '════════ HASIL ════════'
SELECT CASE WHEN verdict = harap THEN '[H]' ELSE '[X]' END AS ok, sisi, pemeriksaan, verdict, harap
FROM hasil ORDER BY sisi, pemeriksaan;
SELECT count(*) FILTER (WHERE verdict IS DISTINCT FROM harap) AS gagal, count(*) AS total,
       CASE WHEN count(*) = 20 THEN 'cacah hasil LENGKAP (20)'
            ELSE 'GERBANG TAK SAH: hasil hilang, cacah=' || count(*) END AS kelengkapan
FROM hasil;
