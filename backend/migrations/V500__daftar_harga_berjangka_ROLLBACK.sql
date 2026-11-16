-- ROLLBACK V500: kembali ke kunci unik tanpa tanggal mulai. HANYA aman bila tak ada dua baris berjangka pada kunci (daftar, barang, satuan, min qty) yang sama;
-- bila ada, CREATE UNIQUE INDEX gagal -> hapus/gabung baris berjangka itu dulu. Kembalikan KODE BUG-245 (HARGA_GANDA per tanggal) SEBELUM menjalankan berkas ini.
DROP INDEX IF EXISTS uq_price_list_items_berjangka;
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_list_items_satuan ON price_list_items
    (price_list_id, item_id, lower(btrim(COALESCE(unit, ''))), min_quantity);
