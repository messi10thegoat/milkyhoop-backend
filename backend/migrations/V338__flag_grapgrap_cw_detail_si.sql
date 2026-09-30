-- V338 (pemilik 30 Sep 2026: "nyalakan detail faktur CW untuk grapgrap") — conversational_detail_si
-- (V336, CW detail faktur, kaos saja) kini juga untuk grapgrap-manado. Efek tampak mengikuti rilis FE detail faktur CW.
-- Pola V326: idempoten (baris enabled=false ikut dinyalakan), gagal keras bila tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_detail_si'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_detail_si' AND enabled) THEN
        RAISE EXCEPTION 'V338: flag conversational_detail_si grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
