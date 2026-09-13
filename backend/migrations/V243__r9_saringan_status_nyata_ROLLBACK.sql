-- ROLLBACK V243 — kembalikan R9 ke keadaan V242.
-- Skrip harian juga harus dikembalikan (git revert commit V243): check_3 versi
-- baru memanggil hc_verdict('bank_sync') yang sesudah rollback ini mengangkat
-- galat -> BROKEN, bukan lulus.
-- NOL data pembukuan disentuh oleh V243 maupun rollback ini.

BEGIN;

DELETE FROM health_check_exemptions WHERE check_name = 'bank_sync';
ALTER TABLE health_check_exemptions DROP CONSTRAINT health_check_exemptions_check_name_check;
ALTER TABLE health_check_exemptions ADD CONSTRAINT health_check_exemptions_check_name_check
    CHECK (check_name IN ('ap_invariant', 'inventory_value', 'status_desync'));

-- hc_verdict persis V242 (tanpa cabang bank_sync)
CREATE OR REPLACE FUNCTION hc_verdict(p_check text, p_tenant text)
RETURNS TABLE (drift numeric, member_count integer, members_sum numeric,
               fingerprint text, verdict text)
LANGUAGE plpgsql AS $$
DECLARE
    v_drift numeric;
    v_cnt integer;
    v_sum numeric;
    v_fp text;
    e health_check_exemptions%ROWTYPE;
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_verdict');

    IF p_check = 'ap_invariant' THEN
        v_drift := hc_ap_drift(p_tenant);
        SELECT count(*), COALESCE(sum(m.amount), 0),
               COALESCE(md5(string_agg(m.member_key || ':' || m.amount::text, ',' ORDER BY m.member_key)), '')
          INTO v_cnt, v_sum, v_fp FROM hc_ap_members(p_tenant) m;
    ELSIF p_check = 'inventory_value' THEN
        v_drift := hc_inventory_drift(p_tenant);
        SELECT count(*), COALESCE(sum(m.amount), 0),
               COALESCE(md5(string_agg(m.member_key || ':' || m.amount::text, ',' ORDER BY m.member_key)), '')
          INTO v_cnt, v_sum, v_fp FROM hc_inventory_members(p_tenant) m;
    ELSIF p_check = 'status_desync' THEN
        v_drift := NULL;
        SELECT count(*), COALESCE(sum(m.amount), 0),
               COALESCE(md5(string_agg(m.member_key || ':' || m.amount::text, ',' ORDER BY m.member_key)), '')
          INTO v_cnt, v_sum, v_fp FROM hc_status_desync_members(p_tenant) m;
    ELSE
        RAISE EXCEPTION 'hc_verdict: pemeriksaan tak dikenal %', p_check;
    END IF;

    SELECT * INTO e FROM health_check_exemptions x
     WHERE x.check_name = p_check AND x.tenant_id = p_tenant;

    drift := v_drift; member_count := v_cnt; members_sum := v_sum; fingerprint := v_fp;

    IF COALESCE(v_drift, 0) = 0 AND v_cnt = 0 THEN
        verdict := 'PASS';
    ELSIF e.check_name IS NULL THEN
        verdict := 'FAIL_NON_EXEMPT';
    ELSIF v_fp = e.baseline_fingerprint
          AND v_cnt = e.baseline_count
          AND v_sum = e.baseline_amount
          AND (v_drift IS NULL OR v_drift = v_sum)
    THEN
        verdict := 'PASS_EXEMPT';
    ELSE
        verdict := 'FAIL_DRIFT_CHANGED';
    END IF;
    RETURN NEXT;
END;
$$;

DROP FUNCTION IF EXISTS hc_bank_sync_drift(text);
DROP FUNCTION IF EXISTS hc_bank_sync_members(text);

COMMIT;
