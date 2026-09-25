-- V310 (Q-013 W4 + BUG-011, putusan pemilik 25 Sep 2026) — dua flag Conversational Workspace
-- untuk KAOS SAJA (tenant uji): conversational_detail_so (aksi detail pesanan, syarat uji nyata W4)
-- dan conversational_form_so_save (simpan di form CW; WORKSPACE memindah flag ini dari
-- localStorage ke server). grapgrap TIDAK (rilis bertahap; menulis ke grapgrap butuh pemilik).
--
-- Dibaca GET /api/permissions/me -> `features` = SEMUA flag enabled tenant (services/tenant_features
-- fitur_aktif; TIDAK ada daftar-izin di kode, jadi tak ada perubahan kode).
-- Pola V304/Q-010: idempoten (ON CONFLICT DO NOTHING), gagal keras bila seed tak mendarat ATAU
-- bila grapgrap ikut memilikinya. Aditif: baris baru saja.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f
FROM unnest(ARRAY['conversational_detail_so', 'conversational_form_so_save']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature IN ('conversational_detail_so', 'conversational_form_so_save')) <> 2 THEN
        RAISE EXCEPTION 'V310: flag kaos-biru-konveksi tidak lengkap/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id <> 'kaos-biru-konveksi'
                 AND feature IN ('conversational_detail_so', 'conversational_form_so_save')) THEN
        RAISE EXCEPTION 'V310: flag kaos-saja ditemukan di tenant lain';
    END IF;
END $$;
