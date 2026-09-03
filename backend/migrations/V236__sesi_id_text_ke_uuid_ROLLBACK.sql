-- ROLLBACK V236: kembalikan kedua kolom id sesi ke `text`.
--
-- `uuid -> text` selalu berhasil dan tak pernah kehilangan data; bentuk yang
-- keluar adalah kanonik huruf kecil.
--
-- YANG TIDAK DIPULIHKAN: string kosong yang dijadikan NULL oleh langkah maju.
-- Nol baris saat V236 diterapkan (3 Sep 2026), jadi biayanya nol saat itu.
-- Kalau rollback dijalankan jauh di kemudian hari, ukur ulang lebih dulu.
--
-- Juga tidak dipulihkan: nilai berhuruf KAPITAL atau tanpa tanda hubung yang
-- pernah ada sebelum V236. Itu memang tujuannya -- bentuk tak baku itulah yang
-- membuat 153 baris terbaca yatim pada 29-30 Agustus 2026.

BEGIN;

ALTER TABLE pending_actions
  ALTER COLUMN conversation_id TYPE text USING conversation_id::text;

ALTER TABLE chat_workflow_state
  ALTER COLUMN chat_session_id TYPE text USING chat_session_id::text;

COMMIT;
