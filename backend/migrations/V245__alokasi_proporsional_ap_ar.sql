-- V245 — compute_ap_outstanding / compute_ar_outstanding: pembayaran multi-alokasi PROPORSIONAL.
--
-- Cacat (terukur lewat eksekusi, handler nyata, ROLLBACK, 13 Sep 2026): pembayaran 2.000 dengan
-- dua alokasi 1.000 menurunkan outstanding MASING-MASING dokumen 2.000. GL BENAR di kedua sisi —
-- buku besar pemilik tidak salah; yang salah laporan per dokumen. Cache AR (dihitung ulang dari
-- fungsi) ikut ganda -> faktur bisa tampil "lunas" padahal belum. Kedua form FE membangun
-- multi-alokasi. Data hari ini: 0 pembayaran multi-alokasi -> nol dokumen historis berubah.
--
-- Hanya dua CTE diganti (dibangkitkan dari definisi hidup, jangkar di-assert). ROLLBACK =
-- definisi hidup sebelum V245, apa adanya.

BEGIN;

CREATE OR REPLACE FUNCTION public.compute_ap_outstanding(p_tenant_id text)
 RETURNS TABLE(vendor_id uuid, vendor_name text, bill_id uuid, bill_number text, bill_date date, due_date date, bill_status text, bill_total numeric, paid_amount numeric, outstanding numeric)
 LANGUAGE sql
 STABLE
