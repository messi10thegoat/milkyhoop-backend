-- ROLLBACK V249. Menolak bila ada riwayat 'reversed' (membuangnya = menghapus riwayat atribusi; putuskan dulu).
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM credit_note_applications WHERE status = 'reversed') THEN
        RAISE EXCEPTION 'ROLLBACK V249 ditolak: ada penerapan nota kredit berstatus reversed (riwayat akan hilang)';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.update_credit_note_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_total_amount BIGINT;
    v_new_status VARCHAR(20);
    v_cn_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_cn_id := OLD.credit_note_id;
    ELSE
        v_cn_id := NEW.credit_note_id;
    END IF;

    SELECT total_amount, status INTO v_total_amount, v_new_status
    FROM credit_notes WHERE id = v_cn_id;

    IF v_new_status IN ('draft', 'void') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM credit_note_applications WHERE credit_note_id = v_cn_id;

    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM credit_note_refunds WHERE credit_note_id = v_cn_id;

    IF (v_total_applied + v_total_refunded) >= v_total_amount THEN
        v_new_status := 'applied';
    ELSIF (v_total_applied + v_total_refunded) > 0 THEN
        v_new_status := 'partial';
    ELSE
        v_new_status := 'posted';
    END IF;

    UPDATE credit_notes
    SET amount_applied = v_total_applied,
        amount_refunded = v_total_refunded,
        status = v_new_status,
        updated_at = NOW()
    WHERE id = v_cn_id;

    RETURN COALESCE(NEW, OLD);
END;
$function$;

ALTER TABLE credit_note_applications
    DROP CONSTRAINT chk_cna_reversed_lengkap,
    DROP CONSTRAINT chk_cna_status,
    DROP COLUMN reversal_reason,
    DROP COLUMN reversed_by,
    DROP COLUMN reversed_at,
    DROP COLUMN status;
DELETE FROM schema_migrations WHERE version = 'V249__credit_note_applications_status.sql';
COMMIT;
