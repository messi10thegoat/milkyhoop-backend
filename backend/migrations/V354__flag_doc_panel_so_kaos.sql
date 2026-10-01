-- V354 (WORKSPACE/MASTER 1 Okt 2026, putusan pemilik #7 P0 SO-dokumen) — flag `conversational_doc_panel_so`
-- (kolom Posisi · Kirim · Dok. + panel dokumen di daftar Pesanan) KAOS SAJA (e2e). grapgrap TIDAK: menyala hanya
-- sesudah gerbang + uji nyata kaos (aturan tetap flag CW), migrasi terpisah.
--
-- Dibaca GET /api/permissions/me -> `features` (services/tenant_features.fitur_aktif; tanpa daftar-izin di kode ->
-- nol perubahan kode, nol restart). Pola V341: idempoten, gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_doc_panel_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_doc_panel_so' AND enabled) THEN
        RAISE EXCEPTION 'V354: flag conversational_doc_panel_so kaos-biru-konveksi tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_doc_panel_so') THEN
        RAISE EXCEPTION 'V354: flag conversational_doc_panel_so ditemukan di tenant lain';
    END IF;
END $$;
