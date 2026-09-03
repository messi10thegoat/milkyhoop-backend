-- ROLLBACK V234: buang kolom kedaluwarsa document_context.
--
-- AMAN: kolom ini murni tambahan, nol backfill, nol constraint, dan tak ada
-- kode LAMA yang membacanya. Menghapusnya mengembalikan perilaku pra-V234 —
-- yaitu konteks dokumen yang TIDAK PERNAH kedaluwarsa (bug yang V234 tutup).
--
-- ⚠️ Jangan jalankan selagi kode pasca-V234 masih hidup: `session_manager`
-- membaca `row.get("document_context_expires_at")` dan menulisnya di setiap
-- update konteks. Tanpa kolomnya, penulisan gagal (`column ... does not
-- exist`). Urutan yang benar: rollback KODE dulu, baru kolomnya.

ALTER TABLE chat_session_state
    DROP COLUMN IF EXISTS document_context_expires_at;
