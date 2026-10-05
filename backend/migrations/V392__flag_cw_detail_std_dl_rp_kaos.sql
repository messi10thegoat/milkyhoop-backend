-- V392 (WORKSPACE 5 Okt 2026) -- flag KAOS SAJA untuk lengan BACA nyata D-standar: conversational_detail_std_dl
-- (D4 Pengiriman) + conversational_detail_std_rp (D5 Penerimaan). grapgrap TIDAK (migrasi terpisah sesudah gerbang +
-- nyata kaos, aturan tetap flag CW). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak
-- mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_detail_std_dl', 'conversational_detail_std_rp']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_detail_std_dl', 'conversational_detail_std_rp'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = ANY(f)) <> 2 THEN
        RAISE EXCEPTION 'V392: flag kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V392: flag ditemukan di tenant lain';
    END IF;
END $$;
