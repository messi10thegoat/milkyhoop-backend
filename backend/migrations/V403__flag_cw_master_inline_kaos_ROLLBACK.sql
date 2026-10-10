-- Rollback V403: cabut flag master inline dari kaos.
DELETE FROM tenant_features WHERE tenant_id = 'kaos-biru-konveksi'
  AND feature IN ('conversational_customer_inline', 'conversational_item_inline');
