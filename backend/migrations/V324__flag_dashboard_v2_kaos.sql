-- V324 (MASTER 28 Sep 2026) — flag `dashboard_v2` (sidebar + dashboard baru, D2) untuk KAOS SAJA (tenant uji).
-- grapgrap TIDAK: menyala hanya sesudah pemilik melihat hasilnya (satu migrasi/UPDATE terpisah).
--
-- Dibaca GET /api/permissions/me -> `features` = SEMUA flag enabled tenant (services/tenant_features
-- fitur_aktif; TIDAK ada daftar-izin di kode -> nol perubahan kode, nol restart).
-- Pola V310: idempoten (ON CONFLICT DO NOTHING), gagal keras bila seed tak mendarat ATAU bila
-- tenant lain ikut memilikinya. Aditif: satu baris baru.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'dashboard_v2'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'dashboard_v2' AND enabled) THEN
        RAISE EXCEPTION 'V324: flag dashboard_v2 kaos-biru-konveksi tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'dashboard_v2') THEN
        RAISE EXCEPTION 'V324: flag dashboard_v2 ditemukan di tenant lain';
    END IF;
END $$;
