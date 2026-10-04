-- V382 (WORKSPACE 5 Okt 2026) -- flag halaman "Batalkan penerapan" uang muka CW (U3a-b) KAOS SAJA untuk uji nyata.
-- BE U3a-b live (d7dd4fd7: reverse/preview + idempotensi /reverse). grapgrap TIDAK (migrasi terpisah, izin pemilik).
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_reverse_page_dp'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
                   AND feature = 'conversational_reverse_page_dp') THEN
        RAISE EXCEPTION 'V382: flag reverse_page_dp kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_reverse_page_dp') THEN
        RAISE EXCEPTION 'V382: flag reverse_page_dp ditemukan di tenant lain';
    END IF;
END $$;
