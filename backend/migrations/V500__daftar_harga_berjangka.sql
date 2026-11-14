-- V500: BUG-245 (15 Nov 2026) -- harga BERJANGKA pada kunci (barang, satuan, min qty) yang sama dalam satu daftar (pola Accurate: penyesuaian harga jual
-- bertanggal efektif). Indeks unik lama uq_price_list_items_satuan (V474) tak memuat tanggal mulai, jadi kenaikan harga terjadwal ditolak HARGA_GANDA.
-- DISETUJUI LANGSUNG pemilik (15 Nov ~01:20, via MASTER): kunci unik kini memuat COALESCE(start_date, '-infinity'): tanggal mulai kosong = berlaku sejak
-- awal, tetap maksimal satu per kunci. Resolve harga memilih mulai TERBARU <= tanggal dokumen (kode). Tanpa backfill; indeks baru LEBIH LONGGAR dari yang lama,
-- jadi tak ada baris eksisting yang melanggar. Pembalik: V500__daftar_harga_berjangka_ROLLBACK.sql.
DROP INDEX IF EXISTS uq_price_list_items_satuan;
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_list_items_berjangka ON price_list_items
    (price_list_id, item_id, lower(btrim(COALESCE(unit, ''))), min_quantity, COALESCE(start_date, '-infinity'::date));
