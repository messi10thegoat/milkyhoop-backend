-- V488 (MASTER 9 Nov 2026; pemilik LANGSUNG di sesi MASTER: "ok, tolong nyalakan UI UX pusat untuk grapgrap") -- flag ui_v3
-- (ui-pusat v3) untuk grapgrap-manado. kaos (V486) + nyawit (V487) tetap; tenant lain TIDAK.
-- ui_v3_kasbank_demo TETAP MATI (tak ditulis; penjaga di bawah). Efek buku: NOL jurnal (flag hanya memilih tampilan FE).
-- Dibaca per-request -> tanpa restart. Rollback: V488_ROLLBACK. Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'ui_v3'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = 'ui_v3') THEN
        RAISE EXCEPTION 'V488: flag ui_v3 grapgrap tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'pt-kita-nyawit-bersama', 'grapgrap-manado') AND feature = 'ui_v3') THEN
        RAISE EXCEPTION 'V488: flag ui_v3 bocor ke tenant lain';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'ui_v3_kasbank_demo' AND enabled) THEN
        RAISE EXCEPTION 'V488: ui_v3_kasbank_demo wajib MATI di semua tenant';
    END IF;
END $$;
