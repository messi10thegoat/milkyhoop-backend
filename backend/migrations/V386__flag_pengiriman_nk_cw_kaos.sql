-- V386 (WORKSPACE meminta 5 Okt 2026, U4 Pengiriman + U5 Nota Kredit lulus gerbang lokal) -- flag CW KAOS SAJA untuk uji nyata:
-- Pengiriman (dl): daftar + detail; Nota Kredit (cn): daftar + detail. BE U4/U5/U9/U10/U11 live.
-- grapgrap TIDAK (migrasi terpisah). Dibaca /permissions/me -> nol kode, nol restart.
-- Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_workspace_dl', 'conversational_detail_dl',
       'conversational_doc_panel_dl', 'conversational_form_dl', 'conversational_workspace_cn',
       'conversational_detail_cn']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_workspace_dl', 'conversational_detail_dl', 'conversational_doc_panel_dl',
                          'conversational_form_dl', 'conversational_workspace_cn', 'conversational_detail_cn'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = ANY(f)) <> 6 THEN
        RAISE EXCEPTION 'V386: flag Pengiriman/NK CW kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V386: flag Pengiriman/NK CW ditemukan di tenant lain';
    END IF;
END $$;
