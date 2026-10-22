-- ROLLBACK V437. Menolak bila ada penerapan berstatus reversed (membuangnya = menghapus riwayat lepas + memutus tautan jurnal pembalik).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM vendor_deposit_applications WHERE status <> 'active') THEN
        RAISE EXCEPTION 'ROLLBACK V437: ada penerapan berstatus reversed; putuskan dulu nasib riwayatnya';
    END IF;
END $$;
-- fungsi pemicu persis seperti sebelum V437 (pg_get_functiondef produksi 22 Okt)
CREATE OR REPLACE FUNCTION public.update_vendor_deposit_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_total_applied BIGINT;
    v_total_refunded BIGINT;
    v_deposit RECORD;
BEGIN
    -- Get deposit
    SELECT * INTO v_deposit FROM vendor_deposits WHERE id = COALESCE(NEW.vendor_deposit_id, OLD.vendor_deposit_id);

    IF v_deposit.id IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- Calculate totals
    SELECT COALESCE(SUM(amount), 0) INTO v_total_applied
    FROM vendor_deposit_applications WHERE vendor_deposit_id = v_deposit.id;

    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM vendor_deposit_refunds WHERE vendor_deposit_id = v_deposit.id;

    -- Update applied amount
    UPDATE vendor_deposits SET
        applied_amount = v_total_applied + v_total_refunded,
        status = CASE
            WHEN v_total_applied + v_total_refunded >= amount THEN 'applied'
            WHEN v_total_applied + v_total_refunded > 0 THEN 'partial'
            ELSE status
        END,
        updated_at = NOW()
    WHERE id = v_deposit.id;

    RETURN COALESCE(NEW, OLD);
END;
$function$
;

ALTER TABLE vendor_deposit_applications
    DROP CONSTRAINT chk_vda_reversed_lengkap,
    DROP CONSTRAINT chk_vda_status,
    DROP COLUMN reversed_by,
    DROP COLUMN reversed_at,
    DROP COLUMN reversed_by_id,
    DROP COLUMN status;
