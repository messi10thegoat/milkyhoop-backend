-- V388 (5 Okt 2026; izin LANGSUNG pemilik di sesi BACKEND2 lewat AskUserQuestion: "Ya, nyalakan keduanya"; WORKSPACE meminta sesudah
-- r213 live) -- flag CW untuk GRAPGRAP: Pengiriman (workspace, detail, panel Dok., form: _dl) + Nota Kredit (workspace, detail: _cn;
-- NK mencakup aksi refund = uang keluar, karena itu izin langsung). kaos sudah memilikinya (V386). Dibaca /permissions/me -> nol kode,
-- nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant selain kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_workspace_dl', 'conversational_detail_dl',
       'conversational_doc_panel_dl', 'conversational_form_dl', 'conversational_workspace_cn',
       'conversational_detail_cn']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_workspace_dl', 'conversational_detail_dl', 'conversational_doc_panel_dl',
                          'conversational_form_dl', 'conversational_workspace_cn', 'conversational_detail_cn'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = ANY(f)) <> 6 THEN
        RAISE EXCEPTION 'V388: flag Pengiriman/NK CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V388: flag Pengiriman/NK CW ditemukan di tenant selain kaos/grapgrap';
    END IF;
END $$;
