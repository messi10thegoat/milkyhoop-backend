-- V363 (2 Okt 2026, aturan tetap pemilik flag CW + perintah pemilik via MASTER) -- tiga flag Penawaran CW untuk GRAPGRAP:
-- conversational_workspace_quote (daftar), conversational_detail_quote (detail), conversational_convert_page_quote
-- (Konversi ke pesanan). Bukti: gerbang + uji nyata kaos Q1/Q2/Q4 (baca) + Q4/Q3 (tulis), r198 live. Nol kode, nol restart.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_workspace_quote', 'conversational_detail_quote',
                                               'conversational_convert_page_quote']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature IN
          ('conversational_workspace_quote', 'conversational_detail_quote', 'conversational_convert_page_quote')) <> 3 THEN
        RAISE EXCEPTION 'V363: flag penawaran CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature IN
          ('conversational_workspace_quote', 'conversational_detail_quote', 'conversational_convert_page_quote')) THEN
        RAISE EXCEPTION 'V363: flag penawaran CW ditemukan di tenant lain';
    END IF;
END $$;
