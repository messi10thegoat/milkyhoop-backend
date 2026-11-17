-- V504: Satuan G3-b (17 Nov 2026) -- tabel ALIAS satuan: nama lama sebuah satuan (sesudah ganti nama / gabung) tetap dikenali sebagai satuan itu, sehingga dokumen & kartu stok lama
-- (yang menyimpan TEKS satuan saat transaksi) tetap terbaca lewat kamus alias services/satuan_dasar. Tabel BARU, ADITIF: tanpa ubah tabel lama, tanpa backfill.
-- Unik per tenant atas lower(btrim(alias)); unit_id -> product_units (alias ikut terhapus bila baris satuan dihapus keras; hapus lunak = nonaktif tak menyentuh alias).
-- DIIZINKAN LANGSUNG pemilik (17 Nov 21:35 WIB, via MASTER). Pembalik: V504__product_unit_aliases_ROLLBACK.sql. Tanpa BEGIN/COMMIT.
CREATE TABLE IF NOT EXISTS product_unit_aliases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    unit_id     UUID NOT NULL REFERENCES product_units(id) ON DELETE CASCADE,
    alias       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_product_unit_aliases_tenant_alias ON product_unit_aliases (tenant_id, lower(btrim(alias)));
CREATE INDEX IF NOT EXISTS idx_product_unit_aliases_unit ON product_unit_aliases (unit_id);
COMMENT ON TABLE product_unit_aliases IS 'Nama/singkatan LAMA satuan (setelah ganti nama atau gabung): teks yang masih tertulis di dokumen/ledger lama tetap = satuan unit_id (kamus satuan_dasar).';
