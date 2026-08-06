-- REVERSE-DDL untuk V221 — ditulis SEBELUM apply, supaya tak perlu disusun saat panik.
-- JANGAN dijalankan kecuali V221 terbukti menyebabkan regresi.
--
-- Mengembalikan model LAMA yang KELIRU (PK global (key)). Hanya untuk rollback
-- darurat; setelah stabil, majulah kembali ke V221.
--
-- ⚠️ AKAN GAGAL bila sudah ada dua tenant memakai key yang sama — justru itulah
-- keadaan yang V221 buat menjadi mungkin. Kalau gagal, jangan dipaksa:
-- pulihkan dari snapshot /root/milkydb_walkthrough_20260806.sql.gz
-- (sha256 e16a2429fc18b3baf465ac6feaf24bea99d91e72509338f972c48578fc5ea2f0,
--  uji restore terverifikasi 2026-08-06: 13 jurnal / 1 tenant / 1 faktur / 1 DP).

BEGIN;

-- 1) lepas PK komposit
ALTER TABLE public.idempotency_keys DROP CONSTRAINT IF EXISTS idempotency_keys_pkey;

-- 2) pasang kembali PK global (key)  -- gagal bila ada key duplikat lintas tenant
ALTER TABLE public.idempotency_keys ADD CONSTRAINT idempotency_keys_pkey PRIMARY KEY (key);

-- 3) kembalikan index yang di-DROP V221
CREATE UNIQUE INDEX IF NOT EXISTS idx_idempotency_tenant_key
    ON public.idempotency_keys USING btree (tenant_id, key);

-- 4) hapus catatan migrasi supaya migrate.sh melihatnya sebagai pending lagi
DELETE FROM schema_migrations WHERE version = 'V221__fix_idempotency_keys_pk_tenant_scoped.sql';

COMMIT;
