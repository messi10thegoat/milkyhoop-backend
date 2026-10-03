-- V364 (WORKSPACE 3 Okt 2026) -- flag Penawaran CW FORM (tampil + simpan + ubah draf; Q6)
-- `conversational_form_quote` KAOS SAJA untuk uji nyata (Q6; FE r198 belum mengenal flag ini). grapgrap TIDAK (aturan tetap pemilik sesudah uji
-- nyata kaos lulus; migrasi terpisah). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila
-- tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_form_quote']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature = 'conversational_form_quote') <> 1 THEN
        RAISE EXCEPTION 'V364: flag penawaran CW kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi'
               AND feature = 'conversational_form_quote') THEN
        RAISE EXCEPTION 'V364: flag penawaran CW ditemukan di tenant lain';
    END IF;
END $$;
