-- V371 (BACKEND 4 Okt 2026) -- flag ubah SO TERKONFIRMASI (pola NetSuite) KAOS SAJA untuk uji nyata.
-- `so_edit_confirmed` dibaca BE (PATCH /sales-orders/{id} + POST /edit/preview) DAN FE (/permissions/me). Tanpa flag = 400 lama.
-- grapgrap TIDAK (aturan tetap pemilik sesudah uji nyata kaos lulus; migrasi terpisah). KODE DULU: apply dengan
-- apply_mig.sh --require-marker services/so_ubah_terkonfirmasi.py (kode lama mengabaikan flag ini -> aman bila terbalik,
-- tapi tak berguna). Tanpa restart sesudah kode live. Idempoten; gagal keras bila tak mendarat ATAU tenant lain ikut.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['so_edit_confirmed']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature IN ('so_edit_confirmed')) <> 1 THEN
        RAISE EXCEPTION 'V371: flag ubah SO terkonfirmasi kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi'
               AND feature IN ('so_edit_confirmed')) THEN
        RAISE EXCEPTION 'V371: flag ubah SO terkonfirmasi ditemukan di tenant lain';
    END IF;
END $$;
