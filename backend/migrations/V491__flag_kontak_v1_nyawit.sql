-- V491 (MASTER 9 Nov 2026; pemilik LANGSUNG di sesi MASTER: "Ok silakan nyalakan" -- Kontak ui-pusat nyawit + grapgrap)
-- flag listpage_/detailpage_/aksipage_{pelanggan,vendor}_v1 untuk pt-kita-nyawit-bersama (tenant uji; TESTER login di sini).
-- grapgrap menyusul sesudah TESTER nyawit LOLOS. Efek buku: NOL jurnal. Dibaca per-request -> tanpa restart. Rollback: V491_ROLLBACK.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'pt-kita-nyawit-bersama', f
FROM unnest(ARRAY['listpage_pelanggan_v1','detailpage_pelanggan_v1','aksipage_pelanggan_v1','listpage_vendor_v1','detailpage_vendor_v1','aksipage_vendor_v1']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'pt-kita-nyawit-bersama')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'pt-kita-nyawit-bersama' AND enabled AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$') <> 6 THEN
        RAISE EXCEPTION 'V491: 6 flag kontak nyawit tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'pt-kita-nyawit-bersama') AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$') THEN
        RAISE EXCEPTION 'V491: flag kontak bocor ke tenant lain';
    END IF;
END $$;
