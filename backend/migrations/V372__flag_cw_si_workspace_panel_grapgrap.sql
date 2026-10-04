-- V372 (MASTER 4 Okt 2026; aturan tetap pemilik flag CW + pilihan LANGSUNG pemilik di sesi MASTER) -- daftar Faktur CW
-- + panel Dok. untuk GRAPGRAP: `conversational_workspace_si` + `conversational_doc_panel_si`. Lulus gerbang + uji nyata
-- kaos U1 (V370, snapshot identik) + r204 live. `conversational_form_si` TETAP MATI (form buat masih lama: "CW sebagian").
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU form_si ikut menyala.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_workspace_si', 'conversational_doc_panel_si']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('conversational_workspace_si', 'conversational_doc_panel_si')) <> 2 THEN
        RAISE EXCEPTION 'V372: flag faktur CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_form_si' AND enabled) THEN
        RAISE EXCEPTION 'V372: conversational_form_si grapgrap menyala -- harus TETAP mati';
    END IF;
END $$;
