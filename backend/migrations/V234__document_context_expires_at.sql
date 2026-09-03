-- V234: TTL untuk chat_session_state.document_context.
--
-- MASALAH TERUKUR: `document_context` (Layer 2) adalah SATU-SATUNYA konteks
-- sesi tanpa kedaluwarsa. Dua saudaranya sudah punya kolom sendiri —
-- `current_period_expires_at` dan `pending_clarification_expires_at` — dan
-- konteks dokumen tidak. Ia hanya dihapus saat dokumen dikonfirmasi.
--
-- Akibatnya: sesi yang meninggalkan kartu dokumen tanpa konfirmasi menahan
-- konteks itu SELAMANYA, dan selama konteks hidup system prompt menyuntikkan
-- steer "koreksi -> update_document_context". Terukur pada sesi owner
-- ff4b5c92…: konteks berumur >24 jam membajak pesan biasa — "Sharon Vanesa,
-- coba cek lagi" dijawab "Vendor diubah menjadi Sharon Vanesa", padahal user
-- sedang mencari PELANGGAN.
--
-- KENAPA KOLOM, BUKAN FIELD DI DALAM JSON: diukur — kedua TTL yang sudah ada
-- di tabel ini adalah KOLOM `timestamptz`, bukan field JSON, dan dibaca lewat
-- `row.get(...)` di `session_manager`. Mengikuti pola yang ada; nol pola baru.
--
-- NULL berarti "tak pernah kedaluwarsa" sehingga baris LAMA tidak berubah
-- artinya oleh migrasi ini SENDIRI. Penegakan ada di kode: pembaca
-- memperlakukan konteks yang `expires_at`-nya sudah lewat sebagai TIDAK ADA,
-- dan setiap penulisan konteks memasang cap baru (30 menit). Baris basi yang
-- sudah terlanjur ada (expires_at NULL) dibereskan oleh penulisan berikutnya;
-- ia tidak dihapus paksa oleh migrasi — DDL tidak boleh menyentuh data sesi.

ALTER TABLE chat_session_state
    ADD COLUMN IF NOT EXISTS document_context_expires_at timestamptz;

COMMENT ON COLUMN chat_session_state.document_context_expires_at IS
    'Kedaluwarsa document_context (V234). NULL = tak pernah kedaluwarsa '
    '(baris pra-V234). Pembaca WAJIB memperlakukan konteks yang sudah lewat '
    'sebagai tidak ada; jangan hapus barisnya.';
