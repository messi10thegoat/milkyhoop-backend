-- V301 (#29, 24 Sep 2026): receive_payments.payment_method menerima e_wallet.
-- Metode kini DITURUNKAN dari jenis akun Kas/Bank (bank_accounts.account_type):
--   cash/petty_cash -> cash, bank/credit_card -> bank_transfer, e_wallet -> e_wallet.
-- Metode = LABEL saja (kwitansi/PDF/filter); jurnal memakai akun, bukan metode.
-- Aditif: hanya MELEBARKAN himpunan nilai sah; semua baris lama (cash/bank_transfer) tetap sah.
ALTER TABLE receive_payments DROP CONSTRAINT IF EXISTS chk_rcv_payment_method;
ALTER TABLE receive_payments ADD CONSTRAINT chk_rcv_payment_method
  CHECK (payment_method::text = ANY (ARRAY['cash', 'bank_transfer', 'e_wallet']));
