-- V367 (3 Okt 2026, aturan tetap pemilik flag CW + perintah MASTER) -- flag SO CW tawaran "Konfirmasi pesanan sekarang?"
-- saat Terima DP pada SO draf, untuk GRAPGRAP: conversational_dp_confirm_offer_so. Bukti: gerbang FE r201 + uji nyata
-- kaos (SO-2610-0008 draf -> pratinjau -> konfirmasi 004-10-26 -> DEP-2610-0002 100.000; snapshot BE persis), r201
-- live. Pagar uang TETAP (SO draf tak menerima DP; FE menawarkan konfirmasi dulu). Nol kode, nol restart.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_dp_confirm_offer_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature = 'conversational_dp_confirm_offer_so') <> 1 THEN
        RAISE EXCEPTION 'V367: flag dp-konfirmasi CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')
               AND feature = 'conversational_dp_confirm_offer_so') THEN
        RAISE EXCEPTION 'V367: flag dp-konfirmasi CW ditemukan di tenant lain';
    END IF;
END $$;
