-- V326 (pemilik LANGSUNG 28 Sep 2026: "grapgrap kasih flag aja sekalian") — tiga flag yang sebelumnya kaos saja kini juga
-- untuk grapgrap-manado: conversational_invoice_page_so (V322, CW "Buat faktur"), conversational_proforma_page_so (V325,
-- CW "Terbitkan proforma"), dashboard_v2 (V324, sidebar + dashboard baru). Efek tampak mengikuti rilis FE masing-masing.
-- Pola V321: idempoten (baris enabled=false ikut dinyalakan), gagal keras bila ketiganya tak mendarat/aktif.

INSERT INTO tenant_features (tenant_id, feature)
SELECT 'grapgrap-manado', f
FROM unnest(ARRAY['conversational_invoice_page_so', 'conversational_proforma_page_so', 'dashboard_v2']) AS f
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'grapgrap-manado')
ON CONFLICT (tenant_id, feature) DO UPDATE SET enabled = true, updated_at = now();

DO $$
BEGIN
    IF (SELECT count(*) FROM tenant_features
        WHERE tenant_id = 'grapgrap-manado' AND enabled
          AND feature IN ('conversational_invoice_page_so', 'conversational_proforma_page_so', 'dashboard_v2')) <> 3 THEN
        RAISE EXCEPTION 'V326: flag grapgrap-manado tidak lengkap/aktif';
    END IF;
END $$;
