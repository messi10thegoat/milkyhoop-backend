-- V414 (MASTER 12 Okt 2026) -- flag koreksi_dp_proforma KAOS SAJA: UI WORKSPACE koreksi nominal proforma terbit +
-- pindah uang muka antar-proforma (BE LIVE 2804693c: POST /proformas/{id}/correct-amount, /customer-deposits/{id}/move-proforma).
-- Tanpa rilis FE = tanpa efek. grapgrap & tenant lain WAJIB NOL. Pola V405/V406.
INSERT INTO tenant_features (tenant_id, feature)
SELECT 'kaos-biru-konveksi', 'koreksi_dp_proforma'
WHERE EXISTS (SELECT 1 FROM "Tenant" WHERE id = 'kaos-biru-konveksi')
ON CONFLICT (tenant_id, feature) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi' AND enabled
          AND feature = 'koreksi_dp_proforma') THEN
        RAISE EXCEPTION 'V414: koreksi_dp_proforma kaos tidak terpasang/aktif';
    END IF;
    IF EXISTS (SELECT 1 FROM tenant_features WHERE feature = 'koreksi_dp_proforma' AND tenant_id <> 'kaos-biru-konveksi') THEN
        RAISE EXCEPTION 'V414: koreksi_dp_proforma bocor ke tenant lain (grapgrap wajib NOL)';
    END IF;
END $$;
