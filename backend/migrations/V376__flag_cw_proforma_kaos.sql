-- V376 (WORKSPACE 4 Okt 2026) -- flag Proforma CW U2 KAOS SAJA untuk uji nyata: daftar, detail, panel Dok., halaman
-- batal, form. BE U2 live (60cfafff + 53196502). grapgrap TIDAK (aturan tetap pemilik sesudah uji nyata kaos lulus;
-- migrasi terpisah). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU
-- tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_workspace_pf', 'conversational_detail_pf',
       'conversational_doc_panel_pf', 'conversational_cancel_page_pf', 'conversational_form_pf']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_workspace_pf', 'conversational_detail_pf', 'conversational_doc_panel_pf',
                          'conversational_cancel_page_pf', 'conversational_form_pf'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = ANY(f)) <> 5 THEN
        RAISE EXCEPTION 'V376: flag Proforma CW kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V376: flag Proforma CW ditemukan di tenant lain';
    END IF;
END $$;
