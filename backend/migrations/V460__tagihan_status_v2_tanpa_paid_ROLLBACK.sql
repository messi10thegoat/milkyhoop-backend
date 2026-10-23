-- ROLLBACK V460: kembalikan transisi + CHECK lama ('paid' diizinkan). DATA 2 tagihan kaos TIDAK dikembalikan ke 'paid' (itu justru data
-- rusak: paid tanpa pembayaran, tak bisa di-void) -- rollback hanya memulihkan aturan.
CREATE OR REPLACE FUNCTION public.validate_bill_status_transition(p_current_status character varying, p_new_status character varying)
 RETURNS boolean
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
BEGIN
    -- Valid transitions:
    -- draft -> posted (when posting to accounting)
    -- draft -> void (cancel draft)
    -- posted -> paid (full payment)
    -- posted -> void (void with reversal journal)
    -- void -> (no transitions allowed)
    -- paid -> (no transitions allowed)

    IF p_current_status = p_new_status THEN
        RETURN true;  -- No change
    END IF;

    RETURN CASE p_current_status
        WHEN 'draft' THEN p_new_status IN ('posted', 'void')
        WHEN 'posted' THEN p_new_status IN ('paid', 'void')
        WHEN 'paid' THEN false
        WHEN 'void' THEN false
        ELSE false
    END;
END;
$function$;
ALTER TABLE bills DROP CONSTRAINT chk_bills_status_v2;
ALTER TABLE bills ADD CONSTRAINT chk_bills_status_v2 CHECK (((status_v2)::text = ANY (ARRAY[('draft'::character varying)::text, ('posted'::character varying)::text, ('paid'::character varying)::text, ('void'::character varying)::text])));
