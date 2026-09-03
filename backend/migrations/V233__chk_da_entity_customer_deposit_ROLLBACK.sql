-- ROLLBACK V233: kembalikan chk_da_entity ke 22 nilai (tanpa
-- 'customer_deposit').
--
-- ⚠️ PRASYARAT: hapus dulu setiap baris lampiran deposit, kalau tidak
-- ALTER TABLE ... ADD CONSTRAINT akan GAGAL memvalidasi baris yang sudah ada:
--     ERROR: check constraint "chk_da_entity" is violated by some row
-- Itu perilaku yang BENAR — rollback harus menolak membuang data diam-diam.
-- Periksa dulu:
--     SELECT count(*) FROM document_attachments
--      WHERE entity_type = 'customer_deposit';
-- Kalau > 0, putuskan secara sadar: hapus tautannya (baris di bawah, yang
-- SENGAJA dikomentari) atau batalkan rollback ini.
--
-- DELETE FROM document_attachments WHERE entity_type = 'customer_deposit';
--
-- Baris `documents` yang bersangkutan TIDAK ikut terhapus oleh perintah di
-- atas; ia tetap tersimpan (dan berkasnya tetap di MinIO). Itu disengaja:
-- `documents` adalah arsip kanonik dengan kewajiban retensi 10 tahun
-- (UU KUP), jadi rollback skema tidak boleh memusnahkannya.

ALTER TABLE document_attachments DROP CONSTRAINT IF EXISTS chk_da_entity;

ALTER TABLE document_attachments ADD CONSTRAINT chk_da_entity CHECK (
    (entity_type)::text = ANY (ARRAY[
        'sales_invoice'::text,
        'bill'::text,
        'expense'::text,
        'customer'::text,
        'vendor'::text,
        'item'::text,
        'journal'::text,
        'quote'::text,
        'purchase_order'::text,
        'sales_order'::text,
        'sales_receipt'::text,
        'payment'::text,
        'credit_note'::text,
        'vendor_credit'::text,
        'stock_adjustment'::text,
        'stock_transfer'::text,
        'employee'::text,
        'asset'::text,
        'project'::text,
        'contract'::text,
        'chat_message'::text,
        'other'::text
    ])
);
