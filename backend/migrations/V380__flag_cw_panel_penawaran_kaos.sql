-- V380 (WORKSPACE 4 Okt 2026, U1e Penawaran PDF/panel): flag panel Dok. Penawaran CW -- KAOS SAJA untuk uji nyata.
-- Pola V378: grapgrap TIDAK (migrasi terpisah bila pemilik/WORKSPACE meminta). Dibaca /permissions/me -> nol kode, nol restart;
-- r209/r210 live belum mengenal flag ini (harmless sebelum rilis). Idempoten; gagal keras bila tak mendarat ATAU tenant lain
-- ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_doc_panel_quote'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'conversational_doc_panel_quote') <> 1 THEN
        RAISE EXCEPTION 'V380: flag conversational_doc_panel_quote kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_doc_panel_quote') THEN
        RAISE EXCEPTION 'V380: flag conversational_doc_panel_quote ditemukan di tenant lain';
    END IF;
END $$;
