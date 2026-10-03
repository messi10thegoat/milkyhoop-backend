-- V366 (3 Okt 2026, aturan tetap pemilik flag CW + perintah MASTER) -- flag FORM Penawaran CW (Q6: tampil + simpan +
-- ubah draf + duplikat lewat form) untuk GRAPGRAP: conversational_form_quote. Bukti: gerbang FE r200 + uji nyata kaos
-- (QUO-2610-0008 kirim, -0009 draf+ubah, -0010 duplikat; snapshot BE identik dgn harapan), r200 live. Nol kode, nol restart.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_form_quote'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature = 'conversational_form_quote') <> 1 THEN
        RAISE EXCEPTION 'V366: flag form penawaran CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')
               AND feature = 'conversational_form_quote') THEN
        RAISE EXCEPTION 'V366: flag form penawaran CW ditemukan di tenant lain';
    END IF;
END $$;
