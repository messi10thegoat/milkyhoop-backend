-- Rollback V390: buang kolom title_label (label judul kembali teks tetap "Judul order" di kode lama).
ALTER TABLE order_code_settings DROP COLUMN IF EXISTS title_label;
