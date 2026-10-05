-- V394 (5 Okt 2026; ATURAN TETAP pemilik 30 Sep: flag halaman CW yang lulus gerbang merge + cek nyata kaos boleh menyala di grapgrap tanpa
-- tanya lagi; MASTER 5 Okt menegaskan; WORKSPACE meminta sesudah FE r217 LIVE) -- flag CW detail-standar untuk GRAPGRAP (TAMPILAN saja,
-- nol jurnal, jalur lama tetap per halaman): conversational_detail_std_dl (D4 Pengiriman), _rp (Penerimaan), _dp (D2 Uang Muka),
-- _pf (D1 Proforma), _cn (D6 NK), _si (D3 Faktur), _quote (D7 Penawaran). kaos sudah memilikinya (V392, V393; uji nyata baca-saja
-- 16 dokumen lulus). Dibaca /permissions/me -> nol kode, nol restart. Idempoten; gagal keras bila tak mendarat ATAU tenant selain
-- kaos/grapgrap ikut memilikinya.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f FROM unnest(ARRAY['conversational_detail_std_dl', 'conversational_detail_std_rp',
       'conversational_detail_std_dp', 'conversational_detail_std_pf', 'conversational_detail_std_cn',
       'conversational_detail_std_si', 'conversational_detail_std_quote']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
DECLARE f text[] := ARRAY['conversational_detail_std_dl', 'conversational_detail_std_rp', 'conversational_detail_std_dp',
                          'conversational_detail_std_pf', 'conversational_detail_std_cn', 'conversational_detail_std_si',
                          'conversational_detail_std_quote'];
BEGIN
    IF (SELECT count(*) FROM tenant_features WHERE tenant_id = 'grapgrap-manado' AND enabled AND feature = ANY(f)) <> 7 THEN
        RAISE EXCEPTION 'V394: flag detail_std grapgrap tidak mendarat/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id NOT IN ('kaos-biru-konveksi', 'grapgrap-manado') AND feature = ANY(f)) THEN
        RAISE EXCEPTION 'V394: flag detail_std ditemukan di tenant selain kaos/grapgrap';
    END IF;
END $$;
