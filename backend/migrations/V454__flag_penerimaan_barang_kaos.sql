-- V454 (BACKEND2, disiapkan 23 Okt 2026; terapan = izin LANGSUNG pemilik) -- flag Penerimaan Barang (GRN) untuk kaos-biru-konveksi SAJA (P6).
-- GERBANG terpenuhi 23 Okt: aturan D (pemilik) LIVE 0012b264 = sisi penerimaan BACKEND2 + sisi tagihan BACKEND (feat/tagihan-grn,
-- feat/grn-aturan-d-tagih) + penjaga #1/#2/#3; flag mati LAMA e5d8b712 == BARU (kaos + grapgrap-manado, terima + tagihan); harness
-- e2e gabungan (terima dulu / tagih dulu / legacy / void simetris / NET 0) 53 PASS + kontrol merah merah/merah_fifo/merah_d MERAH.
-- Efek buku SESUDAH flag (kaos saja; grapgrap TIDAK): barang ber-stok -> stok masuk HANYA saat terima (Dr Persediaan @ harga PO / Cr GRNI
-- 2-10150); tagihan atas baris PO ber-stok -> Dr GRNI (tanpa stok); selisih harga -> 5-10400. Non-stok/jasa: dokumen saja.
-- Rollback: V454_ROLLBACK. Jalankan -v ON_ERROR_STOP=1 -1 (apply_mig.sh). Dibaca per-request -> tanpa restart.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM (VALUES ('penerimaan_barang_v1')) v(f)
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
        AND feature IN ('penerimaan_barang_v1')) <> 1 THEN
        RAISE EXCEPTION 'V454: flag kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature IN ('penerimaan_barang_v1')) THEN
        RAISE EXCEPTION 'V454: flag bocor ke tenant lain';
    END IF;
END $$;