AS $function$
    WITH
    -- 1. Active bills (non-draft, non-void)
    active_bills AS (
        SELECT b.id, b.invoice_number, b.issue_date, b.due_date,
               b.vendor_id, b.vendor_name, b.status_v2, b.amount
        FROM bills b
        WHERE b.tenant_id = p_tenant_id
          AND b.status_v2 NOT IN ('draft', 'void')
    ),
    -- 2. Bill obligation: credits on PAYABLE from BILL journals
    bill_credits AS (
        SELECT je.source_id::uuid AS bill_id,
               COALESCE(SUM(jl.credit), 0) AS total_credit
        FROM journal_lines jl
        JOIN journal_entries je ON je.id = jl.journal_id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE'
          AND jl.credit > 0
          AND je.source_type = 'BILL'
        GROUP BY je.source_id::uuid
    ),
    -- 3. Bill payments: debits on PAYABLE from bill_payment_allocations
    -- V245 (13 Sep 2026): PROPORSIONAL per alokasi. Versi lama menjumlahkan SELURUH debit AP
    -- jurnal pembayaran untuk SETIAP tagihan yang dialokasi -> pembayaran 2.000 ke 2 tagihan
    -- menurunkan outstanding MASING-MASING 2.000 (terukur lewat eksekusi). GL benar; laporan per
    -- tagihan salah. Debit AP jurnal = Σ amount_applied terukur pada lebih bayar/diskon/biaya,
    -- jadi bagian tak teralokasi TIDAK ikut dibagi. Satu alokasi -> identik dgn versi lama.
    pd_bayar AS (
        SELECT bpv2.id AS payment_id, COALESCE(SUM(jl.debit), 0) AS ap_debit
        FROM bill_payments_v2 bpv2
        JOIN journal_entries je ON je.id = bpv2.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE bpv2.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY bpv2.id
    ),
    pd_bagian AS (
        SELECT bpa.bill_id, bpa.payment_id, bpa.id AS alloc_id, pb.ap_debit,
               CASE WHEN SUM(bpa.amount_applied) OVER (PARTITION BY bpa.payment_id) > 0
                    THEN ROUND(pb.ap_debit * bpa.amount_applied
                               / SUM(bpa.amount_applied) OVER (PARTITION BY bpa.payment_id), 2)
                    ELSE 0 END AS bagian,
               ROW_NUMBER() OVER (PARTITION BY bpa.payment_id ORDER BY bpa.id DESC) AS urut_akhir
        FROM bill_payment_allocations bpa
        JOIN pd_bayar pb ON pb.payment_id = bpa.payment_id
    ),
    payment_debits AS (
        SELECT x.bill_id,
               COALESCE(SUM(x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0
                                            THEN x.ap_debit - x.jumlah_bagian ELSE 0 END), 0) AS total_debit
        FROM (SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian FROM pd_bagian b) x
        GROUP BY x.bill_id
    ),
    -- 4. Vendor credit applications to specific bills (debits on PAYABLE)
    vc_applied_debits AS (
        SELECT vca.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM vendor_credit_applications vca
        JOIN journal_entries je ON je.id = vca.journal_id
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE vca.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY vca.bill_id
    ),
    -- 5. Unapplied vendor credits: VENDOR_CREDIT journals on PAYABLE not tied to bills
    --    These are net debit/credit on PAYABLE per vendor.
    --    Debits = VC creation (reduce AP), Credits = VC void reversals (increase AP).
    vc_unapplied AS (
        SELECT vc.vendor_id,
               vc.vendor_name,
               COALESCE(SUM(jl.credit), 0) AS total_credit,
               COALESCE(SUM(jl.debit), 0)  AS total_debit
        FROM journal_entries je
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        JOIN vendor_credits vc ON vc.id = je.source_id::uuid
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED'
          AND je.reversed_by_id IS NULL
          AND je.source_type = 'VENDOR_CREDIT'
          AND coa.account_type = 'PAYABLE'
          -- Exclude applied VCs (they're handled in vc_applied_debits)
          AND NOT EXISTS (
              SELECT 1 FROM vendor_credit_applications vca
              WHERE vca.vendor_credit_id = vc.id
                AND vca.journal_id = je.id
          )
        GROUP BY vc.vendor_id, vc.vendor_name
    ),
    -- 6. Vendor-deposit applications to specific bills (debits on PAYABLE).  [V218]
    --    See header: a DEPOSIT_APPLICATION journal's Dr 2-10100 settles the bill and
    --    is caught by no other CTE. Scoped via je.tenant_id (vda has no tenant_id).
    --    Fan-out-safe under the code-enforced 1 journal : 1 vda : 1 debit-line invariant.
    vd_applied_debits AS (
        SELECT vda.bill_id,
               COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM vendor_deposit_applications vda
        JOIN journal_entries je ON je.id = vda.journal_id
            AND je.source_type = 'DEPOSIT_APPLICATION'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE je.tenant_id = p_tenant_id
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY vda.bill_id
    ),
    -- Combine bill rows
    bill_rows AS (
        SELECT ab.vendor_id, ab.vendor_name::TEXT, ab.id AS bill_id,
               ab.invoice_number::TEXT AS bill_number, ab.issue_date AS bill_date,
               ab.due_date, ab.status_v2::TEXT AS bill_status,
               COALESCE(bc.total_credit, 0)::NUMERIC(18,2) AS bill_total,
               (COALESCE(pd.total_debit, 0) + COALESCE(vcd.total_debit, 0) + COALESCE(vdd.total_debit, 0))::NUMERIC(18,2) AS paid_amount,
               (COALESCE(bc.total_credit, 0) - COALESCE(pd.total_debit, 0) - COALESCE(vcd.total_debit, 0) - COALESCE(vdd.total_debit, 0))::NUMERIC(18,2) AS outstanding
        FROM active_bills ab
        LEFT JOIN bill_credits bc ON bc.bill_id = ab.id
        LEFT JOIN payment_debits pd ON pd.bill_id = ab.id
        LEFT JOIN vc_applied_debits vcd ON vcd.bill_id = ab.id
        LEFT JOIN vd_applied_debits vdd ON vdd.bill_id = ab.id
    ),
    -- Unapplied VC rows (synthetic — no bill, vendor-level)
    vc_rows AS (
        SELECT vcu.vendor_id,
               vcu.vendor_name::TEXT,
               NULL::UUID AS bill_id,
               'VENDOR-CREDIT'::TEXT AS bill_number,
               CURRENT_DATE AS bill_date,
               CURRENT_DATE AS due_date,
               'vendor_credit'::TEXT AS bill_status,
               vcu.total_credit::NUMERIC(18,2) AS bill_total,
               vcu.total_debit::NUMERIC(18,2) AS paid_amount,
               (vcu.total_credit - vcu.total_debit)::NUMERIC(18,2) AS outstanding
        FROM vc_unapplied vcu
    )
    SELECT * FROM bill_rows WHERE outstanding != 0
    UNION ALL
    SELECT * FROM vc_rows WHERE outstanding != 0
    ORDER BY vendor_name, due_date;
$function$;

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
        -- The DEPOSIT_APPLICATION journal's Cr RECEIVABLE line carries no invoice
        -- ref; per-invoice attribution comes from cda.invoice_id. Only the active,
        -- non-reversed application settles (status='active' is 1:1 with the
        -- journal's reversed_by_id IS NULL — belt-and-suspenders both checked).
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
    )
    SELECT ai.customer_id::TEXT, ai.customer_name::TEXT, ai.id,
           ai.invoice_number::TEXT, ai.invoice_date, ai.due_date,
           ai.status::TEXT,
           COALESCE(id2.total_debit, 0)::NUMERIC(18,2),
           COALESCE(ac.total_credit, 0)::NUMERIC(18,2),
           (COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0))::NUMERIC(18,2)
    FROM active_invoices ai
    LEFT JOIN invoice_debits id2 ON id2.inv_id = ai.id
    LEFT JOIN aggregated_credits ac ON ac.inv_id = ai.id
    WHERE COALESCE(id2.total_debit, 0) - COALESCE(ac.total_credit, 0) != 0
    ORDER BY ai.customer_name, ai.due_date;
END;
$function$;

COMMIT;
