-- ROLLBACK V258 — cabut 8 hibah modul ADMIN.
DELETE FROM role_permissions
WHERE role_id = (SELECT id FROM roles WHERE code = 'ADMIN')
  AND module IN ('BRANCH','COST_CENTER','PRICE_LIST','CURRENCY','RECIPE','KDS','PROFORMA','ITEM_BATCH');
