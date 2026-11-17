-- V505: BUG-282 (18 Nov 2026) -- akun bawaan per KATEGORI barang (penjualan/persediaan/HPP/pembelian), gaya Accurate.
-- Putusan pemilik LANGSUNG 17 Nov 21:15: akun kategori DISALIN ke barang BARU saat dibuat; mengubah akun kategori tak mengubah barang/jurnal lama.
-- Aditif: 4 kolom UUID NULL (NULL = kategori tak punya akun bawaan = perilaku lama). Pembalik: V505__item_categories_akun_ROLLBACK.sql. Tanpa BEGIN/COMMIT.
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS sales_account_id UUID REFERENCES chart_of_accounts(id);
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS inventory_account_id UUID REFERENCES chart_of_accounts(id);
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS cogs_account_id UUID REFERENCES chart_of_accounts(id);
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS purchase_account_id UUID REFERENCES chart_of_accounts(id);
