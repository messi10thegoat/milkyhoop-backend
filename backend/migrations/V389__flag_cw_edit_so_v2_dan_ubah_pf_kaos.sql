-- V389 (WORKSPACE 5 Okt 2026) -- flag KAOS SAJA untuk uji nyata: conversational_edit_so_v2 (Ubah-SO-b, di atas
-- so_edit_confirmed yang ada) + conversational_edit_page_pf (U2b Ubah draf proforma; BE live eed9daf8). grapgrap TIDAK
-- (migrasi terpisah, izin pemilik). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak
-- mendarat ATAU tenant lain ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', f FROM unnest(ARRAY['conversational_edit_so_v2', 'conversational_edit_page_pf']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_edit_so_v2', 'conversational_edit_page_pf'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled AND feature = ANY(f)) <> 2 THEN
        RAISE EXCEPTION 'V389: flag kaos tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id <> 'kaos-biru-konveksi' AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V389: flag ditemukan di tenant lain';
    END IF;
END $$;
