-- V358 (1 Okt 2026, pemilik LANGSUNG di sesi BACKEND: "Ya, nyalakan sekarang" atas Kirim grapgrap; juga instruksi
-- pemilik di sesi FRONTEND "semua optimasi rilis terbaru dibikin flag on di grapgrap juga") -- flag
-- `conversational_doc_send_so` (kartu Kirim di panel dokumen SO: wa.me / salin tautan / mailto lewat tautan publik
-- /d/{token}) untuk GRAPGRAP. Bukti: gerbang P5 lulus + uji nyata kaos 2x (r194 & r195, snapshot DB cocok persis).
--
-- Dibaca GET /api/permissions/me -> `features` -> nol perubahan kode, nol restart. Idempoten; gagal keras bila tak
-- mendarat ATAU tenant selain kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_doc_send_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_doc_send_so' AND enabled) THEN
        RAISE EXCEPTION 'V358: flag conversational_doc_send_so grapgrap-manado tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = 'conversational_doc_send_so') THEN
        RAISE EXCEPTION 'V358: flag conversational_doc_send_so ditemukan di tenant lain';
    END IF;
END $$;
