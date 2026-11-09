-- V490 (MASTER 9 Nov 2026; pemilik LANGSUNG di sesi MASTER: "Ok silakan nyalakan" -- Kontak ui-pusat; kaos = tenant uji dulu)
-- flag listpage_/detailpage_/aksipage_{pelanggan,vendor}_v1 untuk kaos-biru-konveksi SAJA (FE r15 ab8532ef1, BE kontak-2 7a5a50633).
-- nyawit + grapgrap menyusul sesudah TESTER kaos LOLOS. Efek buku: NOL jurnal (flag hanya memilih tampilan FE).
-- Dibaca per-request -> tanpa restart. Rollback: V490_ROLLBACK. Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f
FROM unnest(ARRAY['listpage_pelanggan_v1','detailpage_pelanggan_v1','aksipage_pelanggan_v1','listpage_vendor_v1','detailpage_vendor_v1','aksipage_vendor_v1']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$') <> 6 THEN
        RAISE EXCEPTION 'V490: 6 flag kontak kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$') THEN
        RAISE EXCEPTION 'V490: flag kontak bocor ke tenant lain';
    END IF;
END $$;
