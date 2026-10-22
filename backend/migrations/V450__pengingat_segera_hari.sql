-- V450 (BACKEND2 22 Okt 2026; dashboard T1 SATU SUMBER PENGINGAT) -- psql -v ON_ERROR_STOP=1 -1 (apply_mig.sh).
-- PUTUSAN PEMILIK LANGSUNG 22 Okt: SATU definisi "segera jatuh tempo" = jatuh tempo dalam N hari, bawaan 7, bisa diatur per usaha
-- (pola Xero/QBO). Dibaca HANYA lewat services/pengingat.ambang_segera (faktur, proforma, tagihan, dashboard).
-- Tambah saja (expand): kolom ber-DEFAULT; tenant tanpa baris accounting_settings -> kode memakai bawaan 7. Nol angka buku berubah.
ALTER TABLE accounting_settings
    ADD COLUMN IF NOT EXISTS pengingat_segera_hari integer NOT NULL DEFAULT 7;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_pengingat_segera_hari') THEN
        ALTER TABLE accounting_settings ADD CONSTRAINT chk_pengingat_segera_hari CHECK (pengingat_segera_hari BETWEEN 1 AND 60);
    END IF;
    IF (SELECT count(*) FROM accounting_settings WHERE pengingat_segera_hari <> 7) <> 0 THEN
        RAISE EXCEPTION 'V450: nilai awal harus 7 semua';
    END IF;
END $$;
