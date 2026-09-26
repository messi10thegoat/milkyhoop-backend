-- V317 (26 Sep 2026, pemilik MENGUBAH putusan V316, langsung ke MASTER: "WO SO di grapgrap boleh dinyalakan lagi,
-- saya mau tes live"): CW SO PENUH untuk grapgrap-manado, SAMA dengan kaos — conversational_workspace_so,
-- conversational_form_so (dua baris V316 -> enabled=true) + conversational_form_so_save, conversational_detail_so
-- (baris baru). kaos TIDAK berubah. Dibaca per permintaan permissions/me (tanpa cache server) -> tanpa restart.
-- Idempoten (ON CONFLICT + UPDATE hanya yang mati). Gagal keras bila: grapgrap sesudahnya bukan TEPAT 4 flag
-- aktif ini, grapgrap punya flag lain, 4 flag kaos tak utuh, atau ada tenant LAIN yang memegang flag CW.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['conversational_workspace_so', 'conversational_form_so',
                  'conversational_form_so_save', 'conversational_detail_so']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

UPDATE tenant_features SET enabled = true, updated_at = now()
WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('conversational_workspace_so', 'conversational_form_so',
                  'conversational_form_so_save', 'conversational_detail_so')
  AND NOT enabled;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('conversational_workspace_so', 'conversational_form_so',
                          'conversational_form_so_save', 'conversational_detail_so')) <> 4 THEN
        RAISE EXCEPTION 'V317: 4 flag CW grapgrap tidak lengkap/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id = 'grapgrap-manado'
                 AND feature NOT IN ('conversational_workspace_so', 'conversational_form_so',
                                     'conversational_form_so_save', 'conversational_detail_so')) THEN
        RAISE EXCEPTION 'V317: grapgrap punya flag lain di luar CW SO';
    END IF;
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature IN ('conversational_workspace_so', 'conversational_form_so',
                          'conversational_form_so_save', 'conversational_detail_so')) <> 4 THEN
        RAISE EXCEPTION 'V317: 4 flag CW kaos tidak utuh';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id NOT IN ('grapgrap-manado', 'kaos-biru-konveksi')
                 AND feature LIKE 'conversational%') THEN
        RAISE EXCEPTION 'V317: tenant lain memegang flag CW';
    END IF;
END $$;
