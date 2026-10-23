-- V453 (BACKEND2, disiapkan 23 Okt 2026; BELUM diizinkan) -- flag UI dashboard T2 (pembanding/tren/umur) untuk kaos-biru-konveksi SAJA.
-- Nama flag = usulan FRONTEND (RANCANGAN-dashboard-T2 §9) -- WAJIB dikonfirmasi FRONTEND sebelum apply. grapgrap TIDAK.
-- Prasyarat: gerbang FE T2 LULUS + izin LANGSUNG pemilik. Efek buku: NOL (BE /v2/summary sudah mengirim blok T2 untuk semua tenant).
-- Rollback: V453_ROLLBACK. Jalankan -v ON_ERROR_STOP=1 -1 (apply_mig.sh). Dibaca per-request -> tanpa restart.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM (VALUES ('dashboard_v2_insight')) v(f)
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
        AND feature IN ('dashboard_v2_insight')) <> 1 THEN
        RAISE EXCEPTION 'V453: flag kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature IN ('dashboard_v2_insight')) THEN
        RAISE EXCEPTION 'V453: flag bocor ke tenant lain';
    END IF;
END $$;
