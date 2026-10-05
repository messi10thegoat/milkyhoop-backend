-- V391 (5 Okt 2026; ATURAN TETAP pemilik 30 Sep: flag halaman CW yang lulus gerbang merge + cek nyata kaos boleh menyala di grapgrap tanpa
-- tanya lagi; MASTER 5 Okt menegaskan; WORKSPACE meminta sesudah FE r215 live) -- flag CW untuk GRAPGRAP:
--   conversational_edit_so_v2   = Ubah SO terkonfirmasi v2 (hanya merapikan TAMPILAN fitur so_edit_confirmed yang sudah disetujui
--                                  pemilik LANGSUNG 4 Okt)
--   conversational_edit_page_pf = halaman Ubah Proforma DRAF (U2b; nol jurnal, tetap draf).
-- kaos sudah memilikinya (V389; uji tulis nyata U2b lulus, diff DB bersih). Dibaca /permissions/me -> nol kode, nol restart.
-- Idempoten; gagal keras bila tak mendarat ATAU tenant selain kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_edit_so_v2', 'conversational_edit_page_pf']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_edit_so_v2', 'conversational_edit_page_pf'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = ANY(f)) <> 2 THEN
        RAISE EXCEPTION 'V391: flag edit_so_v2/edit_page_pf grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V391: flag edit_so_v2/edit_page_pf ditemukan di tenant selain kaos/grapgrap';
    END IF;
END $$;
