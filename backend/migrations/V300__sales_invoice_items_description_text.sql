-- V300 (Unit D): keterangan baris faktur = text, sama dengan sales_order_items / quote_items.
-- Dulu varchar(255) sementara SO/Penawaran menerima 500 karakter: konversi SO/Penawaran -> Faktur
-- dengan keterangan > 255 gagal (string data right truncation). varchar -> text tanpa tulis ulang tabel.
-- Tak ada view/rule yang bergantung pada kolom ini (pg_depend diukur 23 Sep 2026, prod + scratch).
ALTER TABLE sales_invoice_items ALTER COLUMN description TYPE text;
