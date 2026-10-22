-- V441 (22 Okt 2026, PEMBELIAN-SERAGAM unit (3a), BACKEND; putusan pemilik LANGSUNG 22 Okt): atribusi NOTA DEBIT ke tagihan.
-- Terapkan nota debit = ATRIBUSI tanpa jurnal, seluruh nilai ke SATU tagihan (kembar Nota Kredit / compute_ar_outstanding cabang 2;
-- pola NetSuite apply vendor credit). Mengubah:
--   compute_ap_outstanding : cabang 4 vc_applied_debits via vendor_credits.original_bill_id (dulu JOIN vca.journal_id yang tak pernah
--                            diisi); cabang 5 vc_unapplied = nota debit bebas, debit AP VENDOR_CREDIT − kredit AP VENDOR_CREDIT_REFUND.
--   compute_ap_rincian     : cabang c_vendor_credit = salinan cabang 4 baru (gerbang V440 tetap: rincian == compute_ap_outstanding).
-- Data hari ini: vendor_credits = 0 baris di SEMUA tenant -> hasil kedua fungsi IDENTIK sebelum/sesudah (digerbang).
-- Gerbang: scripts/gate_v441_nota_debit.sh. Rollback: V441__..._ROLLBACK.sql = definisi PERSIS sebelum V441.

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
    -- 4. V441 (22 Okt 2026, putusan pemilik): nota debit TERTAUT tagihan (original_bill_id) -- KEMBAR cabang 2
    --    compute_ar_outstanding (nota kredit). Terapkan = ATRIBUSI tanpa jurnal (pola NetSuite apply credit): debit AP
    --    jurnal VENDOR_CREDIT efektif dari nota debit yang menunjuk tagihan ini. Dulu JOIN vca.journal_id yang tak
    --    pernah diisi -> aplikasi tak pernah mengurangi sisa tagihan.
    vc_applied_debits AS (
        SELECT vc.original_bill_id AS bill_id, COALESCE(SUM(jl.debit), 0) AS total_debit
        FROM vendor_credits vc
        JOIN journal_entries je ON je.source_id::uuid = vc.id
            AND je.source_type = 'VENDOR_CREDIT'
        JOIN journal_lines jl ON jl.journal_id = je.id
        JOIN chart_of_accounts coa ON coa.id = jl.account_id
        WHERE vc.tenant_id = p_tenant_id
          AND vc.original_bill_id IS NOT NULL
          AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
          AND coa.account_type = 'PAYABLE' AND jl.debit > 0
        GROUP BY vc.original_bill_id
    ),
    -- 5. V441: nota debit BEBAS (original_bill_id IS NULL) -- tingkat vendor, DISJOINT dari cabang 4. Debit AP jurnal
    --    VENDOR_CREDIT dikurangi kredit AP jurnal VENDOR_CREDIT_REFUND (dana dikembalikan vendor; dulu tak dihitung ->
    --    kredit vendor tetap tampil sesudah refund). Pembalik void (reversal_of_id) dan jurnal yang dibalik tak dihitung (V314).
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
          AND je.reversal_of_id IS NULL
          AND je.source_type IN ('VENDOR_CREDIT', 'VENDOR_CREDIT_REFUND')
          AND coa.account_type = 'PAYABLE'
          AND vc.original_bill_id IS NULL
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
               tanggal_bisnis(p_tenant_id) AS bill_date,
               tanggal_bisnis(p_tenant_id) AS due_date,
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

CREATE OR REPLACE FUNCTION public.compute_ap_rincian(p_tenant_id text)
RETURNS TABLE(bill_id uuid, bill_total numeric, payment_amount numeric, vendor_credit_amount numeric,
              deposit_applied_amount numeric, paid_amount numeric, outstanding numeric)
LANGUAGE sql STABLE
AS $fn$
-- BADAN-MULAI
WITH
active_bills AS (
    SELECT b.id
    FROM bills b
    WHERE b.tenant_id = p_tenant_id
      AND b.status_v2 NOT IN ('draft', 'void')
),
bill_credits AS (
    SELECT je.source_id::uuid AS b_id, COALESCE(SUM(jl.credit), 0) AS total_credit
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = p_tenant_id
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
      AND coa.account_type = 'PAYABLE' AND jl.credit > 0
      AND je.source_type = 'BILL'
    GROUP BY je.source_id::uuid
),
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
c_payment AS (
    SELECT x.bill_id AS b_id,
           COALESCE(SUM(x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0
                                        THEN x.ap_debit - x.jumlah_bagian ELSE 0 END), 0) AS total_debit
    FROM (SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian FROM pd_bagian b) x
    GROUP BY x.bill_id
),
c_vendor_credit AS (
    -- V441: nota debit TERTAUT (original_bill_id), salinan cabang 4 compute_ap_outstanding V441 (kembar AR cabang 2)
    SELECT vc.original_bill_id AS b_id, COALESCE(SUM(jl.debit), 0) AS total_debit
    FROM vendor_credits vc
    JOIN journal_entries je ON je.source_id::uuid = vc.id
        AND je.source_type = 'VENDOR_CREDIT'
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE vc.tenant_id = p_tenant_id
      AND vc.original_bill_id IS NOT NULL
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
      AND coa.account_type = 'PAYABLE' AND jl.debit > 0
    GROUP BY vc.original_bill_id
),
c_deposit AS (
    SELECT vda.bill_id AS b_id, COALESCE(SUM(jl.debit), 0) AS total_debit
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
r AS (
    SELECT ab.id AS b_id,
           COALESCE(bc.total_credit, 0) AS total_credit,
           COALESCE(pd.total_debit, 0) AS pay,
           COALESCE(vc.total_debit, 0) AS vc,
           COALESCE(vd.total_debit, 0) AS dp
    FROM active_bills ab
    LEFT JOIN bill_credits bc ON bc.b_id = ab.id
    LEFT JOIN c_payment pd ON pd.b_id = ab.id
    LEFT JOIN c_vendor_credit vc ON vc.b_id = ab.id
    LEFT JOIN c_deposit vd ON vd.b_id = ab.id
)
SELECT r.b_id,
       r.total_credit::NUMERIC(18,2),
       r.pay::NUMERIC(18,2),
       r.vc::NUMERIC(18,2),
       r.dp::NUMERIC(18,2),
       (r.pay + r.vc + r.dp)::NUMERIC(18,2),
       (r.total_credit - (r.pay + r.vc + r.dp))::NUMERIC(18,2)
FROM r
-- BADAN-SELESAI
$fn$;

COMMENT ON FUNCTION public.compute_ap_rincian(text) IS
  'V440+V441: rincian pelunasan hutang per tagihan (pembayaran / nota debit / uang muka vendor) dari jurnal; cabang = salinan compute_ap_outstanding, digerbang outstanding sama untuk semua tagihan.';
