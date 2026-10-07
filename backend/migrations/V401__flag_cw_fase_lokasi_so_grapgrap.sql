-- V401 (WORKSPACE meminta 7 Okt 2026; aturan tetap pemilik 30 Sep: flag halaman CW lulus gerbang + nyata kaos hijau -> grapgrap tanpa tanya) --
-- conversational_fase_lokasi_so GRAPGRAP: kontrol "Dikirim ke / Tersedia di {gudang}" di pesanan, filter + kolom Sisa.
-- Bukti: BE a1ffe94b (gerbang 3413/8), FE r225 live (BUILD_INFO 2 kontainer+origin+edge), uji nyata kaos run 1/1b read-only diff NOL + run 2 tulis
-- sesuai ekspektasi (+2 audit, +2 idem, pulih, nol dampak keuangan/stok). Data operasional saja: nol jurnal/stok. Grapgrap punya 2 gudang aktif.
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant selain kaos+grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_fase_lokasi_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = 'conversational_fase_lokasi_so') <> 1 THEN
        RAISE EXCEPTION 'V401: flag fase_lokasi_so grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = 'conversational_fase_lokasi_so') THEN
        RAISE EXCEPTION 'V401: flag fase_lokasi_so ditemukan di tenant lain';
    END IF;
END $$;
