-- Pembalik V483 (BUG-098): kembalikan generate_receive_payment_number ke definisi SEBELUM V483 (RCV-YYYY-NNNN per tahun; disalin dari
-- pg_get_functiondef produksi 7 Nov 2026) + buang kolom year_month. Nomor RCV-YYMM yang sudah terbit TETAP (nol data dokumen diubah).
-- Penghitung (tabel operasional, bukan angka uang) DIPULIHKAN ke nomor RCV-<tahun> tertinggi yang ada: tanpa itu penghitung tahunan
-- melanjutkan angka BULANAN (mis. 5) dan nomor RCV-2026-0006 berikutnya bentrok uq_rcv_payments_tenant_number (500).
-- Cermin rumus Python WAJIB dibalik lebih dulu.

CREATE OR REPLACE FUNCTION public.generate_receive_payment_number(p_tenant_id text)
 RETURNS character varying
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_year INT;
    v_next_number INT;
    v_payment_number VARCHAR(50);
BEGIN
    v_year := EXTRACT(YEAR FROM tanggal_bisnis(p_tenant_id));

    INSERT INTO receive_payment_sequences (tenant_id, last_number, year)
    VALUES (p_tenant_id, 1, v_year)
    ON CONFLICT (tenant_id)
    DO UPDATE SET
        last_number = CASE
            WHEN receive_payment_sequences.year = v_year
            THEN receive_payment_sequences.last_number + 1
            ELSE 1
        END,
        year = v_year
    RETURNING last_number INTO v_next_number;

    -- Format: RCV-YYYY-NNNN
    v_payment_number := 'RCV-' || v_year || '-' || LPAD(v_next_number::TEXT, 4, '0');

    RETURN v_payment_number;
END;
$function$;

UPDATE receive_payment_sequences s
SET last_number = COALESCE((
    SELECT MAX(substring(r.payment_number FROM '^RCV-[0-9]{4}-([0-9]+)$')::int)
    FROM receive_payments r
    WHERE r.tenant_id = s.tenant_id AND r.payment_number ~ ('^RCV-' || s.year || '-[0-9]+$')), 0);

ALTER TABLE receive_payment_sequences DROP COLUMN IF EXISTS year_month;
