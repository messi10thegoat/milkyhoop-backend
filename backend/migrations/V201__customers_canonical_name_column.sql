-- ============================================================================
-- V201__customers_canonical_name_column.sql
--
-- BUG: POST /api/customers -> 500
--   null value in column "name" of relation "customers" violates not-null
--   constraint  (routers/customers.py:632)
-- Artinya: pada skema yang BENAR, membuat pelanggan MUSTAHIL.
--
-- AKAR (hasil audit, bukan asumsi):
--   - V024__customers_table.sql membuat kolom `name VARCHAR(255) NOT NULL`.
--   - Aplikasi kemudian pindah ke kolom Indonesia `nama`. customers.py MENULIS
--     `nama` (body.name -> $4) dan MEMBACA `nama` di seluruh path (list :140,
--     detail :269/:377, lookup :528/:740, dedupe :587).
--   - `customers.name` TIDAK DIBACA siapa pun: nol referensi di Python, nol di
--     fungsi SQL, nol di view.
--   - Penulis tabel ini HANYA SATU di seluruh backend (customers.py:634); jalur
--     chat/agent (unified_agent/tool_executor) memanggil endpoint HTTP yang sama,
--     bukan SQL langsung. Endpoint POST lain (opening-balance, merge) meng-UPDATE.
--   - `nama` sendiri baru dipulihkan oleh V195; migrasi asli yang
--     memperkenalkannya hilang bersama droplet lama, dan migrasi itulah yang
--     hampir pasti dulu melonggarkan `name`.
--
-- KEPUTUSAN: constraint yang TIDAK BISA DIPENUHI kode manapun adalah vestige,
-- bukan integritas. Tapi jaminan "pelanggan wajib punya nama" tetap bernilai —
-- jadi jaminan itu DIPINDAHKAN ke kolom yang benar-benar kanonik, bukan dibuang.
--
-- Ini BERBEDA dari tambalan ad-hoc agen E2E (2026-07-23 16:53:16) yang hanya
-- menjalankan `ALTER COLUMN name DROP NOT NULL` lewat psql tanpa migrasi dan
-- tanpa memindahkan jaminan ke `nama`.
--
-- DITOLAK (alternatif): menambahkan `name` ke INSERT agar NOT NULL bertahan.
-- Itu memelihara dua kolom kembar tanpa pembaca dan tanpa enforcement
-- sinkronisasi — drift yang menunggu terjadi.
-- ============================================================================

BEGIN;

-- 1. Lepaskan vestige.
ALTER TABLE customers ALTER COLUMN name DROP NOT NULL;
COMMENT ON COLUMN customers.name IS
    'DEPRECATED (vestige V024). Kolom kanonik = nama. Tidak dibaca/ditulis kode manapun sejak rename ke Bahasa Indonesia.';

-- 2. Amankan data sebelum memindahkan jaminan (idempoten; 0 baris pada fresh).
UPDATE customers SET nama = COALESCE(NULLIF(TRIM(nama), ''), NULLIF(TRIM(name), ''), '(tanpa nama)')
 WHERE nama IS NULL OR TRIM(nama) = '';

-- 3. Pindahkan jaminan ke kolom kanonik.
ALTER TABLE customers ALTER COLUMN nama SET NOT NULL;
COMMENT ON COLUMN customers.nama IS
    'Nama pelanggan (KANONIK). Diisi dari body.name; dibaca seluruh read path.';

-- 4. Assertion fail-loud.
DO $v201$
DECLARE v_name_null TEXT; v_nama_null TEXT;
BEGIN
    SELECT is_nullable INTO v_name_null FROM information_schema.columns
     WHERE table_name='customers' AND column_name='name';
    SELECT is_nullable INTO v_nama_null FROM information_schema.columns
     WHERE table_name='customers' AND column_name='nama';
    IF v_name_null <> 'YES' THEN
        RAISE EXCEPTION 'V201: customers.name masih NOT NULL';
    END IF;
    IF v_nama_null <> 'NO' THEN
        RAISE EXCEPTION 'V201: customers.nama belum NOT NULL — jaminan tidak berpindah';
    END IF;
    RAISE NOTICE 'V201 OK: name nullable (deprecated), nama NOT NULL (kanonik)';
END $v201$;

COMMIT;
