-- V378 (MASTER 4 Okt 2026; apply DITAHAN sampai WORKSPACE meminta) -- flag Uang Muka CW U3a KAOS SAJA untuk uji nyata:
-- daftar, detail, panel Dok., form, halaman void/refund/terapkan. BE U3a live (de24dcdb + c0e98127 + 59133dbe).
-- grapgrap TIDAK (refund = uang KELUAR; wajib izin LANGSUNG pemilik di sesi BACKEND; migrasi terpisah).
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_workspace_dp', 'conversational_detail_dp',
       'conversational_doc_panel_dp', 'conversational_form_dp', 'conversational_void_page_dp',
       'conversational_refund_page_dp', 'conversational_apply_page_dp']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_workspace_dp', 'conversational_detail_dp', 'conversational_doc_panel_dp',
                          'conversational_form_dp', 'conversational_void_page_dp', 'conversational_refund_page_dp',
                          'conversational_apply_page_dp'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = ANY(f)) <> 7 THEN
        RAISE EXCEPTION 'V378: flag Uang Muka CW kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V378: flag Uang Muka CW ditemukan di tenant lain';
    END IF;
END $$;
