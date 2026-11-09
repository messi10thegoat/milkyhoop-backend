-- V492 (MASTER 9 Nov 2026; pemilik LANGSUNG di sesi MASTER: "Ok silakan nyalakan" -- Kontak ui-pusat nyawit + grapgrap)
-- flag listpage_/detailpage_/aksipage_{pelanggan,vendor}_v1 untuk grapgrap-manado (tenant NYATA; TESTER nyawit LOLOS 1–6 9 Nov).
-- kaos (V490) + nyawit (V491) sudah. Efek buku: NOL jurnal. Dibaca per-request -> tanpa restart. Rollback: V492_ROLLBACK.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['listpage_pelanggan_v1','detailpage_pelanggan_v1','aksipage_pelanggan_v1','listpage_vendor_v1','detailpage_vendor_v1','aksipage_vendor_v1']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$') <> 6 THEN
        RAISE EXCEPTION 'V492: 6 flag kontak grapgrap tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'pt-kita-nyawit-bersama', 'grapgrap-manado') AND feature ~ '^(listpage|detailpage|aksipage)_(pelanggan|vendor)_v1$') THEN
        RAISE EXCEPTION 'V492: flag kontak bocor ke tenant lain';
    END IF;
END $$;
