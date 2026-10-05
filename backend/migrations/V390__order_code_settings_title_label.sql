-- V390 (MASTER/PEMILIK 5 Okt 2026): label "Judul order" bisa diubah per tenant seperti label kode ("No. SPK" ->
-- pemilik mau "Judul SPK"). Aditif: kolom baru ber-bawaan "Judul order" -> tenant lain & semua teks lama TAK berubah
-- sampai tenant mengganti sendiri lewat pengaturan penomoran. Panjang = kolom label (30); validasi teks di aplikasi
-- (kode_order.normal_label, sama dengan label).
ALTER TABLE order_code_settings ADD COLUMN IF NOT EXISTS title_label VARCHAR(30) NOT NULL DEFAULT 'Judul order';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM order_code_settings WHERE title_label IS DISTINCT FROM 'Judul order') THEN
        RAISE EXCEPTION 'V390: title_label tak berbawaan "Judul order"';
    END IF;
END $$;
