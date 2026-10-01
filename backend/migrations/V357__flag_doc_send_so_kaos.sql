-- V357 (WORKSPACE/MASTER 1 Okt 2026, P5 SO-dokumen) -- flag `conversational_doc_send_so` (kartu Kirim di panel
-- dokumen: wa.me / salin tautan / mailto lewat tautan publik /d/{token}) KAOS SAJA (e2e). grapgrap TIDAK: Kirim tetap
-- mati sampai pemilik memerintahkan (migrasi terpisah).
--
-- Dibaca GET /api/permissions/me -> `features` -> nol perubahan kode, nol restart. Idempoten; gagal keras bila tak
-- mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_doc_send_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'kaos-biru-konveksi' AND feature = 'conversational_doc_send_so' AND enabled) THEN
        RAISE EXCEPTION 'V357: flag conversational_doc_send_so kaos-biru-konveksi tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_doc_send_so') THEN
        RAISE EXCEPTION 'V357: flag conversational_doc_send_so ditemukan di tenant lain';
    END IF;
END $$;
