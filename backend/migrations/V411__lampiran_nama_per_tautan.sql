-- V411 (12 Okt 2026, BACKEND; temuan WORKSPACE uji nyata kaos, disetujui MASTER) -- NAMA lampiran per TAUTAN.
-- Lampiran SO/Surat Jalan dideduplikasi per ISI (documents.checksum_sha256) -> unggahan ulang berisi sama dengan nama baru
-- tampil dengan nama dokumen LAMA. Pola NetSuite/SAP: dedup penyimpanan boleh, NAMA milik tautan. Aditif, nullable, tanpa
-- backfill (nama unggahan lama tak pernah disimpan; NULL = cadangan ke documents.file_name). MIGRASI DULU, kode menyusul
-- (kode lama tak membaca/menulis kolom ini).
ALTER TABLE document_attachments ADD COLUMN IF NOT EXISTS display_name varchar(255);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_attachments' AND column_name = 'display_name'
                      AND is_nullable = 'YES' AND character_maximum_length = 255) THEN
        RAISE EXCEPTION 'V411: kolom document_attachments.display_name varchar(255) nullable tidak terpasang';
    END IF;
END $$;
