-- V443 (22 Okt 2026, PEMBELIAN-SERAGAM unit (3a), BACKEND) — KEMBAR V249 + V250 (Nota Kredit) untuk NOTA DEBIT.
-- Putusan pemilik 22 Okt: terapkan nota debit = ATRIBUSI tanpa jurnal, seluruh nilai ke SATU tagihan; atribusi hutang dibaca
-- compute_ap_outstanding cabang 4 (V441) lewat vendor_credits.original_bill_id.
-- (1) Riwayat penerapan APPEND-ONLY: vendor_credit_applications.status 'active' -> 'reversed' (lepas = tanpa jurnal), bukan DELETE.
--     Pemicu update_vendor_credit_status kini hanya menjumlah aplikasi AKTIF.
-- (2) Pagar DB original_bill_id (Law 19; invariant ditegakkan di DB): sesudah nota debit keluar dari draf, kaitan hanya boleh berubah
--     BERPASANGAN dengan riwayat penerapan di transaksi yang sama:
--       NULL -> X : wajib ada aplikasi AKTIF (vc, X)
--       X -> NULL : wajib TAK ada aplikasi aktif (vc, X) DAN ada aplikasi (vc, X) yang dilepas DI TRANSAKSI INI (reversed_at = now())
--       X -> Y    : selalu ditolak (tunjuk-ulang = lepas lalu terapkan)
--     Draf bebas (create/PATCH draf divalidasi handler).
-- Data: vendor_credit_applications = 0 baris di SEMUA tenant (diukur 22 Okt) -- ditegaskan di bawah.

DO $$
BEGIN
    IF (SELECT count(*) FROM vendor_credit_applications) <> 0 THEN
        RAISE EXCEPTION 'V443: vendor_credit_applications diharapkan 0 baris (pengukuran 22 Okt), ditemukan %',
            (SELECT count(*) FROM vendor_credit_applications);
    END IF;
END $$;

ALTER TABLE vendor_credit_applications
    ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active',
    ADD COLUMN reversed_at timestamptz,
    ADD COLUMN reversed_by uuid,
    ADD COLUMN reversal_reason text;

ALTER TABLE vendor_credit_applications
    ADD CONSTRAINT chk_vca_status CHECK (status IN ('active', 'reversed')),
    ADD CONSTRAINT chk_vca_reversed_lengkap CHECK (
        (status = 'active' AND reversed_at IS NULL AND reversed_by IS NULL AND reversal_reason IS NULL)
        OR (status = 'reversed' AND reversed_at IS NOT NULL AND reversed_by IS NOT NULL AND btrim(COALESCE(reversal_reason, '')) <> '')
    );

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

    -- V443: hanya aplikasi AKTIF (penerapan yang dilepas tetap tercatat sebagai riwayat, tak dihitung)
    SELECT COALESCE(SUM(amount_applied), 0) INTO v_total_applied
    FROM vendor_credit_applications WHERE vendor_credit_id = v_vc_id AND status = 'active';

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

CREATE OR REPLACE FUNCTION vendor_credit_kaitan_tagihan_beku()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'draft' THEN
        RETURN NEW;
    END IF;

    IF OLD.original_bill_id IS NOT NULL AND NEW.original_bill_id IS NOT NULL THEN
        RAISE EXCEPTION 'Law 19: kaitan tagihan nota debit % sudah terisi (%) dan tak boleh ditunjuk-ulang ke %; lepas penerapan dulu.',
            OLD.credit_number, OLD.original_bill_id, NEW.original_bill_id
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.original_bill_id IS NULL THEN
        IF NOT EXISTS (SELECT 1 FROM vendor_credit_applications a
                       WHERE a.vendor_credit_id = NEW.id AND a.bill_id = NEW.original_bill_id
                         AND a.tenant_id = NEW.tenant_id AND a.status = 'active') THEN
            RAISE EXCEPTION 'Law 19: kaitan tagihan nota debit % hanya boleh diisi lewat penerapan (aplikasi aktif ke tagihan %).',
                OLD.credit_number, NEW.original_bill_id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    -- X -> NULL
    IF EXISTS (SELECT 1 FROM vendor_credit_applications a
               WHERE a.vendor_credit_id = OLD.id AND a.bill_id = OLD.original_bill_id AND a.status = 'active')
       OR NOT EXISTS (SELECT 1 FROM vendor_credit_applications a
                      WHERE a.vendor_credit_id = OLD.id AND a.bill_id = OLD.original_bill_id
                        AND a.status = 'reversed' AND a.reversed_at = now()) THEN
        RAISE EXCEPTION 'Law 19: kaitan tagihan nota debit % hanya boleh dilepas lewat pelepasan penerapan di transaksi yang sama.',
            OLD.credit_number
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_vendor_credit_kaitan_tagihan_beku
    BEFORE UPDATE OF original_bill_id ON vendor_credits
    FOR EACH ROW WHEN (OLD.original_bill_id IS DISTINCT FROM NEW.original_bill_id)
    EXECUTE FUNCTION vendor_credit_kaitan_tagihan_beku();
