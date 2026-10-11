-- V408 (BACKEND2 11 Okt 2026; kontrak pengiriman serah, disetujui MASTER) -- flag `pengiriman_serah` KAOS SAJA + tiga cara
-- serah generik awal (K1: Kurir / Antar sendiri / Ambil sendiri; pemilik boleh mengganti nama / menambah JNE, J&T, dst).
-- Tanpa rilis FE = tanpa efek. grapgrap & tenant lain WAJIB NOL. Syarat: V407 + kode serah sudah hidup.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'pengiriman_serah'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

INSERT INTO metode_serah (tenant_id, nama, jenis, urutan)
SELECT 'kaos-biru-konveksi', v.nama, v.jenis, v.urutan
FROM (VALUES ('Kurir', 'kurir', 0), ('Antar sendiri', 'antar_sendiri', 1), ('Ambil sendiri', 'ambil_sendiri', 2)) AS v(nama, jenis, urutan)
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, lower(nama)) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature = 'pengiriman_serah') THEN
        RAISE EXCEPTION 'V408: pengiriman_serah kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'pengiriman_serah' AND tenant_id <> 'kaos-biru-konveksi') THEN
        RAISE EXCEPTION 'V408: pengiriman_serah bocor ke tenant lain (grapgrap wajib NOL)';
    END IF;
    IF EXISTS (SELECT 1 FROM metode_serah WHERE tenant_id <> 'kaos-biru-konveksi') THEN
        RAISE EXCEPTION 'V408: metode_serah bocor ke tenant lain';
    END IF;
END $$;
