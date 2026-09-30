-- V339 (pemilik 30 Sep 2026: "semua optimasi UI/UX yang sudah solid di kaos langsung dideploy ke grapgrap") —
-- dua flag CW yang SUDAH lulus gerbang penuh + uji nyata di kaos kini juga untuk grapgrap-manado:
--   conversational_payment_page_so (V331, CW "Terima pelunasan"; uji TULIS nyata kaos lulus 29 Sep)
--   conversational_cancel_page_so  (V333, CW "Batalkan pesanan"; uji baca-saja nyata kaos lulus 30 Sep)
-- conversational_edit_page_so (Ubah pesanan) SENGAJA TIDAK: uji nyata kaos belum dijalankan.
-- Pola V326: idempoten (baris enabled=false ikut dinyalakan), gagal keras bila keduanya tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['conversational_payment_page_so', 'conversational_cancel_page_so']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('conversational_payment_page_so', 'conversational_cancel_page_so')) <> 2 THEN
        RAISE EXCEPTION 'V339: flag grapgrap-manado tidak lengkap/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_edit_page_so' AND enabled) THEN
        RAISE EXCEPTION 'V339: conversational_edit_page_so tak boleh ikut menyala';
    END IF;
END $$;
