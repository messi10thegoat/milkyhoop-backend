\set tid 'konveksi-cemerlang'
\echo ========== INV-1: TRIAL BALANCE (semua POSTED, Dr=Cr) ==========
SELECT SUM(jl.debit) AS total_debit, SUM(jl.credit) AS total_credit,
       SUM(jl.debit)-SUM(jl.credit) AS selisih
  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id
 WHERE je.tenant_id=:'tid' AND je.status='POSTED';

\echo ========== INV-2: tiap jurnal balanced (0 baris tak seimbang) ==========
SELECT je.journal_number, je.total_debit, je.total_credit
  FROM journal_entries je WHERE je.tenant_id=:'tid' AND je.status='POSTED'
   AND je.total_debit <> je.total_credit;

\echo ========== INV-3: orphan journal_lines (0) ==========
SELECT jl.id FROM journal_lines jl LEFT JOIN journal_entries je ON je.id=jl.journal_id WHERE je.id IS NULL LIMIT 5;

\echo ========== LAW-4: 2-10300 Hutang Pajak = 0 ==========
SELECT COALESCE(SUM(jl.debit-jl.credit),0) AS net_2_10300
  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id
 WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code='2-10300';

\echo ========== INV-5: WIP 1-10650 net = 0 (costing identity) ==========
SELECT COALESCE(SUM(jl.debit-jl.credit),0) AS wip_net
  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id
 WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code='1-10650';

\echo ========== INV-6: Deferred Revenue 2-10750 net = 0 ==========
SELECT COALESCE(SUM(jl.debit-jl.credit),0) AS deferred_net
  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id
 WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code='2-10750';

\echo ========== INV-7: applied labor/OH (2-10430/2-10440) = 0 setelah reconcile ==========
SELECT c.account_code, COALESCE(SUM(jl.debit-jl.credit),0) AS net
  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id
 WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code IN ('2-10430','2-10440') GROUP BY 1 ORDER BY 1;

\echo ========== INV-8: AR (1-10400 net) == compute_ar_outstanding ==========
SELECT (SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code='1-10400') AS ar_gl,
       (SELECT COALESCE((SELECT SUM(outstanding) FROM compute_ar_outstanding(:'tid')),0)) AS ar_helper;

\echo ========== INV-9: AP (2-10100 net) == compute_ap_outstanding ==========
SELECT (SELECT COALESCE(SUM(jl.credit-jl.debit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code='2-10100') AS ap_gl,
       (SELECT COALESCE((SELECT SUM(outstanding) FROM compute_ap_outstanding(:'tid')),0)) AS ap_helper;

\echo ========== INV-10: chain integrity (semua is_valid=true) ==========
SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE is_valid) AS valid,
       COUNT(*) FILTER (WHERE NOT is_valid) AS invalid
  FROM verify_chain_integrity(:'tid');

\echo ========== is_effective_journal konsisten (reversed pairs) ==========
SELECT COUNT(*) AS posted_effective
  FROM journal_entries je WHERE je.tenant_id=:'tid' AND is_effective_journal(je.id);

\echo ========== INV-11: bank == GL (BCA + Kas Kecil) ==========
SELECT c.account_code, LEFT(c.name,20) AS akun,
       COALESCE(SUM(jl.debit-jl.credit),0) AS gl_saldo
  FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id
 WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code IN ('1-10201','1-10202') GROUP BY 1,2 ORDER BY 1;

\echo ========== INV-12: inventory GL (1-10600) vs Σ ledger balance ==========
SELECT (SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries je ON je.id=jl.journal_id JOIN chart_of_accounts c ON c.id=jl.account_id WHERE je.tenant_id=:'tid' AND je.status='POSTED' AND c.account_code='1-10600') AS inv_gl,
       (SELECT COALESCE(SUM(quantity_balance*average_cost),0) FROM (SELECT DISTINCT ON (product_id) quantity_balance, average_cost FROM inventory_ledger WHERE tenant_id=:'tid' ORDER BY product_id, movement_date DESC, created_at DESC) x) AS inv_ledger_est;
