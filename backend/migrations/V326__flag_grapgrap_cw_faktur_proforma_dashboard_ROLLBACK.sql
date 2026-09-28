-- Rollback V326: cabut ketiga flag dari grapgrap (kaos tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('conversational_invoice_page_so', 'conversational_proforma_page_so', 'dashboard_v2');
