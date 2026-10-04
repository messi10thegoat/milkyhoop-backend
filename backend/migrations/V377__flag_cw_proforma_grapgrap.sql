-- V377 (MASTER 4 Okt 2026; WAJIB izin LANGSUNG pemilik di sesi BACKEND) -- flag Proforma CW U2 untuk GRAPGRAP: daftar, detail, panel Dok., halaman
-- batal, form. Uji nyata kaos U2 lulus (baca identik; tulis PRO-2610-0004/0005 + batal PRO-2610-0003; kunci /issue terbukti);
-- r209 live. Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat.
-- (kaos sudah memilikinya sejak V376).

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_workspace_pf', 'conversational_detail_pf',
       'conversational_doc_panel_pf', 'conversational_cancel_page_pf', 'conversational_form_pf']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_workspace_pf', 'conversational_detail_pf', 'conversational_doc_panel_pf',
                          'conversational_cancel_page_pf', 'conversational_form_pf'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = ANY(f)) <> 5 THEN
        RAISE EXCEPTION 'V377: flag Proforma CW grapgrap tidak mendarat/aktif';
    END IF;
END $$;
