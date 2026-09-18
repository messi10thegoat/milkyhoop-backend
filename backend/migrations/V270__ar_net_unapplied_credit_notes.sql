-- V270__ar_net_unapplied_credit_notes.sql
-- Item 5 (MASTER Sabtu list): compute_ar_outstanding must NET unapplied credit notes.
--
-- PROBLEM: a credit note credits RECEIVABLE at POSTING time (Dr Retur/PPN, Cr Piutang).
-- When the CN has NO original_invoice_id (a floating customer credit), NO branch of
-- compute_ar_outstanding attributed that RECEIVABLE credit to any invoice, so the
-- per-invoice sum OVERSTATED AR by the CN amount vs the journal-derived RECEIVABLE
-- balance (Law 16 drift). Instance: kaos CN-2609-0006 (RAHAYU, Cr Piutang 25.000).
--
-- FIX (mirrors the AP twin compute_ap_outstanding's vc_unapplied / vc_rows, live since
-- V218/earlier): add a customer-level synthetic row for unapplied CNs.
--
-- NO DOUBLE COUNT: Branch 2 (payment_credits) already nets CNs with
-- original_invoice_id IS NOT NULL. cn_unapplied takes the DISJOINT complement
-- (original_invoice_id IS NULL) — a CN is in exactly one of the two, never both.
--
-- DEPOSITS ARE DELIBERATELY NOT NETTED. A customer deposit credits LIABILITY 2-10500
-- (Uang Muka Pelanggan), NOT RECEIVABLE. Applied deposits already reduce AR via
-- Branch 3 (DEPOSIT_APPLICATION Dr 2-10500 / Cr Piutang). Netting an UNAPPLIED deposit
-- into AR would (a) violate Law 16 (AR must equal journal-derived RECEIVABLE, which
-- excludes 2-10500) and (b) double-count the deposit liability (once on its own
-- balance-sheet line, once as an AR reduction). This is exactly symmetric with the AP
-- twin, which nets unapplied vendor CREDITS (touch PAYABLE) but only APPLIED vendor
-- deposits (vd_applied_debits).

CREATE OR REPLACE FUNCTION public.compute_ar_outstanding(p_tenant_id text)
 RETURNS TABLE(customer_id text, customer_name text, invoice_id uuid, invoice_number text, invoice_date date, due_date date, invoice_status text, invoice_total numeric, paid_amount numeric, outstanding numeric)
 LANGUAGE plpgsql
 STABLE
