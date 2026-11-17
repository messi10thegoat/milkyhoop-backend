-- ROLLBACK V503: buang kolom gudang-per-baris dan sumber pemindahan di baris penyesuaian. Kembalikan KODE BUG-261 ke sebelum V503 SEBELUM berkas ini dijalankan.
-- Dokumen penyesuaian yang sudah POSTED tetap utuh (jurnal + inventory_ledger menyimpan gudangnya sendiri), tetapi tautan ke Pemindahan HILANG: "tertunda" pemindahan
-- kembali = dikirim - diterima (selisih yang pernah diselesaikan muncul lagi sebagai tertunda); koreksi dengan Terima/penyesuaian baru bila perlu.
DROP INDEX IF EXISTS idx_sai_source_transfer;
ALTER TABLE stock_adjustment_items DROP COLUMN IF EXISTS source_transfer_id;
ALTER TABLE stock_adjustment_items DROP COLUMN IF EXISTS warehouse_id;
