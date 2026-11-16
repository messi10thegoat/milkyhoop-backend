-- V498: K-2 (17 Nov 2026) -- master KATEGORI barang standar SO. Putusan pemilik LANGSUNG 14 Nov 2026: "Migrasi aditif item_categories DIIZINKAN = V498";
-- kolom = yang tertulis di rancangan BACKEND2 Q6 SAJA (is_active, is_default, parent_id, description, updated_at). Kolom AKUN per kategori (B1) TIDAK ada di sini
-- (putusan: sesudah BUG-160; migrasi terpisah). ADITIF: semua baris lama = aktif, bukan bawaan, tanpa induk (NOL baris products diubah; products.kategori tetap teks bebas).
-- parent_id = sub kategori SATU tingkat (K-4; kolom disiapkan sekarang); nama anak unik per induk dijaga penulis. Pembalik: V498__item_categories_standar_so_ROLLBACK.sql. Tanpa BEGIN/COMMIT.
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES item_categories(id);
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE item_categories ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
-- Bawaan = SATU per usaha dan harus aktif (DB menjaga; penulis K-3 yang memindahkan bawaan dalam satu transaksi). Induk tak boleh diri sendiri.
CREATE UNIQUE INDEX IF NOT EXISTS uq_item_categories_default ON item_categories (tenant_id) WHERE is_default;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_item_categories_default_aktif') THEN
        ALTER TABLE item_categories ADD CONSTRAINT chk_item_categories_default_aktif CHECK (NOT is_default OR is_active);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_item_categories_bukan_induk_sendiri') THEN
        ALTER TABLE item_categories ADD CONSTRAINT chk_item_categories_bukan_induk_sendiri CHECK (parent_id IS NULL OR parent_id <> id);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_item_categories_parent ON item_categories (parent_id) WHERE parent_id IS NOT NULL;
COMMENT ON COLUMN item_categories.is_active IS 'Kategori aktif = tampil sebagai pilihan baru; nonaktif tetap dibawa barang lamanya (peringatan, bukan blok).';
COMMENT ON COLUMN item_categories.is_default IS 'Kategori bawaan (mis. "Umum") untuk barang baru tanpa pilihan; satu per usaha, harus aktif.';
COMMENT ON COLUMN item_categories.parent_id IS 'Induk (sub kategori satu tingkat; K-4).';
