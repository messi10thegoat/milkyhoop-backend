-- Uji kering V247a, SEMUA di ROLLBACK. Hasil ditangkap \gset / DO-EXCEPTION (bukan INSERT yg lalu dibatalkan).
\set ON_ERROR_STOP 0
BEGIN;
CREATE TEMP TABLE h (urut serial, uji text, hasil text, harap text);
SELECT count(*) AS audit0 FROM audit_logs \gset
SELECT customer_id AS cid0, total_amount AS tot0, journal_id AS jid0 FROM credit_notes WHERE id='a389ccfb-5a1b-4aff-8ec7-a8901636194b' \gset

\echo '== 1 maju pertama'
SAVEPOINT a;
\i /tmp/V247a_badan.sql
SELECT customer_id AS cid1, total_amount AS tot1, journal_id AS jid1 FROM credit_notes WHERE id='a389ccfb-5a1b-4aff-8ec7-a8901636194b' \gset
SELECT count(*) - :audit0 AS daudit1 FROM audit_logs \gset
\echo '== 2 maju KEDUA (harus RAISE 0 baris)'
SAVEPOINT b;
\i /tmp/V247a_badan.sql
ROLLBACK TO SAVEPOINT b;
\echo '== 3 ROLLBACK V247a'
\i /tmp/V247a_rb_badan.sql
SELECT customer_id AS cid3, total_amount AS tot3, journal_id AS jid3 FROM credit_notes WHERE id='a389ccfb-5a1b-4aff-8ec7-a8901636194b' \gset
SELECT count(*) - :audit0 AS daudit3 FROM audit_logs \gset
\echo '== 4 ROLLBACK KEDUA (harus RAISE 0 baris)'
SAVEPOINT c;
\i /tmp/V247a_rb_badan.sql
ROLLBACK TO SAVEPOINT c;
ROLLBACK TO SAVEPOINT a;

INSERT INTO h (uji, hasil, harap) VALUES
 ('maju: customer_id jadi uuid pelanggan', :'cid1', '15c07294-38cb-4523-8e11-1795b6c73069'),
 ('maju: total_amount tak berubah', :'tot1', :'tot0'),
 ('maju: journal_id tak berubah', :'jid1', :'jid0'),
 ('maju: 1 baris audit tercatat', :'daudit1', '1'),
 ('rollback: customer_id kembali Toko Melati', :'cid3', 'Toko Melati'),
 ('rollback: total & jurnal tak berubah', :'tot3' || '|' || :'jid3', :'tot0' || '|' || :'jid0'),
 ('rollback: +1 audit kompensasi (total 2, tak dihapus)', :'daudit3', '2');
SELECT CASE WHEN hasil = harap THEN '[H]' ELSE '[X]' END ok, uji, hasil, harap FROM h ORDER BY urut;
SELECT count(*) FILTER (WHERE hasil IS DISTINCT FROM harap) gagal, count(*) total FROM h;
ROLLBACK;
SELECT customer_id AS sesudah_semua, (SELECT count(*) FROM audit_logs) AS audit_sesudah FROM credit_notes WHERE id='a389ccfb-5a1b-4aff-8ec7-a8901636194b';
\echo 'audit sebelum:' :audit0
