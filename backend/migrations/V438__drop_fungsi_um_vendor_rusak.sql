-- V438 (22 Okt 2026, P-U3b) — DROP lima fungsi DB uang muka vendor yang RUSAK sejak V054 (contract; sesudah kode P-U3 + P-U3b LIVE).
-- Diukur 22 Okt (baca-saja): get_vendor_deposit_applications -> kolom hantu b.bill_number; get_vendor_deposit_refunds,
-- get_vendor_deposits, get_available_vendor_deposits -> "structure of query does not match" (BIGINT vs numeric(18,2));
-- get_vendor_deposit_summary -> berjalan tetapi atas kolom cache BIGINT (bukan jurnal, Law 1/16).
-- Nol pemanggil: grep kode backend, pg_proc.prosrc, pg_views. Pengganti: services/uang_muka_vendor_daftar (SQL_APLIKASI, SQL_REFUND,
-- fakta_vd, sql_agregat) -- tenant eksplisit, angka dari jurnal. ROLLBACK = definisi produksi persis (pg_get_functiondef).
DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM pg_proc p
    WHERE p.prosrc ~ 'get_vendor_deposit|get_available_vendor_deposits'
      AND p.proname NOT IN ('get_vendor_deposit_applications', 'get_vendor_deposit_refunds', 'get_vendor_deposit_summary',
                            'get_vendor_deposits', 'get_available_vendor_deposits');
    IF n <> 0 THEN
        RAISE EXCEPTION 'V438: % fungsi lain merujuk fungsi yang akan di-DROP', n;
    END IF;
END $$;

DROP FUNCTION IF EXISTS public.get_vendor_deposit_applications(uuid);
DROP FUNCTION IF EXISTS public.get_vendor_deposit_refunds(uuid);
DROP FUNCTION IF EXISTS public.get_vendor_deposits(uuid, character varying);
DROP FUNCTION IF EXISTS public.get_available_vendor_deposits(uuid);
DROP FUNCTION IF EXISTS public.get_vendor_deposit_summary(text);
