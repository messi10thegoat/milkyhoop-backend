-- V481 (BACKEND2 6 Nov 2026; terapkan HANYA sesudah konfirmasi LANGSUNG pemilik di sesi BACKEND2) -- flag CW Impor barang (BUG-021)
-- conversational_impor_barang untuk kaos-biru-konveksi SAJA (uji). Bawaan = MATI: tanpa baris tenant_features = mati (FE useFeature).
-- nyawit = migrasi TERPISAH sesudah kaos lulus. Nama flag = FE f4036b0a5 (DashboardSidebar/useFeature).
-- Efek buku: NOL jurnal (flag hanya membuka menu FE; rute /api/items/impor* hidup dan berizin item C). Dibaca per-request -> tanpa restart.
-- Rollback: V481_ROLLBACK. Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_impor_barang'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
                   AND feature = 'conversational_impor_barang') THEN
        RAISE EXCEPTION 'V481: flag impor barang kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_impor_barang') THEN
        RAISE EXCEPTION 'V481: flag impor barang bocor ke tenant lain';
    END IF;
END $$;
