-- V425 (BACKEND2 13 Okt 2026; W-20261013-R1-GRAPGRAP, putusan MASTER) -- flag `pengiriman_sebelum_faktur` untuk grapgrap-manado.
-- SATU flag (non-stok). `pengiriman_stok_sebelum_faktur` TIDAK dinyalakan: grapgrap 0 barang ber-stok (semua non_inventory, putusan pemilik).
-- Efek buku: NOL — SJ non-stok tak membuat jurnal maupun kartu stok (R1 irisan 1); Barang Terkirim 1-10660 tetap 0.
-- Prasyarat terukur 13 Okt: V415/V417 live, akun 1-10660 + peran GOODS_DELIVERED_NOT_INVOICED ada, 0 SJ asal 'so'.
-- Dibaca per-request dari tenant_features (tanpa cache) -> tanpa restart. Rollback: V425_ROLLBACK. Jalankan -v ON_ERROR_STOP=1 -1.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'pengiriman_sebelum_faktur'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = 'pengiriman_sebelum_faktur') THEN
        RAISE EXCEPTION 'V425: pengiriman_sebelum_faktur grapgrap tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'pengiriman_stok_sebelum_faktur') THEN
        RAISE EXCEPTION 'V425: pengiriman_stok_sebelum_faktur TIDAK boleh ada di grapgrap (0 barang ber-stok)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM account_roles WHERE tenant_id = 'grapgrap-manado' AND role_key = 'GOODS_DELIVERED_NOT_INVOICED') THEN
        RAISE EXCEPTION 'V425: peran GOODS_DELIVERED_NOT_INVOICED grapgrap belum ada (V417)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'pengiriman_sebelum_faktur' AND enabled
               AND tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')) THEN
        RAISE EXCEPTION 'V425: pengiriman_sebelum_faktur bocor ke tenant lain';
    END IF;
END $$;
