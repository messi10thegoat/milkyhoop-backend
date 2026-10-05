-- V395 (WORKSPACE meminta 5 Okt 2026; FE U1b lulus gerbang FULL, belum dirilis) -- flag CW pilih-massal daftar KAOS SAJA untuk uji nyata:
-- conversational_bulk_list = kotak centang + bilah aksi di 8 daftar Penjualan, memakai BE aksi massal F1-F5 (live). Uji nyata: baca
-- (CSV, PDF ZIP, pratinjau share) + tulis HANYA data 'TES E2E' di kaos. grapgrap TIDAK (migrasi terpisah sesudah uji).
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_bulk_list'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'conversational_bulk_list') <> 1 THEN
        RAISE EXCEPTION 'V395: flag bulk_list kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_bulk_list') THEN
        RAISE EXCEPTION 'V395: flag bulk_list ditemukan di tenant lain';
    END IF;
END $$;
