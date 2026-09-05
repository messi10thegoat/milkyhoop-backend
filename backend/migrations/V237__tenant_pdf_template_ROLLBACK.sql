-- ROLLBACK V237: cabut pilihan template PDF.
--
-- Aman: kolomnya punya DEFAULT dan tak dipakai sebagai kunci apa pun.
-- YANG HILANG: pilihan template tiap tenant. Sesudah rollback, semua faktur
-- kembali memakai template A, dan tenant yang sudah memilih B harus memilih
-- ulang. Tak ada data dokumen yang terpengaruh -- pilihan ini hanya
-- menentukan TAMPILAN saat cetak, bukan isi faktur.
BEGIN;
ALTER TABLE "Tenant" DROP CONSTRAINT IF EXISTS tenant_pdf_template_check;
ALTER TABLE "Tenant" DROP COLUMN IF EXISTS pdf_template;
COMMIT;
