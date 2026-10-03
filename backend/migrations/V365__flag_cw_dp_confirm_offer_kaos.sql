-- V365 (WORKSPACE 3 Okt 2026) -- flag SO CW tawaran Konfirmasi pesanan saat Terima DP (SO draf)
-- `conversational_dp_confirm_offer_so` KAOS SAJA untuk uji nyata (DP-konfirmasi; FE live belum mengenal flag ini). grapgrap TIDAK (aturan tetap pemilik sesudah uji
-- nyata kaos lulus; migrasi terpisah). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila
-- tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_dp_confirm_offer_so']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature = 'conversational_dp_confirm_offer_so') <> 1 THEN
        RAISE EXCEPTION 'V365: flag dp-konfirmasi CW kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi'
               AND feature = 'conversational_dp_confirm_offer_so') THEN
        RAISE EXCEPTION 'V365: flag dp-konfirmasi CW ditemukan di tenant lain';
    END IF;
END $$;
