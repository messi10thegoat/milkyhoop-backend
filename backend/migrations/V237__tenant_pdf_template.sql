-- V237: pilihan template PDF per tenant.
--
-- LATAR
-- Pemilik ingin bisa memilih tampilan faktur (A = tampilan yang ada, B = gaya
-- faktur industri klasik). Diukur 5 Sep 2026: TIDAK ADA konsep template/gaya/
-- varian di mana pun -- nol kolom `template`/`layout`/`pdf_*` di `Tenant`,
-- `accounting_settings`, maupun tabel dokumen. (`recurring_invoices.
-- template_name` dan `recurring_bills.template_name` yang muncul saat menyisir
-- adalah NAMA JADWAL BERULANG, bukan template cetak, dan ketiganya 0 baris.)
--
-- KENAPA DI TENANT, BUKAN DI DOKUMEN
-- Bawaan milik perusahaan, jadi tempatnya di tenant: satu kolom, nol migrasi
-- pada 7 tabel dokumen. Tapi bawaan saja TIDAK CUKUP -- kalau pilihan hanya
-- ada di sini, pengguna yang mengganti gaya tak bisa lagi mencetak ulang
-- faktur lama dengan tampilan aslinya, padahal faktur adalah dokumen yang
-- SUDAH DIKIRIM ke pelanggan. Karena itu endpoint cetak menerima `?template=`
-- yang MENANG atas bawaan ini.
--
-- Kalau kelak "faktur harus tercetak dengan template saat diterbitkan" jadi
-- kebutuhan sungguhan, kolom per-dokumen bisa ditambahkan DI ATAS bentuk ini
-- tanpa membuang apa pun.
--
-- ⚠️ NPWP TIDAK DITAMBAHKAN. Brief meminta kolom `npwp`, tapi pengukuran
-- menemukan `Tenant.tax_id` SUDAH ADA dan sudah dibaca/ditulis endpoint
-- `/api/tenant/profile` (tenant_profile.py:44/73/120). Menambah `npwp` akan
-- membuat DUA SUMBER KEBENARAN untuk satu fakta -- kelas kesalahan yang sama
-- dengan `User."tenantId"` vs `user_tenant_roles` yang membuat login menjawab
-- 409 walau baris perannya benar. Template B memakai `tax_id`.

BEGIN;

ALTER TABLE "Tenant"
  ADD COLUMN IF NOT EXISTS pdf_template text NOT NULL DEFAULT 'a';

-- Nilai di luar 'a'/'b' ditolak DI BASIS DATA, bukan hanya di aplikasi:
-- resolver di pdf_service menolak dengan 422, tapi kolomnya tetap harus tak
-- bisa menampung nilai yang tak punya template.
ALTER TABLE "Tenant"
  DROP CONSTRAINT IF EXISTS tenant_pdf_template_check;
ALTER TABLE "Tenant"
  ADD CONSTRAINT tenant_pdf_template_check CHECK (pdf_template IN ('a', 'b'));

COMMIT;
