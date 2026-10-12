-- V413 (BACKEND2 12 Okt 2026; R2 Pemindahan Barang) -- flag `pemindahan_barang` KAOS SAJA. Syarat: V412 + kode PB sudah hidup.
-- grapgrap & tenant lain WAJIB NOL (grapgrap: 0 produk ber-stok; tenant lain 1 gudang). Jalankan dengan -v ON_ERROR_STOP=1 -1.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'pemindahan_barang'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'pemindahan_barang') THEN
        RAISE EXCEPTION 'V413: pemindahan_barang kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'pemindahan_barang' AND tenant_id <> 'kaos-biru-konveksi') THEN
        RAISE EXCEPTION 'V413: pemindahan_barang bocor ke tenant lain';
    END IF;
END $$;
