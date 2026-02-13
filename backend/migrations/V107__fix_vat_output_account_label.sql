-- V107: Create dedicated PPN Keluaran (VAT Output) account
-- Previously 2-10400 was incorrectly used for VAT in config.py
-- Now VAT gets its own dedicated account at 2-10600

INSERT INTO chart_of_accounts (tenant_id, account_code, name, account_type, normal_balance, parent_code, level, is_header)
SELECT tenant_id, '2-10600', 'PPN Keluaran', 'LIABILITY', 'CREDIT', '2-10000', 2, false
FROM chart_of_accounts
WHERE account_code = '2-10000'
ON CONFLICT DO NOTHING;
