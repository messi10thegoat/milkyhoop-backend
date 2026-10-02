-- V361 (2 Okt 2026, pemilik via MASTER: kode order GENERIK untuk usaha apa pun) -- label kode per tenant
-- ("Kode order" / "No. Job" / "No. Produksi" / "Kode Proyek") dipakai UI + PDF + /documents; bawaan digit minimal
-- tenant baru 3 -> 4. ADITIF: kolom ber-DEFAULT, tak ada perilaku yang berubah untuk baris yang sudah ada.

ALTER TABLE order_code_settings ADD COLUMN IF NOT EXISTS label varchar(30) NOT NULL DEFAULT 'Kode order';
ALTER TABLE order_code_settings DROP CONSTRAINT IF EXISTS chk_ocs_label;
ALTER TABLE order_code_settings ADD CONSTRAINT chk_ocs_label CHECK (length(btrim(label)) BETWEEN 1 AND 30);
ALTER TABLE order_code_settings ALTER COLUMN min_digits SET DEFAULT 4;

-- Putusan pemilik LANGSUNG 2 Okt (murni SAP/NetSuite): kode terbit saat SO DIKONFIRMASI = satu-satunya pemicu
-- otomatis; first_payment DICABUT. Baris yang ada (kaos) dipindah ke so_confirmed SEBELUM CHECK baru dipasang.
ALTER TABLE order_code_settings DROP CONSTRAINT IF EXISTS order_code_settings_trigger_check;
UPDATE order_code_settings SET trigger = 'so_confirmed', updated_at = now() WHERE trigger = 'first_payment';
ALTER TABLE order_code_settings DROP CONSTRAINT IF EXISTS chk_ocs_trigger;
ALTER TABLE order_code_settings ADD CONSTRAINT chk_ocs_trigger CHECK (trigger IN ('so_confirmed', 'manual_only'));
ALTER TABLE order_code_settings ALTER COLUMN trigger SET DEFAULT 'so_confirmed';
