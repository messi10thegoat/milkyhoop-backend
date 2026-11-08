-- V487 (BACKEND 8 Nov 2026; permintaan pemilik lewat MASTER "nyalakan di sawit saja UI-pusatnya" + konfirmasi LANGSUNG pemilik di sesi
-- BACKEND) -- flag ui_v3 (ui-pusat v3) untuk pt-kita-nyawit-bersama (tenant uji grosir). kaos (V486) tetap; tenant lain TIDAK.
-- ui_v3_kasbank_demo TETAP MATI (tak ditulis; penjaga di bawah). Efek buku: NOL jurnal (flag hanya memilih tampilan FE).
-- Dibaca per-request -> tanpa restart. Rollback: V487_ROLLBACK. Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'pt-kita-nyawit-bersama', 'ui_v3'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'pt-kita-nyawit-bersama')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'pt-kita-nyawit-bersama' AND enabled AND feature = 'ui_v3') THEN
        RAISE EXCEPTION 'V487: flag ui_v3 nyawit tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'pt-kita-nyawit-bersama') AND feature = 'ui_v3') THEN
        RAISE EXCEPTION 'V487: flag ui_v3 bocor ke tenant lain';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'ui_v3_kasbank_demo' AND enabled) THEN
        RAISE EXCEPTION 'V487: ui_v3_kasbank_demo wajib MATI di semua tenant';
    END IF;
END $$;
