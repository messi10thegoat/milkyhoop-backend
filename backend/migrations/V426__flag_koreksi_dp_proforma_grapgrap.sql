-- V426 (BACKEND2 13 Okt 2026; W-20261013-FLAG-GRAPGRAP-2, izin LANGSUNG pemilik di sesi BACKEND2) -- flag `koreksi_dp_proforma` untuk grapgrap-manado.
-- HANYA koreksi_dp_proforma. `pemindahan_barang` TIDAK dinyalakan (putusan pemilik): grapgrap 108 barang SEMUANYA non-stok dan PB menolak
-- barang non-stok (409 PB_BARANG_NON_STOK) -> flag itu menjanjikan fungsi yang tak bisa jalan (Law 34).
-- Efek buku: NOL jurnal (koreksi nominal proforma + pindah DP = metadata/alokasi, plafon + alasan wajib). Flag membuka UI WORKSPACE;
-- endpoint BE correct-amount/move-proforma tak dijaga flag. Dibaca per-request dari tenant_features (tanpa cache) -> tanpa restart.
-- Rollback: V426_ROLLBACK. Jalankan -v ON_ERROR_STOP=1 -1.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'koreksi_dp_proforma'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = 'koreksi_dp_proforma') THEN
        RAISE EXCEPTION 'V426: koreksi_dp_proforma grapgrap tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND feature = 'pemindahan_barang') THEN
        RAISE EXCEPTION 'V426: pemindahan_barang TIDAK boleh ada di grapgrap (0 barang ber-stok; PB menolak non-stok)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'koreksi_dp_proforma' AND enabled
               AND tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')) THEN
        RAISE EXCEPTION 'V426: koreksi_dp_proforma bocor ke tenant lain';
    END IF;
END $$;
