-- V336 (MASTER/WORKSPACE 30 Sep 2026) — flag `conversational_detail_si` (halaman CW detail faktur penjualan) KAOS SAJA (e2e).
-- grapgrap TIDAK: menyala hanya atas putusan pemilik (satu migrasi/UPDATE terpisah).
--
-- Dibaca GET /api/permissions/me -> `features` = SEMUA flag enabled tenant (services/tenant_features
-- fitur_aktif; TIDAK ada daftar-izin di kode -> nol perubahan kode, nol restart).
-- Pola V322: idempoten (ON CONFLICT DO NOTHING), gagal keras bila seed tak mendarat ATAU bila
-- tenant lain ikut memilikinya. Aditif: satu baris baru.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_detail_si'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_detail_si' AND enabled) THEN
        RAISE EXCEPTION 'V336: flag conversational_detail_si kaos-biru-konveksi tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_detail_si') THEN
        RAISE EXCEPTION 'V336: flag conversational_detail_si ditemukan di tenant lain';
    END IF;
END $$;
