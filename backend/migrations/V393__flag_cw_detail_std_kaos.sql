-- V393 (WORKSPACE meminta 5 Okt 2026; FE D-standar lulus gerbang FULL, belum dirilis) -- flag CW detail-standar KAOS SAJA untuk uji nyata
-- BACA-SAJA (nol tulis): conversational_detail_std_dp (D2 Uang Muka), _pf (D1 Proforma), _cn (D6 Nota Kredit), _si (D3 Faktur),
-- _quote (D7 Penawaran). grapgrap TIDAK (migrasi terpisah sesudah uji). Dibaca /permissions/me -> nol kode, nol restart.
-- Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_detail_std_dp', 'conversational_detail_std_pf',
       'conversational_detail_std_cn', 'conversational_detail_std_si', 'conversational_detail_std_quote']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_detail_std_dp', 'conversational_detail_std_pf', 'conversational_detail_std_cn',
                          'conversational_detail_std_si', 'conversational_detail_std_quote'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = ANY(f)) <> 5 THEN
        RAISE EXCEPTION 'V393: flag detail_std kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V393: flag detail_std ditemukan di tenant lain';
    END IF;
END $$;
