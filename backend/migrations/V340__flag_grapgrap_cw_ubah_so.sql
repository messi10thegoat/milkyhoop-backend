-- V340 (ATURAN TETAP pemilik 30 Sep, langsung di sesi BACKEND: "flag CW yang sudah lulus gerbang + uji nyata kaos
-- boleh langsung dinyalakan di grapgrap tanpa tanya ulang") — conversational_edit_page_so (V335, CW "Ubah pesanan")
-- kini juga untuk grapgrap-manado. Bukti: gerbang V335 lulus (5254e684) + uji TULIS nyata kaos 30 Sep 10:36Z
-- (SO-2609-0332: PATCH selisih {notes} -> hanya notes berubah, kedua baris identik seluruh kolom).
-- Pola V332: idempoten, gagal keras bila tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_edit_page_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_edit_page_so' AND enabled) THEN
        RAISE EXCEPTION 'V340: flag conversational_edit_page_so grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
