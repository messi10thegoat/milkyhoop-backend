-- V416 (BACKEND2 12 Okt 2026; R1 irisan 1) -- flag `pengiriman_sebelum_faktur` KAOS SAJA. Syarat: V415 + kode SJ-so hidup.
-- grapgrap & tenant lain WAJIB NOL. Jalankan dengan -v ON_ERROR_STOP=1 -1.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'pengiriman_sebelum_faktur'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'pengiriman_sebelum_faktur') THEN
        RAISE EXCEPTION 'V416: pengiriman_sebelum_faktur kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'pengiriman_sebelum_faktur' AND tenant_id <> 'kaos-biru-konveksi') THEN
        RAISE EXCEPTION 'V416: pengiriman_sebelum_faktur bocor ke tenant lain';
    END IF;
END $$;
