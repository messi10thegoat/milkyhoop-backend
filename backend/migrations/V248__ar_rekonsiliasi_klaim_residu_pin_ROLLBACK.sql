-- ROLLBACK V248: kembalikan verify_ar_reconciliation_all ke badan pra-V248 (V188, BUTA — sadar), buang fungsi & tabel pin.
-- Pin tak "dihapus diam": baris audit kompensasi ditulis sebelum tabel dibuang.
BEGIN;

INSERT INTO audit_logs (id, "eventType", entity_type, entity_id, entity_number, tenant_id, source, metadata, success, "createdAt")
SELECT gen_random_uuid()::text, 'AR_RECONCILIATION_PIN_REMOVED', 'journal_line', p.journal_line_id, p.ticket, p.tenant_id,
       'migration:V248_ROLLBACK', to_jsonb(p), true, now()
FROM ar_reconciliation_pins p;

CREATE OR REPLACE FUNCTION public.verify_ar_reconciliation_all()
 RETURNS TABLE(tenant_id text, total_canonical numeric, total_gl numeric, total_drift numeric, is_exempt boolean, baseline_drift numeric, verdict text)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    IF NOT COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user), false)
       AND (current_setting('app.tenant_id', true) IS NULL OR current_setting('app.tenant_id', true) = '')
    THEN
        RAISE EXCEPTION 'verify_ar_reconciliation_all: no tenant scope (not BYPASSRLS, app.tenant_id unset) -- refusing to return false-GREEN';
    END IF;

    RETURN QUERY
    WITH tenants AS (
        SELECT DISTINCT je.tenant_id AS tid
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE coa.account_type = 'RECEIVABLE'
    ),
    per_tenant AS (
        SELECT t.tid,
               COALESCE(SUM(r.canonical_ar), 0)::NUMERIC(18,2) AS total_canon,
               COALESCE(SUM(r.gl_ar), 0)::NUMERIC(18,2)        AS total_gl,
               COALESCE(SUM(r.drift), 0)::NUMERIC(18,2)        AS total_drift
        FROM tenants t
        LEFT JOIN LATERAL public.verify_ar_reconciliation(t.tid) r ON TRUE
        GROUP BY t.tid
    )
    SELECT p.tid,
           p.total_canon,
           p.total_gl,
           p.total_drift,
           (e.tenant_id IS NOT NULL) AS is_exempt,
           COALESCE(e.baseline_drift, 0)::NUMERIC(18,2) AS baseline_drift,
           CASE
               WHEN e.tenant_id IS NULL AND ABS(p.total_drift) <= 0.01 THEN 'PASS'
               WHEN e.tenant_id IS NULL AND ABS(p.total_drift) >  0.01 THEN 'FAIL_NON_EXEMPT'
               WHEN e.tenant_id IS NOT NULL AND ABS(p.total_drift - e.baseline_drift) <= 0.01 THEN 'PASS_EXEMPT'
               ELSE 'FAIL_DRIFT_CHANGED'
           END AS verdict
    FROM per_tenant p
    LEFT JOIN ar_reconciliation_exemptions e ON e.tenant_id = p.tid
    ORDER BY (CASE WHEN e.tenant_id IS NULL AND ABS(p.total_drift) > 0.01 THEN 0
                   WHEN e.tenant_id IS NOT NULL AND ABS(p.total_drift - e.baseline_drift) > 0.01 THEN 0
                   ELSE 1 END), p.tid;
END;
$function$;

COMMENT ON FUNCTION verify_ar_reconciliation(text) IS NULL;
DROP FUNCTION verify_ar_reconciliation_rincian(text);
DROP FUNCTION ar_klaim_piutang(text);
DROP TABLE ar_reconciliation_pins;
DELETE FROM schema_migrations WHERE version = 'V248__ar_rekonsiliasi_klaim_residu_pin.sql';

COMMIT;