AS $function$
BEGIN
    RETURN QUERY
    WITH
    active_invoices AS (
        SELECT si.id, si.invoice_number, si.invoice_date, si.due_date,
               si.customer_id, si.customer_name, si.status, si.total_amount
        FROM sales_invoices si
        WHERE si.tenant_id = p_tenant_id
          AND si.status NOT IN ('draft', 'void')
    ),
    invoice_debits AS (
        SELECT je.source_id::uuid AS inv_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.debit > 0
          AND je.source_type = 'INVOICE'
        GROUP BY je.source_id::uuid
    ),
    payment_credits AS (
        -- Branch 1: receive_payment settlements (via allocations)
        -- V245 (13 Sep 2026): PROPORSIONAL per alokasi (kembar AP; lihat compute_ap_outstanding).
        -- Versi lama memberi SELURUH kredit AR jurnal penerimaan ke SETIAP faktur yang dialokasi;
        -- cache amount_paid (dihitung ulang dari fungsi ini) ikut ganda -> faktur "lunas" palsu.
        SELECT x.invoice_id AS inv_id,
               COALESCE(SUM(x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0
                                            THEN x.ar_credit - x.jumlah_bagian ELSE 0 END), 0) AS total_credit
        FROM (
            SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian
            FROM (
                SELECT rpa.invoice_id, rpa.payment_id, pb.ar_credit,
                       CASE WHEN SUM(rpa.amount_applied) OVER (PARTITION BY rpa.payment_id) > 0
                            THEN ROUND(pb.ar_credit * rpa.amount_applied
                                       / SUM(rpa.amount_applied) OVER (PARTITION BY rpa.payment_id), 2)
                            ELSE 0 END AS bagian,
                       ROW_NUMBER() OVER (PARTITION BY rpa.payment_id ORDER BY rpa.id DESC) AS urut_akhir
                FROM receive_payment_allocations rpa
                JOIN (
                    SELECT rp.id AS payment_id, COALESCE(SUM(jl.credit), 0) AS ar_credit
                    FROM receive_payments rp
                    JOIN journal_entries je ON je.id = rp.journal_id
                    JOIN journal_lines jl ON jl.journal_id = je.id
                    JOIN chart_of_accounts coa ON coa.id = jl.account_id
                    WHERE rp.tenant_id = p_tenant_id
                      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
                      AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
                    GROUP BY rp.id
                ) pb ON pb.payment_id = rpa.payment_id
            ) b
        ) x
        GROUP BY x.invoice_id

        UNION ALL

        -- Branch 2: credit-note applications (original_invoice_id link)
        SELECT cn.original_invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM credit_notes cn
        JOIN journal_entries je ON je.source_id::uuid = cn.id
            AND je.source_type = 'CREDIT_NOTE'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE cn.tenant_id = p_tenant_id
          AND cn.original_invoice_id IS NOT NULL
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY cn.original_invoice_id

        UNION ALL

        -- Branch 3 (FIX_P35_ARCANON): customer-deposit applications.
        SELECT cda.invoice_id AS inv_id, COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM customer_deposit_applications cda
        JOIN journal_entries je ON je.id = cda.journal_id
            AND je.source_type = 'DEPOSIT_APPLICATION'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE cda.tenant_id = p_tenant_id
          AND cda.status = 'active'
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'RECEIVABLE' AND jl.credit > 0
        GROUP BY cda.invoice_id
    ),
    aggregated_credits AS (
        SELECT pc.inv_id, SUM(pc.total_credit) AS total_credit
        FROM payment_credits pc
        GROUP BY pc.inv_id
    ),
    -- V270: unapplied credit notes (original_invoice_id IS NULL) — RECEIVABLE credit
    -- booked at CN posting but attributed to NO invoice. Net at customer level.
    -- DISJOINT from Branch 2 (which takes original_invoice_id IS NOT NULL) -> no double count.
    cn_unapplied AS (
        SELECT cn.customer_id,
               cn.customer_name,
               COALESCE(SUM(jl.credit), 0) AS total_credit,
               COALESCE(SUM(jl.debit), 0)  AS total_debit
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        JOIN credit_notes cn ON cn.id = je.source_id::uuid
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND je.source_type = 'CREDIT_NOTE'
          AND coa.account_type = 'RECEIVABLE'
          AND cn.original_invoice_id IS NULL
        GROUP BY cn.customer_id, cn.customer_name
    ),
    inv_rows AS (
        SELECT ai.customer_id::TEXT AS customer_id, ai.customer_name::TEXT AS customer_name,
               ai.id AS invoice_id, ai.invoice_number::TEXT AS invoice_number,
               ai.invoice_date, ai.due_date, ai.status::TEXT AS invoice_status,
               COALESCE(id2.total_debit, 0)::NUMERIC(18,2) AS invoice_total,
               COALESCE(ac.total_credit, 0)::NUMERIC(18,2) AS paid_amount,
               (COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0))::NUMERIC(18,2) AS outstanding
        FROM active_invoices ai
        LEFT JOIN invoice_debits id2 ON id2.inv_id = ai.id
        LEFT JOIN aggregated_credits ac ON ac.inv_id = ai.id
        WHERE COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0) != 0
    ),
    -- Synthetic customer-level row for unapplied CNs (mirror AP vc_rows). RECEIVABLE is
    -- debit-normal, so a CN credit yields NEGATIVE outstanding (reduces AR).
    cn_rows AS (
        SELECT cnu.customer_id::TEXT AS customer_id, cnu.customer_name::TEXT AS customer_name,
               NULL::UUID AS invoice_id, 'CREDIT-NOTE'::TEXT AS invoice_number,
               CURRENT_DATE AS invoice_date, CURRENT_DATE AS due_date,
               'credit_note'::TEXT AS invoice_status,
               cnu.total_credit::NUMERIC(18,2) AS invoice_total,
               cnu.total_debit::NUMERIC(18,2)  AS paid_amount,
               (cnu.total_debit - cnu.total_credit)::NUMERIC(18,2) AS outstanding
        FROM cn_unapplied cnu
    )
    SELECT * FROM (
        SELECT * FROM inv_rows
        UNION ALL
        SELECT * FROM cn_rows WHERE cn_rows.outstanding <> 0
    ) q
    ORDER BY q.customer_name, q.due_date;
END;
$function$;
