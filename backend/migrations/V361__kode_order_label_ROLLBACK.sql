-- Rollback V361: label dibuang, bawaan digit 3, CHECK pemicu lama (first_payment kembali diizinkan; DATA pemicu yang
-- sudah dipindah ke so_confirmed TIDAK dikembalikan -- itu putusan pemilik, bukan efek samping).
ALTER TABLE order_code_settings DROP CONSTRAINT IF EXISTS chk_ocs_trigger;
ALTER TABLE order_code_settings ADD CONSTRAINT order_code_settings_trigger_check
    CHECK (trigger IN ('first_payment', 'so_confirmed', 'manual_only'));
ALTER TABLE order_code_settings ALTER COLUMN trigger SET DEFAULT 'first_payment';
ALTER TABLE order_code_settings ALTER COLUMN min_digits SET DEFAULT 3;
ALTER TABLE order_code_settings DROP CONSTRAINT IF EXISTS chk_ocs_label;
ALTER TABLE order_code_settings DROP COLUMN IF EXISTS label;
