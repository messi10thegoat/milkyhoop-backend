-- Rollback V489: matikan ui_v3_detail di nyawit + grapgrap (rupa detail lama PERSIS kembali; tanpa restart).
DELETE FROM tenant_features WHERE tenant_id IN ('pt-kita-nyawit-bersama', 'grapgrap-manado') AND feature = 'ui_v3_detail';
