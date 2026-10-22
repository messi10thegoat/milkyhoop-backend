-- ROLLBACK V443 (kembar ROLLBACK V249+V250). Menolak bila ada riwayat 'reversed' (membuangnya = menghapus riwayat atribusi).
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM vendor_credit_applications WHERE status = 'reversed') THEN
        RAISE EXCEPTION 'ROLLBACK V443 ditolak: ada penerapan nota debit berstatus reversed (riwayat akan hilang)';
    END IF;
END $$;

DROP TRIGGER trg_vendor_credit_kaitan_tagihan_beku ON vendor_credits;
DROP FUNCTION vendor_credit_kaitan_tagihan_beku();

CREATE OR REPLACE FUNCTION public.update_vendor_credit_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_total_applied BIGINT;
    v_total_received BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_vc_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_vc_id := OLD.vendor_credit_id;
    ELSE
        v_vc_id := NEW.vendor_credit_id;
    END IF;

    SELECT total_amount, status INTO v_total_amount, v_new_status
    FROM vendor_credits WHERE id = v_vc_id;

    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM vendor_credit_applications WHERE vendor_credit_id = v_vc_id;

    SELECT COALESCE(SUM(amount), 0) INTO v_total_received
    FROM vendor_credit_refunds WHERE vendor_credit_id = v_vc_id;

    IF (v_total_applied + v_total_received) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_received) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    UPDATE vendor_credits
    SET amount_applied = v_total_applied,
        amount_received = v_total_received,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_vc_id;

    RETURN COALESCE(NEW, OLD);
END;
$function$;

ALTER TABLE vendor_credit_applications
    DROP CONSTRAINT chk_vca_reversed_lengkap,
    DROP CONSTRAINT chk_vca_status,
    DROP COLUMN reversal_reason,
    DROP COLUMN reversed_by,
    DROP COLUMN reversed_at,
    DROP COLUMN status;
DELETE FROM schema_migrations WHERE version = 'V443__nota_debit_aplikasi_status_dan_pagar.sql';
COMMIT;
