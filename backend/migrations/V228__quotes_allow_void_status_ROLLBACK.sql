-- ROLLBACK V228: kembalikan chk_quotes_status ke daftar TANPA 'void'.
--
-- PERINGATAN: rollback GAGAL selama masih ada baris quotes ber-status 'void'
-- (constraint lama akan menolaknya). Itu disengaja — jangan diakali dengan
-- NOT VALID. Netralkan dulu barisnya lewat jalur normal, atau jalankan
-- pemetaan di bawah secara sadar sesudah menimbang artinya.
--
-- Baris pemetaan sengaja DIKOMENTARI: mengubah 'void' -> 'declined' MENGUBAH
-- ARTI dokumen (dibatalkan penerbit vs ditolak pelanggan) dan akan terbaca di
-- laporan. Buka komentarnya hanya bila kamu memang menerima akibat itu.
--
-- UPDATE quotes SET status = 'declined' WHERE status = 'void';

ALTER TABLE quotes DROP CONSTRAINT IF EXISTS chk_quotes_status;

ALTER TABLE quotes ADD CONSTRAINT chk_quotes_status
    CHECK (status::text = ANY (ARRAY[
        'draft'::character varying,
        'sent'::character varying,
        'viewed'::character varying,
        'accepted'::character varying,
        'declined'::character varying,
        'expired'::character varying,
        'converted'::character varying
    ]::text[]));

DELETE FROM schema_migrations WHERE version = 'V228__quotes_allow_void_status.sql';
