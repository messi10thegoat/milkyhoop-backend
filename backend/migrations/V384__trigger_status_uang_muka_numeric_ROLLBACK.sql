-- Rollback V384: kembalikan definisi lama (BIGINT) persis.
CREATE OR REPLACE FUNCTION public.update_customer_deposit_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_deposit_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_deposit_id := OLD.deposit_id;
    ELSE
        v_deposit_id := NEW.deposit_id;
    END IF;

    SELECT amount, status INTO v_total_amount, v_new_status
    FROM customer_deposits WHERE id = v_deposit_id;

    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- FIX_P1_DEPOSIT 2026-06-16: exclude reversed applications from cache.
    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM customer_deposit_applications
    WHERE deposit_id = v_deposit_id
      AND COALESCE(status, 'active') <> 'reversed';

    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM customer_deposit_refunds WHERE deposit_id = v_deposit_id;

    IF (v_total_applied + v_total_refunded) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_refunded) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    UPDATE customer_deposits
    SET amount_applied = v_total_applied,
        amount_refunded = v_total_refunded,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_deposit_id;

    RETURN COALESCE(NEW, OLD);
END;
$function$

;
