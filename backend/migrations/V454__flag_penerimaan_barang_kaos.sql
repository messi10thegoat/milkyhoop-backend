-- V454 (BACKEND2, disiapkan 23 Okt 2026; BELUM diizinkan) -- flag Penerimaan Barang (GRN) untuk kaos-biru-konveksi SAJA (putusan P6).
-- GERBANG (KONTRAK-grn-20261023 §0): JANGAN apply sebelum sisi tagihan BACKEND (feat/tagihan-grn: posting & void tagihan memanggil
-- grn_tagih -> Dr GRNI, tanpa inbound ganda) LIVE dan harness E2E (terima -> posting tagihan nyata -> void) LULUS; lalu izin LANGSUNG pemilik.
-- Efek buku SESUDAH flag: /receive barang ber-stok di kaos menulis jurnal Dr Persediaan / Cr 2-10150 + stok masuk saat terima. grapgrap TIDAK.
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
