-- V369 (3 Okt 2026, aturan tetap pemilik flag CW; permintaan WORKSPACE) -- flag Penawaran CW "Kirim ke pelanggan"
-- (Q5: share/preview + share; draf -> terkirim atomik) untuk GRAPGRAP: conversational_doc_send_quote. Bukti: gerbang
-- FE + uji nyata kaos QUO-2610-0012 (snapshot BE 1->2 persis: +1 penawaran sent, +2 tautan, +1 QUOTE_SENT), r202 live
-- (main.6935e953.js). Nol kode, nol restart.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_doc_send_quote'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature = 'conversational_doc_send_quote') <> 1 THEN
        RAISE EXCEPTION 'V369: flag kirim penawaran CW grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado')
               AND feature = 'conversational_doc_send_quote') THEN
        RAISE EXCEPTION 'V369: flag kirim penawaran CW ditemukan di tenant lain';
    END IF;
END $$;
