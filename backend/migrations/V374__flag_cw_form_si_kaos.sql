-- V374 (WORKSPACE 4 Okt 2026) -- flag form buat Faktur CW (U1c/W7d) KAOS SAJA untuk uji nyata (baca + tulis TES E2E).
-- `conversational_form_si` (form CW) + `conversational_form_si_save` (simpan). Defaults: GET /sales-invoices/defaults (404c907a).
-- grapgrap TIDAK (aturan tetap pemilik sesudah uji nyata kaos lulus; migrasi terpisah). Dibaca /permissions/me ->
-- nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_form_si', 'conversational_form_si_save']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature IN ('conversational_form_si', 'conversational_form_si_save')) <> 2 THEN
        RAISE EXCEPTION 'V374: flag form faktur CW kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi'
               AND feature IN ('conversational_form_si', 'conversational_form_si_save')) THEN
        RAISE EXCEPTION 'V374: flag form faktur CW ditemukan di tenant lain';
    END IF;
END $$;
