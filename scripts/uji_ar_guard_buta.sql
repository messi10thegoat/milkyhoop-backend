-- Buktikan lewat EKSEKUSI: verify_ar_reconciliation_all buta terhadap GL piutang tak teratribusi?
-- ROLLBACK; hasil ditangkap \gset sebelum rollback savepoint; nol menetap.
\set ON_ERROR_STOP 1
\set T 'grapgrap-manado'
BEGIN;

CREATE OR REPLACE FUNCTION pg_temp.jurnal_ar(p_source text, p_source_id uuid, p_amount numeric, p_kredit_ar boolean) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE jid uuid := gen_random_uuid(); ar uuid; lawan uuid;
BEGIN
    SELECT id INTO ar FROM chart_of_accounts WHERE tenant_id='grapgrap-manado' AND account_type='RECEIVABLE'
      AND COALESCE(is_header,false)=false ORDER BY account_code LIMIT 1;
    SELECT id INTO lawan FROM chart_of_accounts WHERE tenant_id='grapgrap-manado' AND account_type='REVENUE'
      AND COALESCE(is_header,false)=false ORDER BY account_code LIMIT 1;
    INSERT INTO journal_entries (id, tenant_id, journal_number, journal_date, description, source_type, source_id,
        status, total_debit, total_credit)
    VALUES (jid, 'grapgrap-manado', 'UJI-AR-' || substr(jid::text,1,8), CURRENT_DATE, 'uji guard AR', p_source,
        COALESCE(p_source_id, jid), 'DRAFT', p_amount, p_amount);
    IF p_kredit_ar THEN
        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES
          (gen_random_uuid(), jid, 1, lawan, p_amount, 0, 'uji'), (gen_random_uuid(), jid, 2, ar, 0, p_amount, 'uji');
    ELSE
        INSERT INTO journal_lines (id, journal_id, line_number, account_id, debit, credit, memo) VALUES
          (gen_random_uuid(), jid, 1, ar, p_amount, 0, 'uji'), (gen_random_uuid(), jid, 2, lawan, 0, p_amount, 'uji');
    END IF;
    UPDATE journal_entries SET status = 'POSTED' WHERE id = jid;
END; $$;

CREATE OR REPLACE FUNCTION pg_temp.gl_ar_efektif(p text) RETURNS numeric LANGUAGE sql AS $$
  SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
  JOIN chart_of_accounts coa ON coa.id=jl.account_id
  WHERE coa.account_type='RECEIVABLE' AND is_effective_journal(je.id) AND je.tenant_id=p $$;

\echo '== awal'
SELECT pg_temp.gl_ar_efektif(:'T') AS gl0 \gset
SELECT verdict AS v0, total_gl AS tg0 FROM verify_ar_reconciliation_all() WHERE tenant_id = :'T' \gset
\echo 'GL efektif sebenarnya:' :gl0 ' | guard total_gl:' :tg0 ' verdict:' :v0

\echo '== UJI: jurnal MANUAL mengkredit piutang 777 (tak teratribusi)'
SAVEPOINT uji;
SELECT pg_temp.jurnal_ar('MANUAL', NULL, 777, true);
SELECT pg_temp.gl_ar_efektif(:'T') AS gl1 \gset
SELECT verdict AS v1, total_gl AS tg1 FROM verify_ar_reconciliation_all() WHERE tenant_id = :'T' \gset
ROLLBACK TO SAVEPOINT uji;
\echo 'GL efektif sebenarnya:' :gl1 ' | guard total_gl:' :tg1 ' verdict:' :v1

\echo '== KONTROL: jurnal INVOICE ke faktur nyata (teratribusi) mendebit piutang 777'
SAVEPOINT kontrol;
SELECT id AS inv FROM sales_invoices WHERE tenant_id = :'T' AND journal_id IS NOT NULL ORDER BY id LIMIT 1 \gset
SELECT pg_temp.jurnal_ar('INVOICE', :'inv'::uuid, 777, false);
SELECT pg_temp.gl_ar_efektif(:'T') AS gl2 \gset
SELECT verdict AS v2, total_gl AS tg2 FROM verify_ar_reconciliation_all() WHERE tenant_id = :'T' \gset
ROLLBACK TO SAVEPOINT kontrol;
\echo 'GL efektif sebenarnya:' :gl2 ' | guard total_gl:' :tg2 ' verdict:' :v2
\echo '   ^ BUKAN kontrol pembeda: INVOICE menaikkan GL DAN compute_ar_outstanding bersama -> PASS memang benar'

\echo '== KONTROL PEMBEDA: jurnal RECEIVE_PAYMENT bersumber penerimaan nyata mengkredit piutang 777'
\echo '   (guard mengatribusi via source_id; compute_ar_outstanding hanya via receive_payments.journal_id -> harus DRIFT)'
SAVEPOINT kontrol2;
SELECT id AS rp FROM receive_payments WHERE tenant_id = :'T' ORDER BY id LIMIT 1 \gset
SELECT pg_temp.jurnal_ar('RECEIVE_PAYMENT', :'rp'::uuid, 777, true);
SELECT pg_temp.gl_ar_efektif(:'T') AS gl3 \gset
SELECT verdict AS v3, total_gl AS tg3, total_drift AS td3 FROM verify_ar_reconciliation_all() WHERE tenant_id = :'T' \gset
ROLLBACK TO SAVEPOINT kontrol2;
\echo 'GL efektif sebenarnya:' :gl3 ' | guard total_gl:' :tg3 ' drift:' :td3 ' verdict:' :v3

ROLLBACK;
SELECT count(*) AS jurnal_uji_menetap FROM journal_entries WHERE journal_number LIKE 'UJI-AR-%';
