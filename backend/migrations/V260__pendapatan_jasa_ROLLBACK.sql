-- ROLLBACK V260: cabut map + akun 4-10150 (hanya kalau tak dipakai jurnal). seed function tak di-rollback (idempoten aman).
DELETE FROM account_roles WHERE role_key='REVENUE_SALES_SERVICE';
DELETE FROM chart_of_accounts WHERE account_code='4-10150' AND NOT EXISTS (
  SELECT 1 FROM journal_lines jl WHERE jl.account_id = chart_of_accounts.id);
