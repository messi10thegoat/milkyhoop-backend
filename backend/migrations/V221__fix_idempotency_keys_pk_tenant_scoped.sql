-- V221__fix_idempotency_keys_pk_tenant_scoped.sql
--
-- KOREKSI MODEL, bukan tambalan.
--
-- `idempotency_keys` adalah tabel MULTI-TENANT (kolom tenant_id NOT NULL, dan
-- SEMUA query aplikasi memfilter `WHERE tenant_id = $1 AND key = $2`).
-- Tetapi PRIMARY KEY-nya `(key)` saja. PK tanpa tenant_id pada tabel multi-tenant
-- MENYATAKAN "key ini unik SECARA GLOBAL lintas tenant" — itu pernyataan yang
-- KELIRU tentang domainnya sejak awal. Kunci idempotency adalah properti
-- (tenant, operasi), bukan properti global.
--
-- AKIBAT KONKRET (terbukti runtime 2026-08-06 di milkydb_fresh):
--   INSERT key='RCV:x' tenant='A'                                   -> OK
--   INSERT key='RCV:x' tenant='B' ON CONFLICT (tenant_id,key) DO NOTHING
--     -> ERROR duplicate key ... "idempotency_keys_pkey"
--     -> current transaction is aborted
--   ON CONFLICT (tenant_id, key) TIDAK menangkap pelanggaran PK(key).
--   Jadi bukan sekadar duplikat tertolak: SELURUH TRANSAKSI tenant B GUGUR,
--   pembayarannya gagal, hanya karena tenant lain memakai string kunci sama.
--
-- KENAPA DORMAN SELAMA INI: implementasi sekarang memakai kunci ACAK (UUID),
-- sehingga tabrakan lintas tenant praktis mustahil. Rencana pindah ke KUNCI
-- DETERMINISTIK sisi-server (Law 14, lapis 1) justru MENGAKTIFKAN bug ini —
-- kunci jadi string yang diturunkan dari data domain, sehingga dua tenant yang
-- memakai pola sama menghasilkan string sama. Perbaiki SEBELUM rollout itu.
--
-- IDEMPOTEN: aman dijalankan ulang.

BEGIN;

DO $mig$
BEGIN
    -- 1) Lepas PK lama (key) bila masih ada
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.idempotency_keys'::regclass
          AND contype = 'p' AND conname = 'idempotency_keys_pkey'
    ) THEN
        -- Hanya lepas kalau PK-nya memang (key) saja; kalau sudah (tenant_id,key)
        -- berarti migrasi ini sudah pernah jalan.
        IF (SELECT array_length(conkey, 1) FROM pg_constraint
             WHERE conrelid = 'public.idempotency_keys'::regclass AND contype='p') = 1 THEN
            ALTER TABLE public.idempotency_keys DROP CONSTRAINT idempotency_keys_pkey;
            RAISE NOTICE 'V221: PK lama (key) dilepas';
        ELSE
            RAISE NOTICE 'V221: PK sudah komposit — dilewati';
        END IF;
    END IF;

    -- 2) Pasang PK baru (tenant_id, key)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.idempotency_keys'::regclass AND contype = 'p'
    ) THEN
        ALTER TABLE public.idempotency_keys
            ADD CONSTRAINT idempotency_keys_pkey PRIMARY KEY (tenant_id, key);
        RAISE NOTICE 'V221: PK baru (tenant_id, key) dipasang';
    END IF;

    -- 3) idx_idempotency_tenant_key kini REDUNDAN: PK (tenant_id, key) sudah
    --    membuat unique index dengan kolom & urutan identik. Mempertahankannya
    --    berarti dua index identik — biaya tulis ganda, nol manfaat.
    IF EXISTS (SELECT 1 FROM pg_indexes
                WHERE schemaname='public' AND indexname='idx_idempotency_tenant_key') THEN
        DROP INDEX public.idx_idempotency_tenant_key;
        RAISE NOTICE 'V221: idx_idempotency_tenant_key (redundan) dihapus';
    END IF;
END
$mig$;

COMMIT;
