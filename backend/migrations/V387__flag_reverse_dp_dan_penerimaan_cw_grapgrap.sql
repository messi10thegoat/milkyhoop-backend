-- V387 (5 Okt 2026; izin LANGSUNG pemilik di sesi BACKEND2 lewat AskUserQuestion: "Ya, nyalakan keduanya"; WORKSPACE meminta sesudah
-- r212 live) -- flag CW untuk GRAPGRAP: Uang Muka halaman lepas penerapan (reverse_page_dp) + Penerimaan (workspace, detail, panel
-- Dok., halaman batal, form: _rp). kaos sudah memilikinya (V382, V385). Dibaca /permissions/me -> nol kode, nol restart.
-- Idempoten; gagal keras bila tak mendarat ATAU tenant selain kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_reverse_page_dp', 'conversational_workspace_rp',
       'conversational_detail_rp', 'conversational_doc_panel_rp', 'conversational_cancel_page_rp',
       'conversational_form_rp']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_reverse_page_dp', 'conversational_workspace_rp', 'conversational_detail_rp',
                          'conversational_doc_panel_rp', 'conversational_cancel_page_rp', 'conversational_form_rp'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = ANY(f)) <> 6 THEN
        RAISE EXCEPTION 'V387: flag reverse_dp/Penerimaan CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V387: flag reverse_dp/Penerimaan CW ditemukan di tenant selain kaos/grapgrap';
    END IF;
END $$;
