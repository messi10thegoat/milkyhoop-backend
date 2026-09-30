-- V337 (pemilik 30 Sep 2026: "Kasbon aktif saja di grapgrap") — conversational_kasbon_page
-- (V334, CW kasbon, kaos saja) kini juga untuk grapgrap-manado. Efek tampak mengikuti rilis FE modul kasbon.
-- Pola V326: idempoten (baris enabled=false ikut dinyalakan), gagal keras bila tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_kasbon_page'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_kasbon_page' AND enabled) THEN
        RAISE EXCEPTION 'V337: flag conversational_kasbon_page grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
