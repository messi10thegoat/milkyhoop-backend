-- V489 (MASTER 9 Nov 2026; pemilik LANGSUNG di sesi MASTER: "langsung nyalakan saja untuk sawit dan grapgrap") -- flag
-- ui_v3_detail (detail/chatbox/halaman aksi = komponen v3 daftar; FE r13 1d28e8559) untuk pt-kita-nyawit-bersama + grapgrap-manado.
-- Hanya bermakna bila ui_v3 aktif (keduanya sudah: V487/V488). Tenant lain TIDAK. Efek buku: NOL jurnal (flag hanya memilih tampilan FE).
-- Dibaca per-request -> tanpa restart. Rollback: V489_ROLLBACK. Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT t.id, 'ui_v3_detail'
FROM "Tenant" t
WHERE t.id IN ('pt-kita-nyawit-bersama', 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id IN ('pt-kita-nyawit-bersama', 'grapgrap-manado') AND enabled AND feature = 'ui_v3_detail') <> 2 THEN
        RAISE EXCEPTION 'V489: flag ui_v3_detail nyawit/grapgrap tidak terpasang/aktif (harus 2)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'pt-kita-nyawit-bersama', 'grapgrap-manado') AND feature = 'ui_v3_detail') THEN
        RAISE EXCEPTION 'V489: flag ui_v3_detail bocor ke tenant lain';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'ui_v3_kasbank_demo' AND enabled) THEN
        RAISE EXCEPTION 'V489: ui_v3_kasbank_demo wajib MATI di semua tenant';
    END IF;
END $$;
