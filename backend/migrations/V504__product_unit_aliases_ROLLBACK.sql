-- ROLLBACK V504: buang tabel alias satuan. Kembalikan KODE ke sebelum V504 (satuan_dasar._alias membaca tabel ini) SEBELUM menjalankan berkas ini. Alias yang tercatat HILANG:
-- sesudahnya dokumen lama yang masih memakai nama LAMA satuan yang sudah diganti-nama/digabung tak dikenali lagi sebagai satuan itu (SATUAN_TAK_TERKONVERSI) -- ekspor
-- `SELECT * FROM product_unit_aliases` dulu bila perlu.
DROP TABLE IF EXISTS product_unit_aliases;
