-- ROLLBACK V242 — cabut fungsi pemeriksaan + patok.
-- Sesudah ini skrip harian yang memanggil hc_verdict akan melaporkan BROKEN
-- (__GAGAL__), bukan lulus. Kembalikan skripnya juga (git revert commit ini).
-- NOL data pembukuan disentuh oleh V242 maupun rollback ini.

BEGIN;

DROP FUNCTION IF EXISTS hc_verdict(text, text);
DROP FUNCTION IF EXISTS hc_status_desync_members(text);
DROP FUNCTION IF EXISTS hc_inventory_members(text);
DROP FUNCTION IF EXISTS hc_inventory_drift(text);
DROP FUNCTION IF EXISTS hc_ap_drift(text);
DROP FUNCTION IF EXISTS hc_ap_members(text);
DROP FUNCTION IF EXISTS hc_scope_guard(text, text);
DROP TABLE IF EXISTS health_check_exemptions;

COMMIT;
