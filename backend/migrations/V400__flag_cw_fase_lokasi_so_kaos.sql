-- V400 (WORKSPACE meminta 7 Okt 2026; putusan MASTER: bendera FE TERPISAH) -- flag CW fase lokasi pesanan KAOS SAJA:
-- conversational_fase_lokasi_so = kontrol "Dikirim ke / Tersedia di {gudang}" di pesanan, filter + kolom Sisa (BE: V399 + fase-lokasi).
-- Uji nyata: tulis HANYA data 'TES E2E' di kaos. grapgrap TIDAK (migrasi terpisah sesudah gerbang + nyata kaos hijau).
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_fase_lokasi_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'conversational_fase_lokasi_so') <> 1 THEN
        RAISE EXCEPTION 'V400: flag fase_lokasi_so kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_fase_lokasi_so') THEN
        RAISE EXCEPTION 'V400: flag fase_lokasi_so ditemukan di tenant lain';
    END IF;
END $$;
