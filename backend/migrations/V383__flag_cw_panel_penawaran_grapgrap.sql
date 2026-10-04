-- V383 (WORKSPACE 4 Okt 2026, U1e Penawaran PDF/panel): flag panel Dok. Penawaran CW untuk GRAPGRAP-MANADO.
-- Dasar izin: ATURAN TETAP pemilik 30 Sep (flag CW yang lulus gerbang merge + cek NYATA kaos boleh menyala di grapgrap tanpa tanya
-- lagi; wajib dilaporkan) + kata pemilik LANGSUNG yang diteruskan MASTER: "langsung nyalakan flag untuk grapgrap kalau sudah solid".
-- Bukti: BE U1e live (229bdc40, gerbang 2979), V380 flag kaos, lengan baca nyata kaos lulus (SNAPSHOT nol diff, log gateway hanya GET),
-- FE r211 live. Flag TAMPILAN saja (panel / Lihat PDF / subtitle): tanpa jalur uang. Pola V380; idempoten; gagal keras bila tak
-- mendarat ATAU tenant selain kaos+grapgrap memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_doc_panel_quote'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = 'conversational_doc_panel_quote') <> 1 THEN
        RAISE EXCEPTION 'V383: flag conversational_doc_panel_quote grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features
               WHERE tenant_id NOT IN ('grapgrap-manado', 'kaos-biru-konveksi') AND feature = 'conversational_doc_panel_quote') THEN
        RAISE EXCEPTION 'V383: flag conversational_doc_panel_quote ditemukan di tenant selain grapgrap/kaos';
    END IF;
END $$;
