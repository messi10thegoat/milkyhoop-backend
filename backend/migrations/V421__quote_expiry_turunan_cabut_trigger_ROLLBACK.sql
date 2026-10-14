-- ROLLBACK V421: pasang kembali trg_quote_expiry persis seperti V306 (tanggal bisnis tenant).
CREATE OR REPLACE FUNCTION public.check_quote_expiry()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    -- Auto-expire sent quotes past expiry date
    IF NEW.expiry_date IS NOT NULL
       AND NEW.expiry_date < tanggal_bisnis(NEW.tenant_id)
       AND OLD.status = 'sent'
       AND NEW.status = 'sent' THEN
        NEW.status := 'expired';
    END IF;
    RETURN NEW;
END;
$function$
;
DROP TRIGGER IF EXISTS trg_quote_expiry ON quotes;
CREATE TRIGGER trg_quote_expiry BEFORE UPDATE ON public.quotes FOR EACH ROW EXECUTE FUNCTION check_quote_expiry();
