-- V228: quotes — izinkan status 'void' di chk_quotes_status.
--
-- BUG YANG DITAMBAL: POST /api/quotes/{id}/void SELALU 500, untuk setiap
-- tenant, sejak endpoint itu ada. `quotes.py:1117` menulis
-- `UPDATE quotes SET status = 'void'`, tetapi chk_quotes_status tidak memuat
-- 'void' -> setiap percobaan melanggar constraint:
--   new row for relation "quotes" violates check constraint "chk_quotes_status"
-- Kelas drift kode-vs-skema yang sama dengan `bank_deleted_at` (2026-07-25):
-- kode merujuk nilai yang skema tak pernah punya.
--
-- ARAH: menambah 'void' ke constraint, BUKAN mengubah kode ke status lain.
-- Alasannya terukur, bukan selera: frontend SUDAH menunggu status ini —
-- `QuoteListDesktop.tsx:76` punya pil filter berlabel "Void" dan `:234`
-- menyaring `(r.status as string) === 'void'`. Mengganti semantik status
-- akan mematikan pil yang sudah dipakai.
--
-- Daftar di bawah DISALIN PERSIS dari pg_get_constraintdef(oid) constraint
-- yang sedang berjalan (diukur 2026-09-03), ditambah 'void' di akhir --
-- bukan diketik ulang dari ingatan.
--
-- Nol dampak akuntansi: quote BUKAN dokumen jurnal. Terukur: nol baris
-- journal_entries yang source_id-nya sebuah quote, dan nol source_type yang
-- menyebut quote. Migrasi ini hanya melebarkan nilai yang sah.

ALTER TABLE quotes DROP CONSTRAINT IF EXISTS chk_quotes_status;

ALTER TABLE quotes ADD CONSTRAINT chk_quotes_status
    CHECK (status::text = ANY (ARRAY[
        'draft'::character varying,
        'sent'::character varying,
        'viewed'::character varying,
        'accepted'::character varying,
        'declined'::character varying,
        'expired'::character varying,
        'converted'::character varying,
        'void'::character varying
    ]::text[]));
