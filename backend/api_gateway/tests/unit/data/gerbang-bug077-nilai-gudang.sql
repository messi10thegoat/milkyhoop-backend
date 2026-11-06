-- BUG-077 gerbang BACA SAJA (kaos): nilai per gudang = ROUND(ws.quantity x WAC barang, 0) -- ekspresi SAMA dengan
-- routers/warehouses.py (WAC_GUDANG / NILAI_GUDANG). Per barang: Σ gudang vs ROUND(Q x WAC, 0) (tol 0,5 x n_gudang)
-- dan vs nilai kartu stok Σ(in x cost - out x cost) (tol tambahan Q x 0,005 = WAC 2 dp, batas Check 15).
-- Hanya barang yang memenuhi Inv-1 (Σ warehouse_stock = qty ledger, qty > 0); sisanya dihitung terpisah (di luar janji).
-- Pakai: psql -v t=kaos-biru-konveksi -f ...   Harapan: salah = 0.
BEGIN READ ONLY;
PREPARE g(text) AS
WITH gudang AS (
  SELECT ws.item_id, COUNT(*) AS n, SUM(ws.quantity) AS q, SUM(ROUND(ws.quantity * w.wac, 0)) AS nilai, MAX(w.wac) AS wac
  FROM warehouse_stock ws
  CROSS JOIN LATERAL (SELECT get_weighted_average_cost($1, ws.item_id) AS wac) w
  WHERE ws.tenant_id = $1 AND ws.quantity > 0 GROUP BY ws.item_id),
kartu AS (
  SELECT product_id, SUM(quantity_in) - SUM(quantity_out) AS q, SUM(quantity_in * unit_cost) - SUM(quantity_out * unit_cost) AS nilai
  FROM inventory_ledger WHERE tenant_id = $1 GROUP BY product_id)
SELECT COUNT(*) FILTER (WHERE g.q = k.q) AS dijanjikan,
       COUNT(*) FILTER (WHERE g.q IS DISTINCT FROM k.q) AS di_luar_janji_inv1,
       COUNT(*) FILTER (WHERE g.q = k.q AND (ABS(g.nilai - ROUND(k.q * g.wac, 0)) > 0.5 * g.n
                                          OR ABS(g.nilai - k.nilai) > 0.5 * g.n + k.q * 0.005)) AS salah,
       SUM(g.nilai) AS total_nilai_gudang, MAX(g.n) AS maks_gudang_per_barang
FROM gudang g LEFT JOIN kartu k ON k.product_id = g.item_id;
EXECUTE g(:'t');
ROLLBACK;
