\pset pager off
\pset tuples_only on
BEGIN READ ONLY;
SELECT 'kaos.flag_v2=' || coalesce((SELECT enabled::text FROM tenant_features WHERE tenant_id='kaos-biru-konveksi' AND feature='milkybook_v2'), 'tidak-ada');
SELECT 'grapgrap.flag_v2=' || coalesce((SELECT enabled::text FROM tenant_features WHERE tenant_id='grapgrap-manado' AND feature='milkybook_v2'), 'tidak-ada');
SELECT 'kaos.saldo_kas_bank=' || coalesce(string_agg(ba.account_name || ':' || (SELECT coalesce(sum(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id WHERE jl.account_id = ba.coa_id AND je.status = 'POSTED' AND je.tenant_id = ba.tenant_id)::text, ' ' ORDER BY ba.account_name), '-') FROM bank_accounts ba WHERE ba.tenant_id = 'kaos-biru-konveksi';
SELECT 'kaos.beban_bersih=' || coalesce(sum(jl.debit - jl.credit), 0) FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_id JOIN chart_of_accounts c ON c.id = jl.account_id WHERE je.tenant_id = 'kaos-biru-konveksi' AND je.status = 'POSTED' AND c.account_type IN ('EXPENSE', 'OTHER_EXPENSE');
SELECT 'kaos.expenses_bukan_void=' || count(*) FROM expenses WHERE tenant_id = 'kaos-biru-konveksi' AND status <> 'void';
SELECT 'kaos.quotes=' || count(*) FROM quotes WHERE tenant_id = 'kaos-biru-konveksi';
SELECT 'kaos.pelanggan_aktif=' || count(*) FROM customers WHERE tenant_id = 'kaos-biru-konveksi' AND is_active AND deleted_at IS NULL;
SELECT 'kaos.percakapan_v2=' || count(*) FROM milkybook_v2_percakapan WHERE tenant_id = 'kaos-biru-konveksi';
SELECT 'kaos.tafsir_log=' || count(*) || ' biaya_usd=' || coalesce(sum(biaya_usd), 0) FROM milkybook_v2_tafsir_log WHERE tenant_id = 'kaos-biru-konveksi';
SELECT 'grapgrap.md5=' || md5((SELECT count(*) FROM journal_entries WHERE tenant_id='grapgrap-manado')::text || '|' || (SELECT count(*) FROM expenses WHERE tenant_id='grapgrap-manado')::text || '|' || (SELECT count(*) FROM quotes WHERE tenant_id='grapgrap-manado')::text || '|' || (SELECT count(*) FROM customers WHERE tenant_id='grapgrap-manado')::text || '|' || (SELECT count(*) FROM milkybook_v2_percakapan WHERE tenant_id='grapgrap-manado')::text || '|' || (SELECT string_agg(feature, ',' ORDER BY feature) FROM tenant_features WHERE tenant_id='grapgrap-manado' AND enabled));
ROLLBACK;
