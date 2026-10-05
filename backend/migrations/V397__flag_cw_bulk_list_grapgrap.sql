-- V397 (WORKSPACE meminta 5 Okt 2026; MASTER GO; pemilik memutuskan "bulk grapgrap = aksi BACA saja") -- flag CW pilih-massal daftar GRAPGRAP:
-- conversational_bulk_list = kotak centang + bilah aksi di 8 daftar Penjualan. SENGAJA TANPA conversational_bulk_write: di grapgrap bilah
-- hanya memuat aksi baca (CSV, PDF ZIP, bagikan); aksi tulis massal (V396) tetap kaos saja. Dasar: aturan tetap pemilik 30 Sep (flag halaman CW
-- lulus gerbang + nyata kaos) -- bulk_list kaos sudah lulus uji nyata baca-saja (r219, nol tulis).
-- Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat, bila bulk_write ikut ada di grapgrap,
-- atau bila tenant selain kaos+grapgrap memiliki bulk_list.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_bulk_list'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = 'conversational_bulk_list') <> 1 THEN
        RAISE EXCEPTION 'V397: flag bulk_list grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_bulk_write') THEN
        RAISE EXCEPTION 'V397: bulk_write ada di grapgrap (harus TIDAK ada)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi','grapgrap-manado') AND feature = 'conversational_bulk_list') THEN
        RAISE EXCEPTION 'V397: bulk_list ditemukan di tenant lain';
    END IF;
END $$;
