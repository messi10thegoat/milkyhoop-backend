-- V396 (WORKSPACE meminta 5 Okt 2026; putusan MASTER) -- flag CW aksi TULIS massal KAOS SAJA:
-- conversational_bulk_write = aksi tulis di bilah pilih-massal (SO konfirmasi/hapus draf, penawaran kirim/hapus, proforma batal),
-- dipisah dari aksi baca (conversational_bulk_list) supaya pemilik bisa menyalakan "baca saja" dulu di grapgrap. FE r219 (F-pill):
-- aksi tulis tampil hanya bila bulk_list DAN bulk_write aktif; kaos wajib memegang keduanya.
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat, bulk_list kaos tak aktif, ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'conversational_bulk_write'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'conversational_bulk_write') <> 1 THEN
        RAISE EXCEPTION 'V396: flag bulk_write kaos tidak mendarat/aktif';
    END IF;
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'conversational_bulk_list') <> 1 THEN
        RAISE EXCEPTION 'V396: bulk_list kaos tidak aktif (aksi tulis butuh keduanya)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'conversational_bulk_write') THEN
        RAISE EXCEPTION 'V396: flag bulk_write ditemukan di tenant lain';
    END IF;
END $$;
