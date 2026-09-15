-- ROLLBACK V262: cabut trigger + fungsi updated_at bills + bill_payments_v2.
DROP TRIGGER IF EXISTS trg_bills_updated_at ON bills;
DROP TRIGGER IF EXISTS trg_bill_payments_v2_updated_at ON bill_payments_v2;
DROP FUNCTION IF EXISTS public.update_bills_updated_at();
DROP FUNCTION IF EXISTS public.update_bill_payments_v2_updated_at();
