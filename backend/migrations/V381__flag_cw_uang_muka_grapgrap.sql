-- V381 (4/5 Okt 2026; izin LANGSUNG pemilik di sesi BACKEND: "Semua 7 termasuk Refund") -- flag Uang Muka CW U3a untuk
-- GRAPGRAP: daftar, detail, panel Dok., form, halaman void/refund/terapkan. r210 (main.f87afa32) LIVE; uji nyata kaos
-- void/apply/refund lulus (WORKSPACE). kaos sudah memilikinya sejak V378. Dibaca /permissions/me -> nol kode, nol restart.
-- Idempoten; gagal keras bila tak mendarat ATAU tenant selain kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_workspace_dp', 'conversational_detail_dp',
       'conversational_doc_panel_dp', 'conversational_form_dp', 'conversational_void_page_dp',
       'conversational_refund_page_dp', 'conversational_apply_page_dp']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_workspace_dp', 'conversational_detail_dp', 'conversational_doc_panel_dp',
                          'conversational_form_dp', 'conversational_void_page_dp', 'conversational_refund_page_dp',
                          'conversational_apply_page_dp'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = ANY(f)) <> 7 THEN
        RAISE EXCEPTION 'V381: flag Uang Muka CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V381: flag Uang Muka CW ditemukan di tenant lain';
    END IF;
END $$;
