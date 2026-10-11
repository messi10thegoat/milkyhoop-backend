-- Rollback V404: cabut flag master inline dari grapgrap.
DELETE FROM tenant_features WHERE tenant_id = 'grapgrap-manado'
  AND feature IN ('conversational_customer_inline', 'conversational_item_inline');
