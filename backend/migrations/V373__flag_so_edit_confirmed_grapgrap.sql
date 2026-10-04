-- V373 (BACKEND 4 Okt 2026) -- flag ubah SO TERKONFIRMASI (pola NetSuite) untuk GRAPGRAP. Jalur disetujui pemilik LANGSUNG
-- di sesi BACKEND ("kaos dulu, lalu grapgrap lewat aturan tetap flag CW"); uji nyata kaos LULUS 4 Okt (baca: snapshot
-- identik; tulis SO-2610-0007: diff tepat header+1 baris+1 riwayat+1 kunci, replay tanpa tulis). Flag dibaca BE DAN FE:
-- APPLY HANYA SESUDAH FE ubah SO (r205) LIVE (MASTER 4 Okt) -- sebelum itu PATCH terkonfirmasi terbuka tanpa layar.
-- Idempoten; gagal keras bila tak mendarat.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['so_edit_confirmed']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature = 'so_edit_confirmed') <> 1 THEN
        RAISE EXCEPTION 'V373: flag ubah SO terkonfirmasi grapgrap tidak mendarat/aktif';
    END IF;
END $$;
