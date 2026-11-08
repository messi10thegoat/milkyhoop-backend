-- V486 (BACKEND 8 Nov 2026; pemilik konfirmasi LANGSUNG di sesi BACKEND: "Ya, kaos saja") -- flag ui_v3 (ui-pusat v3) untuk
-- kaos-biru-konveksi SAJA (tenant uji). Bawaan = MATI: tanpa baris tenant_features = mati (FE useFeature). ui_v3_kasbank_demo TETAP
-- MATI (tak ditulis; penjaga di bawah). nyawit = migrasi TERPISAH sesudah kaos lulus jalan-uji TESTER + konfirmasi pemilik.
-- Efek buku: NOL jurnal (flag hanya memilih tampilan FE). Dibaca per-request -> tanpa restart.
-- Rollback: V486_ROLLBACK. Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'ui_v3'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = 'ui_v3') THEN
        RAISE EXCEPTION 'V486: flag ui_v3 kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = 'ui_v3') THEN
        RAISE EXCEPTION 'V486: flag ui_v3 bocor ke tenant lain';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'ui_v3_kasbank_demo' AND enabled) THEN
        RAISE EXCEPTION 'V486: ui_v3_kasbank_demo wajib MATI di semua tenant';
    END IF;
END $$;
