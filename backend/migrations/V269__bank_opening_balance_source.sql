-- V269: register source_type BANK_OPENING_BALANCE (Law 6 FK). Per-account bank/cash
-- opening balance uses this instead of OPENING so guard_opening_balance (which only
-- fires on OPENING/OPENING_BALANCE) does NOT block adding a new cash account after
-- the tenant has operational transactions. Guard itself UNCHANGED.
INSERT INTO journal_source_types (source_type, description) VALUES
  ('BANK_OPENING_BALANCE', 'Saldo awal per-akun bank/kas (ditambah kapan saja; bukan opening global)')
ON CONFLICT (source_type) DO NOTHING;
