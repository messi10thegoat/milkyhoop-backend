-- V406 (MASTER 11 Okt 2026) -- flag cf_submode KAOS SAJA: "Sub-mode Master Data" di Conversational Form Penawaran
-- (WORKSPACE feat/cf-submode 50785e8fb; spek ~/cw-docs/sub-mode, putusan pemilik K1-K13 11 Okt). Menggantikan tampilan
-- panel F2/F3 di Penawaran bila ON. Tanpa rilis FE = tanpa efek. grapgrap & tenant lain WAJIB NOL.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'cf_submode'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature = 'cf_submode') THEN
        RAISE EXCEPTION 'V406: cf_submode kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'cf_submode' AND tenant_id <> 'kaos-biru-konveksi') THEN
        RAISE EXCEPTION 'V406: cf_submode bocor ke tenant lain (grapgrap wajib NOL)';
    END IF;
END $$;
