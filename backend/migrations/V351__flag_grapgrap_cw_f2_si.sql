-- V351 (ATURAN TETAP pemilik 30 Sep, langsung di sesi BACKEND: "flag CW yang sudah lulus gerbang + uji nyata kaos
-- boleh langsung dinyalakan di grapgrap tanpa tanya ulang") — tiga halaman CW Faktur Penjualan untuk grapgrap-manado:
--   conversational_payment_page_si    (V346) gerbang 405b4307 + uji nyata kaos baca (7 pratinjau, snapshot identik)
--                                     + TULIS RCV-2026-0039 (payload pratinjau apa adanya, 10.000, jurnal seimbang)
--   conversational_edit_page_si       (V344) gerbang b13c500c + uji nyata kaos baca (calculate) + TULIS PATCH
--                                     INV-2609-0136 {notes} If-Match, baris faktur identik
--   conversational_creditnote_page_si (V343) gerbang 054bfec3/45ad6b91 + uji nyata kaos baca INV-2609-0103 (blok
--                                     per baris tampil) + TULIS CN-2609-0030 (jurnal CN-2609-0007 seimbang, tautan baris)
-- Halaman aktif di grapgrap hanya bila bundel FE memuatnya (rilis FE = izin pemilik). Pola V332: idempoten, gagal keras.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['conversational_payment_page_si', 'conversational_edit_page_si', 'conversational_creditnote_page_si']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('conversational_payment_page_si', 'conversational_edit_page_si', 'conversational_creditnote_page_si')) <> 3 THEN
        RAISE EXCEPTION 'V351: tiga flag F2 SI grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
