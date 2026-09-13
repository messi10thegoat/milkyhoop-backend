-- ROLLBACK V246 — hc_ap_members persis definisi hidup sebelum V246 (V242).

BEGIN;

CREATE OR REPLACE FUNCTION public.hc_ap_members(p_tenant text)
 RETURNS TABLE(member_key text, amount numeric)
 LANGUAGE plpgsql
AS $function$
BEGIN
    PERFORM hc_scope_guard(p_tenant, 'hc_ap_members');
    RETURN QUERY
    WITH gl AS (
        SELECT je.id, je.source_type, je.source_id, SUM(jl.credit - jl.debit) AS net
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE coa.account_type = 'PAYABLE'
          AND is_effective_journal(je.id)
          AND je.tenant_id = p_tenant
        GROUP BY je.id, je.source_type, je.source_id
    ),
    sub AS (SELECT s.bill_id, s.outstanding FROM compute_ap_outstanding(p_tenant) s
            WHERE s.bill_id IS NOT NULL),
    per_bill AS (
        SELECT b.id AS bill_id,
            COALESCE((SELECT SUM(g.net) FROM gl g
                      WHERE g.source_type = 'BILL' AND g.source_id::text = b.id::text), 0)
          + COALESCE((SELECT SUM(g.net) FROM gl g
                      JOIN bill_payments_v2 p ON p.journal_id = g.id
                      JOIN bill_payment_allocations a ON a.payment_id = p.id
                      WHERE a.bill_id = b.id), 0) AS gl_net
        FROM bills b WHERE b.tenant_id = p_tenant
    )
    SELECT 'bill:' || pb.bill_id::text,
           (pb.gl_net - COALESCE(s.outstanding, 0))::numeric(18,2)
    FROM per_bill pb
    LEFT JOIN sub s ON s.bill_id = pb.bill_id
    WHERE pb.gl_net <> COALESCE(s.outstanding, 0);
END;
$function$;

COMMIT;
