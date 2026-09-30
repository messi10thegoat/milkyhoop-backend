-- Rollback V351: cabut tiga flag F2 SI dari grapgrap (kaos tak tersentuh).
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('conversational_payment_page_si', 'conversational_edit_page_si', 'conversational_creditnote_page_si');
