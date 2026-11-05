-- GERBANG DB DUA SISI BUG-057 / V480 (6 Nov 2026). SATU transaksi, selalu ROLLBACK (nol perubahan produksi).
-- Pakai: salin V480 + _ROLLBACK ke /tmp kontainer postgres, lalu psql -f berkas ini. HIJAU: SESUDAH_V480 salah_onhand = 0,
-- berubah_cadangan = 0; SESUDAH_ROLLBACK beda_dari_lama = 0 dan definisi_sama = t. KONTROL MERAH (terbukti 6 Nov): pasang definisi
-- lama sebagai /tmp/V480.sql -> salah_onhand = 5.
\set ON_ERROR_STOP 1
BEGIN;
CREATE TEMP TABLE t57_def AS SELECT md5(pg_get_functiondef('public.get_weighted_average_cost(text, uuid)'::regprocedure)) AS asli;
CREATE TEMP TABLE t57 AS
  WITH a AS (SELECT tenant_id, product_id, SUM(quantity_in)-SUM(quantity_out) q, SUM(quantity_in*unit_cost)-SUM(quantity_out*unit_cost) n
             FROM inventory_ledger GROUP BY 1,2)
  SELECT tenant_id, product_id, q, round(n/NULLIF(q,0),2) benar, get_weighted_average_cost(tenant_id, product_id) lama FROM a;
\i /tmp/V480.sql
ALTER TABLE t57 ADD COLUMN baru numeric;
UPDATE t57 SET baru = get_weighted_average_cost(tenant_id, product_id);
SELECT 'SESUDAH_V480' AS k, count(*) produk, count(*) FILTER (WHERE q > 0 AND benar >= 0 AND baru <> benar) salah_onhand,
       count(*) FILTER (WHERE NOT (q > 0 AND benar >= 0) AND baru <> lama) berubah_cadangan,
       count(*) FILTER (WHERE baru <> lama) berubah_total FROM t57;
SELECT 'NYAWIT' AS k, p.nama_produk, t.lama, t.baru, t.benar FROM t57 t JOIN products p ON p.id = t.product_id
 WHERE t.tenant_id = 'pt-kita-nyawit-bersama' AND t.lama <> t.baru;
\i /tmp/V480_ROLLBACK.sql
SELECT 'SESUDAH_ROLLBACK' AS k, count(*) FILTER (WHERE get_weighted_average_cost(tenant_id, product_id) <> lama) beda_dari_lama,
       (SELECT asli FROM t57_def) = md5(pg_get_functiondef('public.get_weighted_average_cost(text, uuid)'::regprocedure)) AS definisi_sama
  FROM t57;
ROLLBACK;
