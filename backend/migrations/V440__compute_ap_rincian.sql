-- V440 (22 Okt 2026, PEMBELIAN-SERAGAM unit (1), BACKEND; putusan MASTER): compute_ap_rincian(tenant) = RINCIAN pelunasan hutang
-- per tagihan dari JURNAL (Law 1/16/29), sumber tunggal sisa/terbayar Faktur Pembelian (daftar, summary, detail, mark_paid).
-- FUNGSI BARU; compute_ap_outstanding TIDAK DISENTUH (fungsi uang inti) -- pola V430 compute_ar_rincian.
--
-- Cabang = SALINAN cabang compute_ap_outstanding (V218, V245 proporsional, V314), dipecah per sumber:
--   payment_amount         = cabang 3 (bill_payments_v2, debit AP PROPORSIONAL per alokasi)
--   vendor_credit_amount   = cabang 4 (aplikasi nota debit ber-journal_id)
--   deposit_applied_amount = cabang 6 (DEPOSIT_APPLICATION uang muka vendor)
--   paid_amount = jumlah ketiganya; outstanding = bill_total (kredit AP jurnal BILL efektif) - paid_amount.
-- Beda dengan compute_ap_outstanding: SEMUA tagihan aktif (status_v2 bukan draft/void -- predikat SAMA) dikembalikan, juga yang
-- outstanding 0 (lunas, ATAU jurnal BILL-nya dibalik -> bill_total 0); tanpa baris sintetis nota debit lepas (vc_rows).
-- Jurnal ADJUSTMENT (dibaca BILL_JOURNAL_PAID_CTE lama) TIDAK dihitung -- sama dengan compute_ap_outstanding (0 jurnal hari ini).
-- Dua salinan logika = risiko kisi dua sumber -> GERBANG: scripts/ops/uji-v440-ap-rincian.py menegaskan, untuk SEMUA tagihan
-- SEMUA tenant: outstanding rincian == COALESCE(compute_ap_outstanding.outstanding, 0) dan bill_total/paid sama bila ada baris.
-- Harness membaca badan di antara penanda BADAN-MULAI/BADAN-SELESAI berkas ini (teks yang sama dengan fungsi).
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
    SELECT vca.bill_id AS b_id, COALESCE(SUM(jl.debit), 0) AS total_debit
    FROM vendor_credit_applications vca
    JOIN journal_entries je ON je.id = vca.journal_id
    JOIN journal_lines jl ON jl.journal_id = je.id
    JOIN chart_of_accounts coa ON coa.id = jl.account_id
    WHERE vca.tenant_id = p_tenant_id
      AND je.status = 'POSTED' AND je.reversed_by_id IS NULL
      AND coa.account_type = 'PAYABLE' AND jl.debit > 0
    GROUP BY vca.bill_id
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
  'V440: rincian pelunasan hutang per tagihan (pembayaran / nota debit / uang muka vendor) dari jurnal; cabang = salinan compute_ap_outstanding, digerbang outstanding sama untuk semua tagihan.';
