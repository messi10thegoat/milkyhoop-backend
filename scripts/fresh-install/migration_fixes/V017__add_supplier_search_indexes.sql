-- FIXDIR override of V017 (2026-07-25): drop CONCURRENTLY.
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block, so it fails
-- under the hardened runner (--single-transaction). CONCURRENTLY only matters to
-- avoid locking a table under live traffic; on a fresh-install empty DB a plain
-- CREATE INDEX is correct and faster. Surfaced by the 2026-07-25 hard rebuild.
CREATE INDEX IF NOT EXISTS idx_transaksi_tenant_supplier
ON public.transaksi_harian (tenant_id, nama_pihak)
WHERE nama_pihak IS NOT NULL AND nama_pihak != '';
