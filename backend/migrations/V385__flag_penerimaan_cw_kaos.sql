-- V385 (WORKSPACE meminta 5 Okt 2026, U3b lulus gerbang lokal) -- flag Penerimaan CW U3b KAOS SAJA untuk uji nyata:
-- daftar, detail, panel Dok., halaman batal, form. BE U3b + U8 (daftar memuat voided) live.
-- grapgrap TIDAK (migrasi terpisah; izin LANGSUNG pemilik). Dibaca /permissions/me -> nol kode, nol restart.
-- Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_workspace_rp', 'conversational_detail_rp',
       'conversational_doc_panel_rp', 'conversational_cancel_page_rp', 'conversational_form_rp']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_workspace_rp', 'conversational_detail_rp', 'conversational_doc_panel_rp',
                          'conversational_cancel_page_rp', 'conversational_form_rp'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = ANY(f)) <> 5 THEN
        RAISE EXCEPTION 'V385: flag Penerimaan CW kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V385: flag Penerimaan CW ditemukan di tenant lain';
    END IF;
END $$;
