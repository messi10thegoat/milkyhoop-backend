-- V430 (20 Okt 2026, S1-U3, BACKEND; putusan MASTER opsi b): compute_ar_rincian(tenant) = RINCIAN pelunasan piutang
-- per faktur dari JURNAL (Law 1/16/29), untuk payment_summary detail faktur (penerimaan / uang muka diterapkan / nota
-- kredit / sisa). FUNGSI BARU; compute_ar_outstanding TIDAK DISENTUH (fungsi uang inti).
--
-- Cabang = SALINAN cabang compute_ar_outstanding (V245 proporsional, FIX_P35_ARCANON, V299), dipecah per sumber:
--   receipt_amount     = cabang 1 (penerimaan, PROPORSIONAL per alokasi) + cabang 4 (lepas pembayaran, NEGATIF)
--   credit_note_amount = cabang 2 (nota kredit ber-original_invoice_id)
--   dp_applied_amount  = cabang 3 (DEPOSIT_APPLICATION, aplikasi aktif)
--   paid_amount = jumlah ketiganya; outstanding = invoice_total (debit AR jurnal INVOICE) - paid_amount.
-- Beda dengan compute_ar_outstanding: SEMUA faktur terbit (bukan draf/void) dikembalikan, juga yang outstanding 0
-- (lunas); tanpa baris sintetis NK lepas (cn_rows) -- per faktur saja.
-- Dua salinan logika = risiko kisi dua sumber -> GERBANG: scripts/ops/uji-v430-rincian.py menegaskan, untuk SEMUA faktur
-- SEMUA tenant: outstanding rincian == COALESCE(compute_ar_outstanding.outstanding, 0) dan invoice_total sama.
-- Harness membaca badan di antara penanda BADAN-MULAI/BADAN-SELESAI berkas ini (teks yang sama dengan fungsi).
CREATE OR REPLACE FUNCTION public.compute_ar_rincian(p_tenant_id text)
RETURNS TABLE(invoice_id uuid, invoice_total numeric, receipt_amount numeric, dp_applied_amount numeric,
              credit_note_amount numeric, paid_amount numeric, outstanding numeric)
LANGUAGE sql STABLE
AS $fn$
-- BADAN-MULAI
WITH
active_invoices AS (
    SELECT si.id
    FROM sales_invoices si
    WHERE si.tenant_id = p_tenant_id
      AND si.status NOT IN ('draft', 'void')
),
invoice_debits AS (
    SELECT je.source_id::uuid AS inv_id, COALESCE(SUM(jl.debit), 0) AS total_debit
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = p_tenant_id
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
      AND coa.account_type = 'RECEIVABLE' AND jl.debit > 0
      AND je.source_type = 'INVOICE'
    GROUP BY je.source_id::uuid
),
c_receipt AS (
    -- cabang 1 compute_ar_outstanding (V245): kredit AR jurnal penerimaan dibagi PROPORSIONAL per alokasi
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
),
c_credit_note AS (
    -- cabang 2: nota kredit ber-original_invoice_id
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
),
c_deposit AS (
    -- cabang 3 (FIX_P35_ARCANON): penerapan uang muka
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
c_unapply AS (
    -- cabang 4 (V299): lepas pembayaran mendebit Piutang -> kredit NEGATIF
    SELECT je.source_id::uuid AS inv_id, -COALESCE(SUM(jl.debit), 0) AS total_credit
    FROM journal_entries je
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE je.tenant_id = p_tenant_id
      AND je.source_type = 'RECEIVE_PAYMENT_UNAPPLY'
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
      AND coa.account_type = 'RECEIVABLE' AND jl.debit > 0
    GROUP BY je.source_id::uuid
),
r AS (
    SELECT ai.id AS inv_id,
           COALESCE(d.total_debit, 0) AS total_debit,
           COALESCE(rc.total_credit, 0) + COALESCE(ua.total_credit, 0) AS receipt,
           COALESCE(dp.total_credit, 0) AS dp,
           COALESCE(cn.total_credit, 0) AS cn
    FROM active_invoices ai
    LEFT JOIN invoice_debits d ON d.inv_id = ai.id
    LEFT JOIN c_receipt rc ON rc.inv_id = ai.id
    LEFT JOIN c_unapply ua ON ua.inv_id = ai.id
    LEFT JOIN c_deposit dp ON dp.inv_id = ai.id
    LEFT JOIN c_credit_note cn ON cn.inv_id = ai.id
)
SELECT r.inv_id,
       r.total_debit::NUMERIC(18,2),
       r.receipt::NUMERIC(18,2),
       r.dp::NUMERIC(18,2),
       r.cn::NUMERIC(18,2),
       (r.receipt + r.dp + r.cn)::NUMERIC(18,2),
       (r.total_debit - (r.receipt + r.dp + r.cn))::NUMERIC(18,2)
FROM r
-- BADAN-SELESAI
$fn$;

COMMENT ON FUNCTION public.compute_ar_rincian(text) IS
  'V430: rincian pelunasan piutang per faktur (penerimaan+lepas / uang muka / nota kredit) dari jurnal; cabang = salinan compute_ar_outstanding, digerbang outstanding sama untuk semua faktur.';
