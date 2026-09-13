-- V246 — pemeriksa check_7 (hc_ap_members) PROPORSIONAL, ditulis INDEPENDEN dari compute_ap_outstanding.
-- Harus di-deploy BERSAMA V245: tanpa ini check_7 memerah palsu pada pembayaran multi pertama.
-- Gerbang: scripts/gerbang_v245.py (gabungan V245+V246, sabotase dua arah).

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
          -- V246: PROPORSIONAL, ekspresi SENDIRI (independen dari fungsi yang diperiksa).
          -- bagian alokasi = ROUND(net jurnal pembayaran * applied / Σapplied pembayaran, 2);
          -- alokasi id terbesar menyerap sisa sen. Versi V242 menjumlahkan net SELURUH jurnal
          -- pembayaran ke setiap tagihan yang dialokasi -> hitung-ganda pada multi-alokasi.
          + COALESCE((SELECT SUM(
                    CASE WHEN (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id) > 0
                         THEN ROUND(g.net * a.amount_applied
                                    / (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id), 2)
                         ELSE 0 END
                  + CASE WHEN a.id = (SELECT a3.id FROM bill_payment_allocations a3 WHERE a3.payment_id = p.id ORDER BY a3.id DESC LIMIT 1)
                          AND (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id) > 0
                         THEN g.net - (SELECT SUM(ROUND(g.net * a4.amount_applied
                                                        / (SELECT SUM(a2.amount_applied) FROM bill_payment_allocations a2 WHERE a2.payment_id = p.id), 2))
                                       FROM bill_payment_allocations a4 WHERE a4.payment_id = p.id)
                         ELSE 0 END)
                FROM gl g
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
