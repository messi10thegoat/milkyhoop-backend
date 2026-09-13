-- V249 (14 Sep 2026) — batalkan penerapan nota kredit (un-apply): riwayat atribusi APPEND-ONLY (status), bukan DELETE.
-- Unit B menerapkan CN tanpa jurnal (piutang sudah dikredit saat CN dibukukan), maka un-apply juga TANPA jurnal:
-- baris aplikasi berpindah 'active' -> 'reversed' dan original_invoice_id kembali NULL (compare-and-set di handler).
-- Prior art: customer_deposit_applications.status/reversed_at (FIX_P1_DEPOSIT).
-- Pemicu update_credit_note_status kini hanya menjumlah aplikasi AKTIF (dulu semua baris -> CN tetap 'applied'
-- dan void CN tetap terkunci sesudah un-apply).

DO $$
BEGIN
    IF (SELECT count(*) FROM credit_note_applications) <> 0 THEN
        RAISE EXCEPTION 'V249: credit_note_applications diharapkan 0 baris (pengukuran 14 Sep), ditemukan %',
            (SELECT count(*) FROM credit_note_applications);
    END IF;
END $$;

ALTER TABLE credit_note_applications
    ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active',
    ADD COLUMN reversed_at timestamptz,
    ADD COLUMN reversed_by uuid,
    ADD COLUMN reversal_reason text;

ALTER TABLE credit_note_applications
    ADD CONSTRAINT chk_cna_status CHECK (status IN ('active', 'reversed')),
    ADD CONSTRAINT chk_cna_reversed_lengkap CHECK (
        (status = 'active' AND reversed_at IS NULL AND reversed_by IS NULL AND reversal_reason IS NULL)
        OR (status = 'reversed' AND reversed_at IS NOT NULL AND reversed_by IS NOT NULL AND btrim(COALESCE(reversal_reason, '')) <> '')
    );

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

    -- V249: hanya aplikasi AKTIF (penerapan yang dibatalkan tetap tercatat sebagai riwayat, tak dihitung)
    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM credit_note_applications WHERE credit_note_id = v_cn_id AND status = 'active';

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
