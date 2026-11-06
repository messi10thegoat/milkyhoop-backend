-- V482 (BACKEND2 6 Nov 2026; terapkan HANYA sesudah konfirmasi LANGSUNG pemilik di sesi BACKEND2) -- flag CW Impor barang (BUG-021)
-- conversational_impor_barang untuk pt-kita-nyawit-bersama (target toko grosir; putusan pemilik opsi B 6 Nov malam: uji langsung nyawit).
-- Sesudah V482 yang ber-flag HANYA kaos-biru-konveksi (V481) + pt-kita-nyawit-bersama; grapgrap & lainnya TIDAK.
-- Efek buku: NOL jurnal (flag hanya membuka menu FE; rute /api/items/impor* hidup dan berizin item C). Dibaca per-request -> tanpa restart.
-- Rollback: V482_ROLLBACK (hanya baris nyawit; kaos tak tersentuh). Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'pt-kita-nyawit-bersama', 'conversational_impor_barang'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'pt-kita-nyawit-bersama')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'pt-kita-nyawit-bersama' AND enabled
                   AND feature = 'conversational_impor_barang') THEN
        RAISE EXCEPTION 'V482: flag impor barang nyawit tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('pt-kita-nyawit-bersama', 'kaos-biru-konveksi')
               AND feature = 'conversational_impor_barang') THEN
        RAISE EXCEPTION 'V482: flag impor barang bocor ke tenant lain';
    END IF;
END $$;
