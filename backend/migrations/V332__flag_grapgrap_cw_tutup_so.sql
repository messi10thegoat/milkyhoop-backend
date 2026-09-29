-- V332 (pemilik 29 Sep 2026 ~10:55: "nyalakan flag tutup pesanan untuk grapgrap") — conversational_close_page_so
-- (V330, CW "Tutup pesanan", kaos saja) kini juga untuk grapgrap-manado. Efek tampak mengikuti rilis FE r184.
-- Pola V326: idempoten (baris enabled=false ikut dinyalakan), gagal keras bila tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', 'conversational_close_page_so'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features
                   WHERE tenant_id = 'grapgrap-manado' AND feature = 'conversational_close_page_so' AND enabled) THEN
        RAISE EXCEPTION 'V332: flag conversational_close_page_so grapgrap-manado tidak mendarat/aktif';
    END IF;
END $$;
