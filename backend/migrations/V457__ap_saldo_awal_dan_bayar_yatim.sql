-- V457 (BACKEND2 23 Okt 2026; arah DISETUJUI LANGSUNG pemilik "Ya, bangun perbaikannya"; terapan = izin terpisah) -- audit F2 dashboard T2.
-- compute_ap_outstanding: dua baris SINTETIS tingkat vendor (pola vc_rows) supaya subledger == GL hutang:
--   (1) 'OPENING-BALANCE'  : jurnal VENDOR_OPENING_BALANCE efektif (source_id = vendor) -> kredit - debit pada akun PAYABLE (kaos Δ 1.000).
--   (2) 'VENDOR-PAYMENT'   : bagian pembayaran keluar (bill_payments_v2, jurnal efektif) yang TAK jatuh ke tagihan aktif -- pembayaran tanpa
--                            alokasi, atau alokasi ke tagihan yang sudah void/draf -> kredit vendor (outstanding negatif). Atribusi per alokasi
--                            memakai rumus pd_bagian YANG SAMA (bagian + sisa ke alokasi terakhir), sehingga Σ atribusi = debit AP jurnal
--                            (kaos BP-2609-0001 100.000 teralokasi ke BILL-2609-0002 yang void).
-- Nol jurnal; hanya rumus baca. Ditambal dari badan SEKARANG (pg_get_functiondef + anchor gagal-keras). Jalankan -v ON_ERROR_STOP=1 -1.
DO $$
DECLARE v_def text; v_baru text;
BEGIN
    v_def := pg_get_functiondef('compute_ap_outstanding(text)'::regprocedure);
    IF v_def LIKE '%OPENING-BALANCE%' THEN
        RAISE NOTICE 'V457: sudah terpasang, lewati';
        RETURN;
    END IF;
    v_baru := replace(v_def,
        E'    SELECT * FROM bill_rows WHERE outstanding != 0\n    UNION ALL\n    SELECT * FROM vc_rows WHERE outstanding != 0\n',
        E'    , -- V457 (1) saldo awal vendor tanpa tagihan\n'
        || E'    ob_rows AS (\n'
        || E'        SELECT je.source_id AS vendor_id, MAX(v.name)::TEXT AS vendor_name, NULL::UUID AS bill_id, ''OPENING-BALANCE''::TEXT AS bill_number,\n'
        || E'               tanggal_bisnis(p_tenant_id) AS bill_date, tanggal_bisnis(p_tenant_id) AS due_date, ''opening_balance''::TEXT AS bill_status,\n'
        || E'               COALESCE(SUM(jl.credit), 0)::NUMERIC(18,2) AS bill_total, COALESCE(SUM(jl.debit), 0)::NUMERIC(18,2) AS paid_amount,\n'
        || E'               (COALESCE(SUM(jl.credit), 0) - COALESCE(SUM(jl.debit), 0))::NUMERIC(18,2) AS outstanding\n'
        || E'        FROM journal_entries je JOIN journal_lines jl ON jl.journal_id = je.id JOIN chart_of_accounts coa ON coa.id = jl.account_id\n'
        || E'        LEFT JOIN vendors v ON v.id = je.source_id\n'
        || E'        WHERE je.tenant_id = p_tenant_id AND je.source_type = ''VENDOR_OPENING_BALANCE'' AND je.status = ''POSTED''\n'
        || E'          AND je.reversed_by_id IS NULL AND je.reversal_of_id IS NULL AND coa.account_type = ''PAYABLE''\n'
        || E'        GROUP BY je.source_id\n'
        || E'    ),\n'
        || E'    -- V457 (2) pembayaran keluar yang tak jatuh ke tagihan aktif -> kredit vendor\n'
        || E'    bayar_yatim AS (\n'
        || E'        SELECT o.payment_id, SUM(o.nilai) AS nilai FROM (\n'
        || E'            SELECT x.payment_id, x.bagian + CASE WHEN x.urut_akhir = 1 AND x.jumlah_bagian > 0 THEN x.ap_debit - x.jumlah_bagian ELSE 0 END AS nilai\n'
        || E'            FROM (SELECT b.*, SUM(b.bagian) OVER (PARTITION BY b.payment_id) AS jumlah_bagian FROM pd_bagian b) x\n'
        || E'            WHERE x.bill_id NOT IN (SELECT ab.id FROM active_bills ab)\n'
        || E'            UNION ALL\n'
        || E'            SELECT pb.payment_id, pb.ap_debit FROM pd_bayar pb\n'
        || E'            WHERE NOT EXISTS (SELECT 1 FROM bill_payment_allocations a0 WHERE a0.payment_id = pb.payment_id)\n'
        || E'        ) o GROUP BY o.payment_id\n'
        || E'    ),\n'
        || E'    yatim_rows AS (\n'
        || E'        SELECT p2.vendor_id, MAX(p2.vendor_name)::TEXT AS vendor_name, NULL::UUID AS bill_id, ''VENDOR-PAYMENT''::TEXT AS bill_number,\n'
        || E'               tanggal_bisnis(p_tenant_id) AS bill_date, tanggal_bisnis(p_tenant_id) AS due_date, ''unapplied_payment''::TEXT AS bill_status,\n'
        || E'               0::NUMERIC(18,2) AS bill_total, SUM(y.nilai)::NUMERIC(18,2) AS paid_amount, (-SUM(y.nilai))::NUMERIC(18,2) AS outstanding\n'
        || E'        FROM bayar_yatim y JOIN bill_payments_v2 p2 ON p2.id = y.payment_id\n'
        || E'        GROUP BY p2.vendor_id\n'
        || E'    )\n'
        || E'    SELECT * FROM bill_rows WHERE outstanding != 0\n    UNION ALL\n    SELECT * FROM vc_rows WHERE outstanding != 0\n'
        || E'    UNION ALL\n    SELECT * FROM ob_rows WHERE outstanding != 0\n'
        || E'    UNION ALL\n    SELECT * FROM yatim_rows WHERE outstanding != 0\n');
    IF v_baru = v_def THEN
        RAISE EXCEPTION 'V457: anchor SELECT akhir (bill_rows UNION ALL vc_rows) tak ditemukan';
    END IF;
    EXECUTE v_baru;
    IF pg_get_functiondef('compute_ap_outstanding(text)'::regprocedure) NOT LIKE '%OPENING-BALANCE%' THEN
        RAISE EXCEPTION 'V457: tambalan tak terpasang';
    END IF;
END $$;
