-- V437 (22 Okt 2026, P-U3b) — LEPAS penerapan uang muka pembelian (unapply): riwayat APPEND-ONLY (status), bukan DELETE.
-- Kembar V249 (credit_note_applications) dan customer_deposit_applications (FIX_P1_DEPOSIT). Berbeda dari V249: penerapan UM vendor
-- BERJURNAL (Dr Utang Usaha / Cr Uang Muka Pembelian), maka lepas = jurnal PEMBALIK; reversed_by_id = jurnal pembalik itu (Law 26).
-- Pemicu update_vendor_deposit_status kini:
--   * NUMERIC (dulu BIGINT -> sen terpotong, Law 9),
--   * hanya penerapan AKTIF (penerapan yang dilepas tetap tercatat sebagai riwayat),
--   * status kembali 'posted' bila terpakai + dikembalikan = 0 (dulu `ELSE status` -> tetap 'partial' selamanya),
--   * deposit draft/void tidak disentuh statusnya.
-- Kolom cache applied_amount/remaining_amount tetap cache tulis-saja; angka layar = jurnal (uang_muka_vendor_daftar, Law 1/16).
-- Expand-contract: tambah saja (kolom ber-DEFAULT); aman diterapkan sebelum kode. Diukur 22 Okt: 0 baris applications.

ALTER TABLE vendor_deposit_applications
    ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active',
    ADD COLUMN reversed_by_id uuid REFERENCES journal_entries(id),
    ADD COLUMN reversed_at timestamptz,
    ADD COLUMN reversed_by uuid;

ALTER TABLE vendor_deposit_applications
    ADD CONSTRAINT chk_vda_status CHECK (status IN ('active', 'reversed')),
    ADD CONSTRAINT chk_vda_reversed_lengkap CHECK (
        (status = 'active' AND reversed_by_id IS NULL AND reversed_at IS NULL)
        OR (status = 'reversed' AND reversed_by_id IS NOT NULL AND reversed_at IS NOT NULL)
    );

CREATE OR REPLACE FUNCTION public.update_vendor_deposit_status()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_total_applied NUMERIC(18,2);
    v_total_refunded NUMERIC(18,2);
    v_deposit RECORD;
BEGIN
    SELECT * INTO v_deposit FROM vendor_deposits WHERE id = COALESCE(NEW.vendor_deposit_id, OLD.vendor_deposit_id);

    IF v_deposit.id IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    -- V437: hanya penerapan AKTIF
    SELECT COALESCE(SUM(amount), 0) INTO v_total_applied
    FROM vendor_deposit_applications WHERE vendor_deposit_id = v_deposit.id AND status = 'active';

    SELECT COALESCE(SUM(amount), 0) INTO v_total_refunded
    FROM vendor_deposit_refunds WHERE vendor_deposit_id = v_deposit.id;

    UPDATE vendor_deposits SET
        applied_amount = v_total_applied + v_total_refunded,
        status = CASE
            WHEN v_deposit.status IN ('draft', 'void') THEN v_deposit.status
            WHEN v_total_applied + v_total_refunded >= amount THEN 'applied'
            WHEN v_total_applied + v_total_refunded > 0 THEN 'partial'
            ELSE 'posted'
        END,
        updated_at = NOW()
    WHERE id = v_deposit.id;

    RETURN COALESCE(NEW, OLD);
END;
$function$;
