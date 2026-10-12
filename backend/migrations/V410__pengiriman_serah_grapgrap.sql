-- V410 (MASTER 12 Okt 2026) -- pengiriman serah untuk GRAPGRAP, putusan pemilik ("nyalakan di grapgrap kalau kaos solid").
-- Pola NetSuite Preferences: PENGATURAN per tenant, bukan kode khusus. TERAPKAN hanya sesudah FE r236 (Kirim serah) live.
--   1) flag pengiriman_serah untuk grapgrap-manado;
--   2) tiga cara serah generik yang SAMA dengan kaos (V408): Kurir / Antar sendiri / Ambil sendiri;
--   3) pengiriman_preferensi grapgrap: bawaan cara serah = Ambil sendiri, lokasi kirim = gudang 'Toko Manado'
--      (pemilik: konsumen biasanya ambil sendiri di toko Manado). Gudang dicari LEWAT NAMA milik grapgrap (diukur 12 Okt:
--      tepat satu, kode MDO, aktif) -- FK default_lokasi_kirim_id hanya ke warehouses(id), jadi tenant dijaga di sini.
-- Idempoten (ON CONFLICT DO NOTHING; preferensi yang SUDAH ada TIDAK ditimpa). Guard: grapgrap lengkap, kaos tetap,
-- tenant lain nol. NOL BUKU. Snapshot: SJ lama tak tersentuh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'pengiriman_serah'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

INSERT INTO metode_serah (tenant_id, nama, jenis, urutan)
SELECT 'grapgrap-manado', v.nama, v.jenis, v.urutan
FROM (VALUES ('Kurir', 'kurir', 0), ('Antar sendiri', 'antar_sendiri', 1), ('Ambil sendiri', 'ambil_sendiri', 2)) AS v(nama, jenis, urutan)
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, lower(nama)) DO NOTHING;

DO $$
DECLARE
    n_gudang int;
BEGIN
    SELECT count(*) INTO n_gudang FROM warehouses
     WHERE tenant_id = 'grapgrap-manado' AND name = 'Toko Manado' AND is_active;
    IF n_gudang <> 1 THEN
        RAISE EXCEPTION 'V410: gudang aktif ''Toko Manado'' milik grapgrap-manado harus tepat satu (ditemukan %)', n_gudang;
    END IF;
END $$;

INSERT INTO pengiriman_preferensi (tenant_id, default_metode_serah_id, default_lokasi_kirim_id)
SELECT 'grapgrap-manado',
       (SELECT id FROM metode_serah WHERE tenant_id = 'grapgrap-manado' AND jenis = 'ambil_sendiri' AND lower(nama) = 'ambil sendiri'),
       (SELECT id FROM warehouses WHERE tenant_id = 'grapgrap-manado' AND name = 'Toko Manado' AND is_active)
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = 'pengiriman_serah') THEN
        RAISE EXCEPTION 'V410: pengiriman_serah grapgrap tidak terpasang/aktif';
    END IF;
    IF (SELECT count(*) FROM metode_serah WHERE tenant_id = 'grapgrap-manado' AND aktif
          AND (lower(nama), jenis) IN (('kurir', 'kurir'), ('antar sendiri', 'antar_sendiri'), ('ambil sendiri', 'ambil_sendiri'))) <> 3 THEN
        RAISE EXCEPTION 'V410: tiga cara serah generik grapgrap tidak lengkap/aktif (harus 3)';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pengiriman_preferensi p
          JOIN metode_serah m ON m.id = p.default_metode_serah_id AND m.tenant_id = p.tenant_id
          JOIN warehouses w ON w.id = p.default_lokasi_kirim_id AND w.tenant_id = p.tenant_id
         WHERE p.tenant_id = 'grapgrap-manado' AND m.jenis = 'ambil_sendiri' AND w.name = 'Toko Manado') THEN
        RAISE EXCEPTION 'V410: preferensi grapgrap bukan Ambil sendiri @ Toko Manado milik grapgrap (atau sudah ada nilai lain)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'pengiriman_serah')
       OR (SELECT count(*) FROM metode_serah WHERE tenant_id = 'kaos-biru-konveksi') <> 3 THEN
        RAISE EXCEPTION 'V410: kaos tidak lagi utuh (flag aktif + 3 cara serah)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'pengiriman_serah' AND tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado'))
       OR EXISTS (SELECT 1 FROM metode_serah WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado'))
       OR EXISTS (SELECT 1 FROM pengiriman_preferensi WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')) THEN
        RAISE EXCEPTION 'V410: serah bocor ke tenant selain kaos/grapgrap';
    END IF;
END $$;
