-- V345 (ATURAN TETAP pemilik 30 Sep, langsung di sesi BACKEND: "flag CW yang sudah lulus gerbang + uji nyata kaos
-- boleh langsung dinyalakan di grapgrap tanpa tanya ulang") — conversational_ship_page_so (V341, CW "Kirim barang")
-- kini juga untuk grapgrap-manado. Bukti: gerbang 7276cf4f lulus (2382/8) + uji nyata kaos 30 Sep: baca-saja
-- SO-2609-0008 (SNAPSHOT-1 = SNAPSHOT-2) + TULIS SO-2609-0333 (3 pcs -> SJ-2609-0006/0007 satu transaksi, HPP 165.000,
-- pendapatan 300.000 = pratinjau). Halaman aktif di grapgrap hanya bila bundel FE memuatnya (rilis FE = izin pemilik).
-- Pola V332: idempoten, gagal keras bila tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_ship_page_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_ship_page_so' AND enabled) THEN
        RAISE EXCEPTION 'V345: flag conversational_ship_page_so grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
