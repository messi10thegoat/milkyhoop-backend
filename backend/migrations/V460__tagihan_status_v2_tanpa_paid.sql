-- V460 (23 Okt 2026, BACKEND; blok V460-V464; tiket LOW MASTER "BILL-2609-0005/0009 paid tanpa pembayaran"): status_v2 tagihan = SIKLUS
-- HIDUP saja (draft / posted / void). Status bayar diturunkan dari jurnal (compute_ap_rincian; tagihan_daftar.sql_keadaan) -- diukur 23 Okt:
-- NOL pembaca status_v2 = 'paid' (kode BE, fungsi DB, view); FE "Lunas" membaca medan API `status` turunan jurnal.
-- Celah yang ditutup: validate_bill_status_transition mengizinkan posted->paid TANPA syarat pembayaran, dan paid->(apa pun) DITOLAK ->
-- 2 tagihan subkontrak uji kaos (2 Sep, BILL-2609-0005/0009, amount_paid 0, nol alokasi) tak bisa di-void selamanya.
-- 1) penjaga: tepat 2 baris status_v2 'paid' di SELURUH DB, keduanya kaos, nol pembayaran; 2) transisi sementara paid->posted (hanya di
-- transaksi ini) lalu data 2 baris -> posted (status cache bayar ikut 'posted', amount_paid tetap 0); 3) transisi FINAL: posted -> void saja;
-- 4) CHECK status_v2 tanpa 'paid' (NOT VALID lalu VALIDATE). Nol jurnal disentuh.

DO $$
DECLARE n int; n_kaos int; n_bayar int;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE tenant_id = 'kaos-biru-konveksi' AND invoice_number IN ('BILL-2609-0005', 'BILL-2609-0009'))
      INTO n, n_kaos FROM bills WHERE status_v2 = 'paid';
    SELECT count(*) INTO n_bayar FROM bill_payment_allocations a JOIN bills b ON b.id = a.bill_id WHERE b.status_v2 = 'paid';
    IF n <> 2 OR n_kaos <> 2 OR n_bayar <> 0 THEN
        RAISE EXCEPTION 'V460: harap tepat 2 tagihan paid (kaos BILL-2609-0005/0009) tanpa pembayaran; ukur: paid=% kaos=% alokasi=%', n, n_kaos, n_bayar;
    END IF;
END $$;

-- 2. transisi sementara: paid -> posted (hanya untuk perbaikan data di transaksi ini)
CREATE OR REPLACE FUNCTION public.validate_bill_status_transition(p_current_status character varying, p_new_status character varying)
 RETURNS boolean
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
BEGIN
    IF p_current_status = p_new_status THEN
        RETURN true;
    END IF;
    RETURN CASE p_current_status
        WHEN 'draft' THEN p_new_status IN ('posted', 'void')
        WHEN 'posted' THEN p_new_status IN ('void')
        WHEN 'paid' THEN p_new_status = 'posted'   -- V460 sementara (dicabut di bawah, transaksi yang sama)
        WHEN 'void' THEN false
        ELSE false
    END;
END;
$function$;

DO $$
DECLARE n int;
BEGIN
    UPDATE bills SET status_v2 = 'posted', status = 'posted'
     WHERE tenant_id = 'kaos-biru-konveksi' AND invoice_number IN ('BILL-2609-0005', 'BILL-2609-0009') AND status_v2 = 'paid'
       AND COALESCE(amount_paid, 0) = 0;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n <> 2 THEN RAISE EXCEPTION 'V460: perbaikan data menyentuh % baris (harap 2)', n; END IF;
END $$;

-- 3. transisi FINAL: status_v2 = siklus hidup; status bayar dari jurnal
CREATE OR REPLACE FUNCTION public.validate_bill_status_transition(p_current_status character varying, p_new_status character varying)
 RETURNS boolean
 LANGUAGE plpgsql
 IMMUTABLE
AS $function$
BEGIN
    -- Valid transitions (V460, 23 Okt 2026: status_v2 = SIKLUS HIDUP saja; status bayar diturunkan dari jurnal / compute_ap_rincian):
    -- draft -> posted (when posting to accounting)
    -- draft -> void (cancel draft)
    -- posted -> void (void with reversal journal)
    -- void -> (no transitions allowed)
    -- 'paid' DICABUT (dulu posted -> paid tanpa syarat pembayaran; paid -> apa pun ditolak = tagihan tak bisa di-void).

    IF p_current_status = p_new_status THEN
        RETURN true;  -- No change
    END IF;

    RETURN CASE p_current_status
        WHEN 'draft' THEN p_new_status IN ('posted', 'void')
        WHEN 'posted' THEN p_new_status IN ('void')
        WHEN 'void' THEN false
        ELSE false
    END;
END;
$function$;

-- 4. CHECK tanpa 'paid'
ALTER TABLE bills DROP CONSTRAINT chk_bills_status_v2;
ALTER TABLE bills ADD CONSTRAINT chk_bills_status_v2 CHECK (status_v2::text = ANY (ARRAY['draft', 'posted', 'void'])) NOT VALID;
ALTER TABLE bills VALIDATE CONSTRAINT chk_bills_status_v2;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM bills WHERE status_v2 = 'paid') THEN RAISE EXCEPTION 'V460: masih ada status_v2 paid'; END IF;
    IF validate_bill_status_transition('posted', 'paid') OR validate_bill_status_transition('paid', 'posted') THEN
        RAISE EXCEPTION 'V460: transisi paid masih diizinkan';
    END IF;
    IF NOT validate_bill_status_transition('posted', 'void') OR NOT validate_bill_status_transition('draft', 'posted') THEN
        RAISE EXCEPTION 'V460: transisi sah rusak';
    END IF;
END $$;
