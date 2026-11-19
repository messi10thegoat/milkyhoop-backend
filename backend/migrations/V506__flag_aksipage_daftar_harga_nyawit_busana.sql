-- V506 (KEPALA BE 19 Nov 2026; pemilik LANGSUNG ke MASTER 19 Nov ±09:00: "nyalakan saja di semua tenant" -- tahap 1: tenant demo presentasi)
-- flag aksipage_daftar_harga_v1 (aksi tulis Daftar Harga CW; kini hanya kaos) untuk pt-kita-nyawit-bersama + pt-busana-indah-semesta.
-- grapgrap-manado = migrasi TERPISAH sesudah r56 FE live (aba-aba MASTER). Efek buku: NOL jurnal (flag hanya memilih tampilan/aksi FE).
-- Dibaca per-request -> tanpa restart. Rollback: V506_ROLLBACK. Jalankan lewat /root/apply_mig.sh.
INSERT INTO tenant_features (tenant_id, feature)
SELECT t.id, 'aksipage_daftar_harga_v1'
FROM "Tenant" t
WHERE t.id IN ('pt-kita-nyawit-bersama', 'pt-busana-indah-semesta')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true;
DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id IN ('pt-kita-nyawit-bersama', 'pt-busana-indah-semesta') AND enabled AND feature = 'aksipage_daftar_harga_v1') <> 2 THEN
        RAISE EXCEPTION 'V506: flag aksipage_daftar_harga_v1 nyawit/busana tidak terpasang/aktif (harus 2; tenant tak ada?)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'pt-kita-nyawit-bersama', 'pt-busana-indah-semesta') AND feature = 'aksipage_daftar_harga_v1' AND enabled) THEN
        RAISE EXCEPTION 'V506: flag aksipage_daftar_harga_v1 bocor ke tenant lain (grapgrap menyusul migrasi terpisah)';
    END IF;
END $$;
