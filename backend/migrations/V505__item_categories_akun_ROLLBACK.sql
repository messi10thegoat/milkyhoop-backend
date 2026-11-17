-- Pembalik V505 (kolom akun kategori; data akun kategori hilang, barang & jurnal tak tersentuh).
ALTER TABLE item_categories DROP COLUMN IF EXISTS purchase_account_id;
ALTER TABLE item_categories DROP COLUMN IF EXISTS cogs_account_id;
ALTER TABLE item_categories DROP COLUMN IF EXISTS inventory_account_id;
ALTER TABLE item_categories DROP COLUMN IF EXISTS sales_account_id;
