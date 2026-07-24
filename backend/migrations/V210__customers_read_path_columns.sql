-- ============================================================================
-- V210__customers_read_path_columns.sql
--
-- BUG: modul Kontak->Pelanggan "Gagal memuat data pelanggan" (GET /api/customers
--      500). asyncpg UndefinedColumnError: column "tipe" does not exist
--      (customers.py:279 list, :377 detail, :390, filter :241).
--
-- KELAS BARU (ketiga): DRIFT READ-PATH (SELECT). Audit INSERT (V202) & UPDATE
-- (V206 dsb) tak menangkap ini — kolom dibaca SELECT, bukan ditulis. GET list
-- crash di kolom pertama yang hilang, jadi seluruh modul list mati walau
-- create (chat) sukses.
--
-- 5 kolom yang DIBACA customers.py tapi tak ada di tabel:
--   tipe               — SELECT + filter (row["tipe"] -> response "type")
--   points             — loyalty (members.py: points:int)
--   total_transaksi    — loyalty (members.py: int)
--   total_nilai        — loyalty (nilai transaksi kumulatif)
--   last_transaction_at— loyalty (members.py: Optional[str])
--
-- tipe: kode HANYA membaca/memfilter (nol INSERT/UPDATE tipe) -> dibuat
-- GENERATED dari customer_type (yang ditulis create path, V195). Mirror
-- otomatis, filter+read jalan, create baru langsung punya tipe, NOL ubah kode.
-- ============================================================================

BEGIN;

-- tipe = cermin customer_type (read-only di kode)
ALTER TABLE customers ADD COLUMN IF NOT EXISTS tipe VARCHAR(50)
    GENERATED ALWAYS AS (customer_type) STORED;

-- loyalty / denormalized transaction cache (ditulis members.py)
ALTER TABLE customers ADD COLUMN IF NOT EXISTS points              INTEGER       DEFAULT 0;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS total_transaksi     INTEGER       DEFAULT 0;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS total_nilai         NUMERIC(18,2) DEFAULT 0;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS last_transaction_at TIMESTAMPTZ;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS points_per_50k      INTEGER       DEFAULT 1;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS default_currency_id UUID;  -- dibaca detail SELECT (referensi currencies)

DO $v210$
DECLARE v_missing TEXT := '';
BEGIN
    FOR v_missing IN
        SELECT c FROM unnest(ARRAY['tipe','points','points_per_50k','total_transaksi','total_nilai','last_transaction_at','default_currency_id']) AS c
        WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name='customers' AND column_name=c)
    LOOP
        RAISE EXCEPTION 'V210: customers.% belum terbentuk', v_missing;
    END LOOP;
    RAISE NOTICE 'V210 OK: customers read-path columns (tipe generated + loyalty)';
END $v210$;

COMMIT;
