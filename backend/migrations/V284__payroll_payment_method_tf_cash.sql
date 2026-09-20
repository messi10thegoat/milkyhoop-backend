-- V284: per-employee payroll payment method (TRANSFER / CASH) + split salary settlement.
-- The salary payment settled the whole net from ONE bank account. Owners pay some staff by
-- bank transfer and others in cash from a Kas account, and reconcile Kas against physical cash.
-- Split the 'salary' payment's credit leg by per-employee method: employees.payment_method
-- (owner override) defaults from bank presence (a bank_account_number on file -> transfer,
-- else cash). The payment row SNAPSHOTS transfer_amount + cash_amount at create, so a later
-- method change never retro-touches a posted payment. The cash "account" is an ordinary
-- bank_accounts row whose CoA is Kas (same shape as the transfer account) -- no CASH role,
-- matching how the owner already models Kas Kecil / Kas Operasional / Kas Manado.
ALTER TABLE employees ADD COLUMN IF NOT EXISTS payment_method text
    CHECK (payment_method IS NULL OR payment_method IN ('transfer','cash'));
ALTER TABLE payroll_payments ADD COLUMN IF NOT EXISTS cash_account_id uuid;
ALTER TABLE payroll_payments ADD COLUMN IF NOT EXISTS transfer_amount numeric(18,2);
ALTER TABLE payroll_payments ADD COLUMN IF NOT EXISTS cash_amount numeric(18,2);
