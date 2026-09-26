-- V316 (26 Sep 2026, putusan pemilik "B" via MASTER): grapgrap melihat SEMUA dengan cara lama sampai siap.
-- Matikan (enabled=false, BUKAN hapus -> bisa dinyalakan lagi, lihat _ROLLBACK) dua flag Conversational Workspace
-- milik grapgrap-manado: conversational_workspace_so + conversational_form_so. kaos TIDAK berubah.
-- Dibaca GET /api/permissions/me -> features (services/tenant_features.fitur_aktif, per permintaan, TANPA cache
-- server) -> berlaku tanpa restart; FE yang menyimpan permissions/me melihatnya saat memuat ulang izin.
-- Idempoten (ulang = nol perubahan, penjaga tetap lulus). Gagal keras bila: baris grapgrap bukan TEPAT dua flag
-- itu, UPDATE menyentuh selain dua baris itu, grapgrap masih punya flag aktif, atau 4 flag kaos tak utuh.

DO $$
DECLARE n int;
BEGIN
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'grapgrap-manado'
          AND feature IN ('conversational_workspace_so', 'conversational_form_so')) <> 2 THEN
        RAISE EXCEPTION 'V316: dua baris flag CW grapgrap tidak ditemukan (premis salah)';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id = 'grapgrap-manado'
                 AND feature NOT IN ('conversational_workspace_so', 'conversational_form_so')) THEN
        RAISE EXCEPTION 'V316: grapgrap punya flag lain di luar dua ini — putusan pemilik perlu ditinjau ulang';
    END IF;

    UPDATE tenant_features SET enabled = false, updated_at = now()
    WHERE tenant_id = 'grapgrap-manado'
      AND feature IN ('conversational_workspace_so', 'conversational_form_so')
      AND enabled;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n > 2 THEN
        RAISE EXCEPTION 'V316: UPDATE menyentuh % baris (maks 2)', n;
    END IF;

    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled) THEN
        RAISE EXCEPTION 'V316: grapgrap masih punya flag aktif';
    END IF;
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature IN ('conversational_workspace_so', 'conversational_form_so',
                          'conversational_detail_so', 'conversational_form_so_save')) <> 4 THEN
        RAISE EXCEPTION 'V316: 4 flag CW kaos tidak utuh';
    END IF;
END $$;
