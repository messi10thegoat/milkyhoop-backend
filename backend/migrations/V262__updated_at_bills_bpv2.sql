-- V262: trigger updated_at utk bills + bill_payments_v2 (isi celah If-Match; 6 tabel realtime lain
-- sudah punya `update_<tbl>_updated_at`). Pola SAMA (BEFORE UPDATE, NEW.updated_at=NOW()).
-- updated_at BUKAN kolom beku Law 19: trg_law19_bekukan_nominal (WHEN old.journal_id IS NOT NULL)
-- hanya membandingkan kolom finansial di TG_ARGV → tak berinteraksi, tak jadi lebih longgar.
-- Nama trg_<tbl>_updated_at (alfabetis < trg_law19_...) → jalan sebelum freeze; freeze abaikan updated_at.
CREATE OR REPLACE FUNCTION public.update_bills_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_bills_updated_at ON bills;
CREATE TRIGGER trg_bills_updated_at BEFORE UPDATE ON bills
    FOR EACH ROW EXECUTE FUNCTION update_bills_updated_at();

CREATE OR REPLACE FUNCTION public.update_bill_payments_v2_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_bill_payments_v2_updated_at ON bill_payments_v2;
CREATE TRIGGER trg_bill_payments_v2_updated_at BEFORE UPDATE ON bill_payments_v2
    FOR EACH ROW EXECUTE FUNCTION update_bill_payments_v2_updated_at();
