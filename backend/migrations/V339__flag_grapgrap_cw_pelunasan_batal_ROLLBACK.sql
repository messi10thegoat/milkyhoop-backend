-- Rollback V339: cabut kedua flag dari grapgrap (kaos tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('conversational_payment_page_so', 'conversational_cancel_page_so');
