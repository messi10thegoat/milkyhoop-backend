-- Rollback V428: KODE dulu dikembalikan (kode tahap 1 membaca/menulis customer_addresses), baru tabel dihapus.
-- Kolom datar customers tak pernah diubah oleh V428 -> alamat lama utuh. Alamat TAMBAHAN yang dibuat pengguna
-- sesudah V428 (selain bawaan tagih yang tercermin di kolom datar) HILANG -- ekspor dulu bila ada isinya.
ALTER TABLE sales_orders DROP COLUMN IF EXISTS shipping_address_id;
DROP TABLE IF EXISTS customer_addresses;
DROP FUNCTION IF EXISTS ca_tenant_sama();
