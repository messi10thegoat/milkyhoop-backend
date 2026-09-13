-- UJI KERING V248: badan dipasang di dalam transaksi lalu ROLLBACK. Baca ramalan: residu tepat 2 anggota, L3 nol.
\set ON_ERROR_STOP 1
BEGIN;
\i /tmp/V248_badan.sql
\echo === verdikt baru
SELECT * FROM verify_ar_reconciliation_all();
\echo === rincian per tenant
SELECT 'grapgrap-manado' t, * FROM verify_ar_reconciliation_rincian('grapgrap-manado')
UNION ALL SELECT 'kaos-biru-konveksi', * FROM verify_ar_reconciliation_rincian('kaos-biru-konveksi');
\echo === cacah klaim per cara
SELECT 'kaos' t, cara, count(*), SUM(amount) FROM ar_klaim_piutang('kaos-biru-konveksi') GROUP BY 1,2
UNION ALL SELECT 'grap', cara, count(*), SUM(amount) FROM ar_klaim_piutang('grapgrap-manado') GROUP BY 1,2 ORDER BY 1,2;
ROLLBACK;
\echo === sesudah ROLLBACK: tabel pin ada?
SELECT to_regclass('ar_reconciliation_pins') IS NULL AS pin_tak_ada, (SELECT count(*) FROM audit_logs WHERE "eventType"='AR_RECONCILIATION_PIN') audit;
