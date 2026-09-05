-- V238: nomor PO pelanggan + nomor surat jalan pada faktur penjualan.
--
-- MENGAPA BUKAN DISISIPKAN KE V237: V237 sudah DITERAPKAN dan TERCATAT di
-- milkydb (checksum bcdbc4bac585..., 2026-09-05 04:44 UTC). Mengedit berkas
-- migrasi yang sudah mendarat membuat checksum tercatat tak cocok dengan isi
-- berkas -- runner fresh-install akan menolak, dan itu jalur pemulihan.
--
-- MENGAPA BUKAN MEMAKAI ULANG ref_no: `ref_no` dipakai 8 berkas termasuk
-- jalur chat (tool_executor / direct_action_registry) sebagai "No. Order"
-- teks bebas. Mengubah artinya = regresi bot tanpa suara.
--
-- Keduanya TEKS BEBAS dan NULL-able: nomor PO diterbitkan oleh PELANGGAN
-- (kita tak pernah bisa menghasilkannya), dan surat jalan di sistem ini lahir
-- SESUDAH faktur (invoice_fulfillments.invoice_id -> faktur), jadi saat faktur
-- dibuat nomor DO memang belum ada.

ALTER TABLE sales_invoices
    ADD COLUMN IF NOT EXISTS purchase_order_no text,
    ADD COLUMN IF NOT EXISTS delivery_order_no text;

COMMENT ON COLUMN sales_invoices.purchase_order_no IS
    'Nomor PO milik pelanggan (teks bebas, diterbitkan pihak lain). Bukan tautan.';
COMMENT ON COLUMN sales_invoices.delivery_order_no IS
    'Nomor surat jalan yang DITULIS pengguna. Kalau kosong, cetakan memakai '
    'invoice_fulfillments.fulfillment_number terakhir yang belum void -- '
    'diturunkan saat cetak, tidak disalin ke kolom ini.';
