-- V451 (BACKEND2 23 Okt 2026; izin LANGSUNG pemilik di sesi BACKEND2) -- flag UI Uang Muka Pembelian seragam untuk kaos-biru-konveksi SAJA:
-- listpage_uang_muka_pembelian_v1 + detailpage_uang_muka_pembelian_v1 (nama dari FE feat/um-pembelian-seragam e886efe02). grapgrap TIDAK.
-- Efek buku: NOL jurnal (flag hanya membuka tampilan FE; endpoint BE vendor-deposits tak dijaga flag). Dibaca per-request -> tanpa restart.
-- Rollback: V451_ROLLBACK. Jalankan -v ON_ERROR_STOP=1 -1.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM (VALUES ('listpage_uang_muka_pembelian_v1'), ('detailpage_uang_muka_pembelian_v1')) v(f)
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
        AND feature IN ('listpage_uang_muka_pembelian_v1', 'detailpage_uang_muka_pembelian_v1')) <> 2 THEN
        RAISE EXCEPTION 'V451: flag UM pembelian kaos tidak terpasang/aktif dua-duanya';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi'
               AND feature IN ('listpage_uang_muka_pembelian_v1', 'detailpage_uang_muka_pembelian_v1')) THEN
        RAISE EXCEPTION 'V451: flag UM pembelian bocor ke tenant lain';
    END IF;
END $$;
