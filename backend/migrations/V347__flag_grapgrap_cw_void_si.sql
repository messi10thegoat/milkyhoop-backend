-- V347 (ATURAN TETAP pemilik 30 Sep, langsung di sesi BACKEND: "flag CW yang sudah lulus gerbang + uji nyata kaos
-- boleh langsung dinyalakan di grapgrap tanpa tanya ulang") — conversational_void_page_si (V342, CW "Batalkan faktur")
-- kini juga untuk grapgrap-manado. Bukti: gerbang 7619e17b lulus (2395/8) + uji nyata kaos 30 Sep: baca-saja INV-0103/0121/0005 (SNAPSHOT identik) + TULIS void INV-2609-0141 (REV-0018/0019 + COGS-REV-0001 = pratinjau, stok +1).
-- Halaman aktif di grapgrap hanya bila bundel FE memuatnya (rilis FE = izin pemilik).
-- Pola V332: idempoten, gagal keras bila tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_void_page_si'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_void_page_si' AND enabled) THEN
        RAISE EXCEPTION 'V347: flag conversational_void_page_si grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
