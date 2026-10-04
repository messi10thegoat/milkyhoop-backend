-- V375 (WORKSPACE 4 Okt 2026; aturan tetap pemilik flag CW + persetujuan LANGSUNG pemilik di sesi WORKSPACE) -- form buat
-- Faktur CW untuk GRAPGRAP: `conversational_form_si` + `conversational_form_si_save`. Lulus gerbang + uji nyata kaos U1c
-- (V374: baca identik, tulis INV-2610-0002 posted jurnal seimbang) + idempotensi POST /sales-invoices live & terbukti
-- nyata (INV-2610-0003 balasan hilang = 1 faktur) + r206 live (origin main.4710c307). Rekening utama grapgrap NULL
-- (pemilik tahu). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_form_si', 'conversational_form_si_save']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('conversational_form_si', 'conversational_form_si_save')) <> 2 THEN
        RAISE EXCEPTION 'V375: flag form faktur CW grapgrap tidak mendarat/aktif';
    END IF;
END $$;
