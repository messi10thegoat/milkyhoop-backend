-- Pembalik V515. Aman selama belum ada alokasi tersimpan (flag/FE belum memakai).
DROP TABLE IF EXISTS sales_invoice_item_batches;
DELETE FROM schema_migrations WHERE version = 'V515__alokasi_batch_baris_faktur.sql';
